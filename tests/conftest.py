import pytest


@pytest.fixture(autouse=True)
def _no_history_db(tmp_path, monkeypatch):
    """Point the history DB at a temp file once db.py exists (Task 6)."""
    try:
        import db  # noqa: F401
    except ImportError:
        return
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "hist.db")
