import pytest


@pytest.fixture(autouse=True)
def fresh_connectors(monkeypatch):
    """Connectors are built once per process; each test builds its own."""
    import abogi.connectors as connectors

    monkeypatch.setattr(connectors, "_built", None)
