"""
db/session.py — Thread-safe SQLAlchemy session context manager.

Usage (sync routes/services):
    from db.session import get_db
    with get_db() as db:
        db.add(row)          # commit happens automatically on exit
        results = db.execute(text("SELECT ...")).fetchall()
"""

from __future__ import annotations

from contextlib import contextmanager

from db.database import SessionLocal


@contextmanager
def get_db():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
