"""Release checks and installation adapters for supported deployments."""
from __future__ import annotations

import hmac
import json
import re
import ssl
import subprocess
import time
import uuid
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from websockets.sync.client import connect
from websockets.exceptions import WebSocketException

from backend.config import Settings

RELEASE_URL = "https://api.github.com/repos/salakinn/goblin-archive/releases/latest"
IMAGE = "ghcr.io/salakinn/goblin-archive:stable"


class UpdateError(Exception):
    pass


def _read_secret(path: Path | None) -> str:
    if path is None:
        raise UpdateError("Update-Zugang ist nicht eingerichtet.")
    try:
        secret = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise UpdateError("Update-Zugang konnte nicht gelesen werden.") from exc
    if not secret:
        raise UpdateError("Update-Zugang ist leer.")
    return secret


def install_mode(settings: Settings) -> str | None:
    if settings.update_mode == "systemd" and settings.update_root and settings.update_password_file:
        return "systemd"
    if settings.update_mode in {"", "truenas"} and settings.truenas_ws_url and settings.truenas_username and \
            settings.truenas_api_key_file and settings.truenas_app_name and settings.update_password_file:
        return "truenas"
    return None


def _version(value: str) -> tuple[int, int, int] | None:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", value)
    return tuple(map(int, match.groups())) if match else None


@lru_cache(maxsize=8)
def _latest_release_for_window(_window: int) -> str | None:
    try:
        response = httpx.get(RELEASE_URL, headers={"Accept": "application/vnd.github+json"}, timeout=8)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        tag = response.json()["tag_name"]
    except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
        raise UpdateError("Neue Versionen konnten nicht abgefragt werden.") from exc
    if not isinstance(tag, str) or _version(tag) is None:
        raise UpdateError("Die veröffentlichte Versionsnummer ist ungültig.")
    return tag


def latest_release() -> str | None:
    return _latest_release_for_window(int(time.time() // 300))


def _rpc(ws, method: str, params: list, call_id: int):
    ws.send(json.dumps({"jsonrpc": "2.0", "id": call_id, "method": method, "params": params}))
    while True:
        response = json.loads(ws.recv(timeout=12))
        if response.get("id") != call_id:
            continue
        if "error" in response:
            raise UpdateError("TrueNAS hat die Update-Anfrage abgelehnt.")
        return response.get("result")


def _truenas_call(settings: Settings, method: str, params: list):
    try:
        parts = urlsplit(settings.truenas_ws_url)
    except ValueError as exc:
        raise UpdateError("TrueNAS-WebSocket-URL ist ungültig.") from exc
    if parts.scheme != "wss" or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise UpdateError("TrueNAS-WebSocket-URL muss eine wss://-Adresse sein.")
    key = _read_secret(settings.truenas_api_key_file)
    try:
        context = ssl.create_default_context(cafile=str(settings.truenas_ca_file) if settings.truenas_ca_file else None)
        with connect(settings.truenas_ws_url, ssl=context, open_timeout=8, close_timeout=2,
                     max_size=2 * 1024 * 1024) as ws:
            login = _rpc(ws, "auth.login_ex", [{"mechanism": "API_KEY_PLAIN",
                "username": settings.truenas_username, "api_key": key,
                "login_options": {"user_info": False}}], 1)
            if not isinstance(login, dict) or login.get("response_type") != "SUCCESS":
                raise UpdateError("TrueNAS-Anmeldung fehlgeschlagen.")
            return _rpc(ws, method, params, 2)
    except UpdateError:
        raise
    except (OSError, TimeoutError, ValueError, WebSocketException) as exc:
        raise UpdateError("TrueNAS ist für die Update-Prüfung nicht erreichbar.") from exc


def _app_info(settings: Settings) -> dict:
    info = _truenas_call(settings, "app.get_instance", [settings.truenas_app_name, {}])
    if not isinstance(info, dict) or not info.get("custom_app"):
        raise UpdateError("Die konfigurierte TrueNAS-App wurde nicht gefunden.")
    workloads = info.get("active_workloads")
    images = workloads.get("images", []) if isinstance(workloads, dict) else []
    if IMAGE not in images:
        raise UpdateError("Die TrueNAS-App verwendet nicht das erwartete Goblin-Image.")
    return info


def update_status(settings: Settings, current_version: str) -> dict:
    mode = install_mode(settings)
    status = {"enabled": True, "available": False, "install_ready": False,
              "install_mode": mode, "current_version": current_version, "latest_version": None}
    latest = latest_release()
    if latest is None:
        return status
    current = _version(current_version)
    if current is None:
        raise UpdateError("Die installierte Versionsnummer ist ungültig.")
    status["latest_version"] = latest
    status["available"] = _version(latest) > current
    if status["available"] and mode == "truenas":
        try:
            info = _app_info(settings)
            status["install_ready"] = bool(info.get("image_updates_available"))
        except UpdateError:
            pass
    elif status["available"] and mode == "systemd":
        root = settings.update_root
        status["install_ready"] = bool(root and root.is_absolute() and
            (root / "current" / "deploy" / "update-systemd.sh").is_file())
    return status


def _start_systemd_update(settings: Settings, tag: str) -> None:
    root = settings.update_root
    if root is None or not root.is_absolute():
        raise UpdateError("Update-Verzeichnis muss ein absoluter Pfad sein.")
    if not re.fullmatch(r"[A-Za-z0-9_.@-]+\.service", settings.update_service):
        raise UpdateError("Der Dienstname ist ungültig.")
    script = root / "current" / "deploy" / "update-systemd.sh"
    if not script.is_file():
        raise UpdateError("Das Update-Skript ist nicht installiert.")
    unit = f"goblin-update-{tag.replace('.', '-')}-{uuid.uuid4().hex[:8]}"
    command = ["systemd-run", "--user", "--collect", f"--unit={unit}",
               f"--setenv=GOBLIN_UPDATE_ROOT={root}",
               f"--setenv=GOBLIN_UPDATE_SERVICE={settings.update_service}",
               str(script), tag]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        raise UpdateError("Der Update-Dienst konnte nicht gestartet werden.") from exc


def install_update(settings: Settings, current_version: str, password: str) -> dict:
    mode = install_mode(settings)
    if mode is None:
        raise UpdateError("Für diese Installation ist kein Update-Weg eingerichtet.")
    if not hmac.compare_digest(password, _read_secret(settings.update_password_file)):
        raise UpdateError("Update-Passwort ist ungültig.")
    status = update_status(settings, current_version)
    if not status["available"] or not status["install_ready"]:
        raise UpdateError("Das Update ist noch nicht installierbar.")
    if mode == "systemd":
        _start_systemd_update(settings, status["latest_version"])
        return {"started": True, "job_id": None, "latest_version": status["latest_version"], "install_mode": mode}
    result = _truenas_call(settings, "app.upgrade", [settings.truenas_app_name,
        {"app_version": "latest", "snapshot_hostpaths": True}])
    return {"started": True, "job_id": result if isinstance(result, int) else None,
            "latest_version": status["latest_version"], "install_mode": mode}
