"""Persistent accounting and atomic reservations for paid AI requests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select, update

from backend.models import AIUsage


class AILimitError(ValueError):
    pass


def _since(period: str) -> datetime:
    now = datetime.now(timezone.utc)
    if period == "day":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "month":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return now - timedelta(days=3650)


def priced(rate) -> bool:
    return rate[0] > 0 and rate[1] > 0


def cost(input_tokens: int, output_tokens: int, rate) -> float:
    return (input_tokens * rate[0] + output_tokens * rate[1]) / 1_000_000


def _sum(db, since: datetime, *, include_reservations=False, feature=None, book_id=None) -> float:
    expression = AIUsage.cost_usd + AIUsage.reserved_usd if include_reservations else AIUsage.cost_usd
    query = select(func.coalesce(func.sum(expression), 0)).where(AIUsage.created_at >= since)
    if feature:
        query = query.where(AIUsage.feature == feature)
    if book_id:
        query = query.where(AIUsage.book_id == book_id)
    return float(db.scalar(query) or 0)


def total_cost(session_factory, period: str = "month", *, feature=None, book_id=None) -> float:
    with session_factory() as db:
        return round(_sum(db, _since(period), feature=feature, book_id=book_id), 6)


def reserve(session_factory, settings, *, feature: str, model: str,
            estimated_input_tokens: int, max_output_tokens: int, rate,
            job_id: str | None = None, book_id: str | None = None) -> int:
    known = priced(rate)
    if (settings.ai_daily_limit_usd or settings.ai_monthly_limit_usd) and not known:
        raise AILimitError("Für globale KI-Limits müssen für dieses Modell Eingabe- und Ausgabepreise konfiguriert sein")
    estimate = cost(estimated_input_tokens, max_output_tokens, rate) if known else 0.0
    with session_factory() as db:
        # Serialize the read/check/insert across request threads and processes.
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        daily = _sum(db, _since("day"), include_reservations=True)
        monthly = _sum(db, _since("month"), include_reservations=True)
        if settings.ai_daily_limit_usd and daily + estimate > settings.ai_daily_limit_usd:
            raise AILimitError("Tageslimit für KI-Kosten erreicht")
        if settings.ai_monthly_limit_usd and monthly + estimate > settings.ai_monthly_limit_usd:
            raise AILimitError("Monatslimit für KI-Kosten erreicht")
        row = AIUsage(created_at=datetime.now(timezone.utc), feature=feature, model=model,
                      job_id=job_id, book_id=book_id, input_tokens=0, output_tokens=0,
                      cost_usd=0, reserved_usd=estimate, input_rate=rate[0] if known else None,
                      output_rate=rate[1] if known else None, status="reserved")
        db.add(row)
        db.commit()
        return row.id


def finish(session_factory, usage_id: int, *, input_tokens: int, output_tokens: int,
           status: str = "completed", usage_known: bool = True) -> float | None:
    with session_factory() as db:
        row = db.get(AIUsage, usage_id)
        if row is None or row.status != "reserved":
            raise RuntimeError("KI-Nutzungsreservierung fehlt")
        row.input_tokens = input_tokens
        row.output_tokens = output_tokens
        row.cost_usd = cost(input_tokens, output_tokens, (row.input_rate, row.output_rate)) if usage_known and row.input_rate is not None and row.output_rate is not None else 0.0
        row.reserved_usd = 0
        row.status = status if usage_known else "unknown"
        if not usage_known:
            row.error = "Anbieter meldete keine Token-Nutzung; Kosten unbekannt"
        amount = row.cost_usd if usage_known and row.input_rate is not None else None
        db.commit()
        return amount


def fail(session_factory, usage_id: int, error: str = "KI-Anfrage fehlgeschlagen") -> None:
    with session_factory() as db:
        db.execute(update(AIUsage).where(AIUsage.id == usage_id, AIUsage.status == "reserved")
                   .values(status="failed", reserved_usd=0, error=error[:300]))
        db.commit()


def mark_invalid(session_factory, usage_id: int) -> None:
    with session_factory() as db:
        db.execute(update(AIUsage).where(AIUsage.id == usage_id, AIUsage.status == "completed")
                   .values(status="invalid", error="KI-Antwort konnte nicht verwendet werden"))
        db.commit()


def expire_reservations(session_factory) -> None:
    with session_factory() as db:
        db.execute(update(AIUsage).where(AIUsage.status == "reserved")
                   .values(status="interrupted", reserved_usd=0,
                           error="Dienst während der KI-Anfrage neu gestartet; Kosten unbekannt"))
        db.commit()


def summary(session_factory, settings=None, *, feature: str | None = None, book_id: str | None = None) -> dict:
    with session_factory() as db:
        query = select(AIUsage.feature, func.count(AIUsage.id),
                       func.sum(AIUsage.input_tokens), func.sum(AIUsage.output_tokens),
                       func.sum(AIUsage.cost_usd),
                       func.sum(case((((AIUsage.input_rate.is_(None) & (AIUsage.cost_usd == 0))
                                       | AIUsage.status.in_(["failed", "interrupted", "unknown"])), 1), else_=0)))
        if feature:
            query = query.where(AIUsage.feature == feature)
        if book_id:
            query = query.where(AIUsage.book_id == book_id)
        rows = db.execute(query.group_by(AIUsage.feature)).all()
        day = _sum(db, _since("day"), feature=feature, book_id=book_id)
        month = _sum(db, _since("month"), feature=feature, book_id=book_id)
        reserved = (_sum(db, _since("month"), include_reservations=True, feature=feature, book_id=book_id)
                    - _sum(db, _since("month"), feature=feature, book_id=book_id))
        global_day = _sum(db, _since("day"))
        global_month = _sum(db, _since("month"))
    by_feature = {name: {"requests": count, "input_tokens": int(input_tokens or 0),
                         "output_tokens": int(output_tokens or 0), "cost_usd": round(float(amount or 0), 6),
                         "unpriced_requests": int(unpriced or 0)}
                  for name, count, input_tokens, output_tokens, amount, unpriced in rows}
    threshold = getattr(settings, "ai_warning_percent", 80) / 100 if settings else 0.8
    day_limit = getattr(settings, "ai_daily_limit_usd", 0) if settings else 0
    month_limit = getattr(settings, "ai_monthly_limit_usd", 0) if settings else 0
    return {"day_usd": round(day, 6), "month_usd": round(month, 6),
            "reserved_usd": round(reserved, 6),
            "warning": {"day": bool(day_limit and global_day >= day_limit * threshold),
                        "month": bool(month_limit and global_month >= month_limit * threshold)},
            "by_feature": by_feature}


def records(session_factory, *, limit: int = 100, feature: str | None = None,
            book_id: str | None = None) -> list[dict]:
    with session_factory() as db:
        query = select(AIUsage).order_by(AIUsage.created_at.desc()).limit(limit)
        if feature:
            query = query.where(AIUsage.feature == feature)
        if book_id:
            query = query.where(AIUsage.book_id == book_id)
        rows = db.scalars(query).all()
        return [{"id": row.id, "created_at": row.created_at.isoformat(), "feature": row.feature,
                 "job_id": row.job_id, "book_id": row.book_id, "model": row.model,
                 "input_tokens": row.input_tokens, "output_tokens": row.output_tokens,
                 "cost_usd": row.cost_usd if row.status in {"completed", "invalid"} and (row.input_rate is not None or row.cost_usd > 0) else None,
                 "reserved_usd": row.reserved_usd, "input_rate": row.input_rate,
                 "output_rate": row.output_rate, "status": row.status, "error": row.error} for row in rows]
