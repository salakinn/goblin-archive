import json

from fastapi.testclient import TestClient

from backend import main, updater
from backend.config import Settings


def test_truenas_update_check_and_install_use_only_configured_app(tmp_path, monkeypatch):
    api_key = tmp_path / "api-key"
    api_key.write_text("secret-key")
    password = tmp_path / "password"
    password.write_text("install-secret")
    settings = Settings(
        data_dir=tmp_path / "data", truenas_ws_url="wss://nas.example/api/current",
        truenas_username="goblin-updater", truenas_api_key_file=api_key,
        truenas_app_name="goblin-archive", update_password_file=password,
    )
    monkeypatch.setattr(updater, "latest_release", lambda: "v0.2.0")
    calls = []

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def send(self, raw):
            calls.append(json.loads(raw))

        def recv(self, timeout):
            assert timeout == 12
            call = calls[-1]
            result = {
                "auth.login_ex": {"response_type": "SUCCESS"},
                "app.get_instance": {"custom_app": True, "image_updates_available": True,
                                     "active_workloads": {"images": [updater.IMAGE]}},
                "app.upgrade": 42,
            }[call["method"]]
            return json.dumps({"jsonrpc": "2.0", "id": call["id"], "result": result})

    monkeypatch.setattr(updater, "connect", lambda *_args, **_kwargs: FakeSocket())
    status = updater.update_status(settings, "v0.1.0")
    assert status == {"enabled": True, "available": True, "install_ready": True,
                      "install_mode": "truenas", "current_version": "v0.1.0", "latest_version": "v0.2.0"}
    assert calls[0]["params"][0]["api_key"] == "secret-key"
    assert calls[1]["params"] == ["goblin-archive", {}]
    before = len(calls)
    try:
        updater.install_update(settings, "v0.1.0", "wrong")
        assert False, "wrong password was accepted"
    except updater.UpdateError:
        assert len(calls) == before
    result = updater.install_update(settings, "v0.1.0", "install-secret")
    assert result == {"started": True, "job_id": 42, "latest_version": "v0.2.0", "install_mode": "truenas"}
    assert calls[-1]["method"] == "app.upgrade"
    assert calls[-1]["params"] == ["goblin-archive", {"app_version": "latest", "snapshot_hostpaths": True}]


def test_update_api_shows_release_without_installer(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "settings", Settings(data_dir=tmp_path / "data"))
    monkeypatch.setattr(updater, "latest_release", lambda: "v0.2.0")
    with TestClient(main.app) as client:
        status = client.get("/api/update")
        assert status.status_code == 200
        assert status.json()["enabled"] is True
        assert status.json()["available"] is True
        assert status.json()["install_ready"] is False
        assert client.post("/api/update/install", json={"password": "anything"}).status_code == 400


def test_systemd_update_uses_fixed_script_and_no_shell(tmp_path, monkeypatch):
    root = tmp_path / "install"
    script = root / "current" / "deploy" / "update-systemd.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/sh\n")
    password = tmp_path / "password"
    password.write_text("install-secret")
    settings = Settings(data_dir=tmp_path / "data", update_mode="systemd",
                        update_root=root, update_password_file=password)
    monkeypatch.setattr(updater, "latest_release", lambda: "v0.2.0")
    commands = []
    monkeypatch.setattr(updater.subprocess, "run", lambda command, **kwargs: commands.append((command, kwargs)))
    status = updater.update_status(settings, "0.1.0")
    assert status["available"] and status["install_ready"]
    assert status["install_mode"] == "systemd"
    result = updater.install_update(settings, "0.1.0", "install-secret")
    assert result["latest_version"] == "v0.2.0"
    assert result["install_mode"] == "systemd"
    command, options = commands[0]
    assert command[0:3] == ["systemd-run", "--user", "--collect"]
    assert command[-2:] == [str(script), "v0.2.0"]
    assert options["check"] is True


def test_update_api_blocks_active_imports(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "settings", Settings(data_dir=tmp_path / "data"))
    with TestClient(main.app) as client:
        monkeypatch.setattr(main.app.state.import_manager, "has_active_imports", lambda: True)
        response = client.post("/api/update/install", json={"password": "anything"})
        assert response.status_code == 409
