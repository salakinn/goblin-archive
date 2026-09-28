from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import text

from backend.database import init_db
from backend.models import AIUsage
from backend.usage import AILimitError, fail, finish, records, reserve, summary


def test_parallel_requests_reserve_limit_atomically(db_context):
    settings, factory = db_context
    settings.ai_daily_limit_usd = 0.02
    rate = (100, 100)

    def attempt():
        try:
            return reserve(factory, settings, feature="translation", model="example",
                           estimated_input_tokens=100, max_output_tokens=100,
                           rate=rate, book_id="bk_one")
        except AILimitError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: attempt(), range(2)))
    assert sum(value is not None for value in ids) == 1
    usage_id = next(value for value in ids if value is not None)
    assert summary(factory, settings)["reserved_usd"] == 0.02
    assert finish(factory, usage_id, input_tokens=50, output_tokens=20) == 0.007
    assert summary(factory, settings)["day_usd"] == 0.007
    assert summary(factory, settings, book_id="bk_other")["day_usd"] == 0
    assert records(factory)[0]["input_rate"] == 100


def test_unknown_prices_are_visible_and_block_configured_limits(db_context):
    settings, factory = db_context
    usage_id = reserve(factory, settings, feature="tagging", model="example",
                       estimated_input_tokens=100, max_output_tokens=100, rate=(0, 0))
    finish(factory, usage_id, input_tokens=20, output_tokens=10)
    assert records(factory)[0]["cost_usd"] is None
    assert summary(factory)["by_feature"]["tagging"]["unpriced_requests"] == 1
    settings.ai_monthly_limit_usd = 1
    try:
        reserve(factory, settings, feature="tagging", model="example",
                estimated_input_tokens=100, max_output_tokens=100, rate=(0, 0))
    except AILimitError:
        pass
    else:
        raise AssertionError("unpriced request bypassed global limit")
    with factory() as db:
        assert db.query(AIUsage).count() == 1


def test_missing_provider_usage_is_reported_as_unknown(db_context):
    settings, factory = db_context
    usage_id = reserve(factory, settings, feature="translation", model="example",
                       estimated_input_tokens=100, max_output_tokens=100, rate=(100, 100))
    assert finish(factory, usage_id, input_tokens=0, output_tokens=0, usage_known=False) is None
    entry = records(factory)[0]
    assert entry["status"] == "unknown"
    assert entry["cost_usd"] is None
    assert summary(factory)["by_feature"]["translation"]["unpriced_requests"] == 1
    assert summary(factory)["reserved_usd"] == 0


def test_failed_request_releases_reservation(db_context):
    settings, factory = db_context
    usage_id = reserve(factory, settings, feature="tagging", model="example",
                       estimated_input_tokens=100, max_output_tokens=100, rate=(100, 100))
    fail(factory, usage_id)
    assert records(factory)[0]["status"] == "failed"
    assert summary(factory)["reserved_usd"] == 0


def test_existing_usage_table_gets_reservation_and_price_columns(db_context):
    _settings, factory = db_context
    engine = factory.kw['bind']
    with engine.begin() as connection:
        connection.execute(text('DROP TABLE ai_usage'))
        connection.execute(text('DROP TABLE translation_jobs'))
        connection.execute(text('''CREATE TABLE ai_usage (
            id INTEGER PRIMARY KEY, created_at DATETIME NOT NULL, feature VARCHAR(50) NOT NULL,
            job_id VARCHAR(32), book_id VARCHAR(32), model VARCHAR(120) NOT NULL,
            input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
            cost_usd FLOAT NOT NULL, status VARCHAR(20) NOT NULL, error TEXT
        )'''))
        connection.execute(text('''CREATE TABLE translation_jobs (
            id VARCHAR(32) PRIMARY KEY, source_book_id VARCHAR(32) NOT NULL,
            status VARCHAR(32) NOT NULL, data_json TEXT NOT NULL
        )'''))
    init_db(engine)
    with engine.connect() as connection:
        columns = {row[1] for row in connection.execute(text('PRAGMA table_info(ai_usage)'))}
        job_columns = {row[1] for row in connection.execute(text('PRAGMA table_info(translation_jobs)'))}
    assert {'reserved_usd', 'input_rate', 'output_rate'} <= columns
    assert 'revision' in job_columns
