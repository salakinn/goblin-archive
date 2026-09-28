from __future__ import annotations

import stat

import pytest
from fastapi.testclient import TestClient

from backend import auth, main
from backend.config import Settings


@pytest.mark.auth
def test_setup_login_csrf_logout_and_persistence(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path / "data")
    monkeypatch.setattr(main, "settings", settings)
    with TestClient(main.app) as client:
        path = settings.data_dir / "auth-setup-code"
        code = path.read_text().strip()
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert client.get("/api/books").status_code == 401
        assert client.get("/api/auth/status").json()["configured"] is False
        assert client.post("/api/auth/setup", json={"code": "wrong", "password": "long-secret-password"}).status_code == 401
        setup = client.post("/api/auth/setup", json={"code": code, "password": "long-secret-password"})
        assert setup.status_code == 200, setup.text
        assert "HttpOnly" in setup.headers["set-cookie"]
        assert "SameSite=strict" in setup.headers["set-cookie"]
        assert not path.exists()
        csrf = setup.json()["csrf_token"]
        assert client.get("/api/settings/ai").status_code == 200
        assert client.put("/api/settings/ai", json={"ai_tagging_model": "gpt-test"}).status_code == 403
        assert client.put("/api/settings/ai", json={"ai_tagging_model": "gpt-test"},
                          headers={"X-CSRF-Token": csrf}).status_code == 200
        assert client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 200
        assert client.get("/api/books").status_code == 401
        assert client.post("/api/auth/setup", json={"code": code, "password": "another-long-password"}).status_code == 409
        login = client.post("/api/auth/login", json={"password": "long-secret-password"})
        assert login.status_code == 200
        assert client.get("/api/books").status_code == 200
    with TestClient(main.app) as restarted:
        assert restarted.get("/api/auth/status").json()["configured"] is True
        assert restarted.post("/api/auth/login", json={"password": "long-secret-password"}).status_code == 200
        auth.reset_password(settings.data_dir, "replacement-password")
        assert restarted.get("/api/books").status_code == 401
        assert restarted.post("/api/auth/login", json={"password": "long-secret-password"}).status_code == 401
        assert restarted.post("/api/auth/login", json={"password": "replacement-password"}).status_code == 200


@pytest.mark.auth
def test_cross_site_and_login_throttling(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "settings", Settings(data_dir=tmp_path / "data"))
    with TestClient(main.app) as client:
        code = (tmp_path / "data" / "auth-setup-code").read_text().strip()
        response = client.post("/api/auth/setup", json={"code": code, "password": "long-secret-password"},
                               headers={"Origin": "http://evil.example", "Sec-Fetch-Site": "cross-site"})
        assert response.status_code == 403
        assert client.post("/api/auth/setup", json={"code": code, "password": "long-secret-password"}).status_code == 200
        for _ in range(5):
            assert client.post("/api/auth/login", json={"password": "wrong"}).status_code == 401
        assert client.post("/api/auth/login", json={"password": "long-secret-password"}).status_code == 429
