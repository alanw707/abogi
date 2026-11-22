import pytest


@pytest.fixture(autouse=True)
def fresh_connectors(monkeypatch):
    """Connectors are built once per process; each test builds its own."""
    import abogi.connectors as connectors

    monkeypatch.setattr(connectors, "_built", None)


@pytest.fixture(autouse=True)
def private_profile(monkeypatch, tmp_path):
    """Never read or write the real profile store in tests."""
    for name in ("ABOGI_PROFILE_VAULT", "ABOGI_GOOGLE_TOKEN_VAULT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ABOGI_PROFILE_DIR", str(tmp_path / "profile"))
