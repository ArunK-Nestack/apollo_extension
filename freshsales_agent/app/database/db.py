from __future__ import annotations

from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.database.models import Base

# Ensure local data directory exists if SQLite is used
db_url = settings.database_url
if db_url.startswith("sqlite"):
    if db_url.startswith("sqlite:///"):
        raw_path = db_url[len("sqlite:///"):]
        p = Path(raw_path)
        if not p.is_absolute():
            # If path contains 'freshsales_agent', resolve from root or data_dir
            if "freshsales_agent" in raw_path:
                p = (settings.data_dir.parent.parent / raw_path).resolve()
            else:
                p = (settings.data_dir / raw_path).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        db_url = f"sqlite:///{p.as_posix()}"
    else:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
else:
    engine = create_engine(db_url, pool_pre_ping=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db() -> None:
    """Initializes database tables."""
    Base.metadata.create_all(bind=engine)


def get_db() -> Session:
    """Yields a database session."""
    db = SessionLocal()
    try:
        return db
    finally:
        pass
