import pytest
import requests


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Every test gets its own config/cache/reports dir and no real credentials."""
    monkeypatch.setenv("ASAM_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ASAM_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("ASAM_REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.chdir(tmp_path)
    from asam import auth

    auth._cached.clear()
    yield tmp_path
    auth._cached.clear()


@pytest.fixture
def fake_google(monkeypatch):
    """Route authenticated API calls through a plain session so `responses` can mock them."""
    from asam import auth

    monkeypatch.setattr(auth, "session", lambda: requests.Session())
    monkeypatch.setattr(auth, "identity", lambda: {"email": "me@example.com", "type": "user"})
