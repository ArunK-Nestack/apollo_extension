from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load from freshsales_agent/.env if exists, otherwise workspace .env
env_local = Path(__file__).resolve().parent.parent / ".env"
if env_local.is_file():
    load_dotenv(env_local)
else:
    load_dotenv()


@dataclass(frozen=True)
class Settings:
    api_key: str
    domain: str
    default_owner_id: str | None
    database_url: str
    input_dir: Path
    reports_dir: Path
    data_dir: Path
    poll_interval_seconds: int
    watch_interval_seconds: int
    batch_size: int
    auto_delete_input: bool

    def require_api_key(self) -> None:
        if not self.api_key or self.api_key.startswith("your_"):
            raise RuntimeError(
                "FRESHSALES_API_KEY is not configured. "
                "Add your real Freshsales API key to .env file."
            )

    @property
    def base_url(self) -> str:
        return self.domain.rstrip("/")


base_dir = Path(__file__).resolve().parent.parent

# Default SQLite path if DATABASE_URL is not set
default_db_path = (base_dir / "data" / "crm_automation.db").resolve()
default_sqlite_url = f"sqlite:///{default_db_path}"

import urllib.parse

# Construct Database URL from separate DB_* variables if provided
db_host = os.getenv("DB_HOST", "").strip()
if db_host:
    db_user = os.getenv("DB_USER", "").strip()
    db_password = os.getenv("DB_PASSWORD", "").strip()
    db_name = os.getenv("DB_NAME", "").strip()
    db_port = os.getenv("DB_PORT", "3306").strip()
    enc_user = urllib.parse.quote_plus(db_user)
    enc_pwd = urllib.parse.quote_plus(db_password)
    active_db_url = f"mysql+pymysql://{enc_user}:{enc_pwd}@{db_host}:{db_port}/{db_name}"
else:
    active_db_url = os.getenv("DATABASE_URL", default_sqlite_url).strip() or default_sqlite_url

db_table_name = os.getenv("DB_TABLE", "freshsales_companies").strip() or "freshsales_companies"

settings = Settings(
    api_key=os.getenv("FRESHSALES_API_KEY", "").strip(),
    domain=os.getenv("FRESHSALES_DOMAIN", "https://nestack.myfreshworks.com/crm/sales").strip(),
    default_owner_id=os.getenv("DEFAULT_OWNER_ID", "").strip() or None,
    database_url=active_db_url,
    input_dir=Path(
        os.getenv(
            "INPUT_DIR",
            r"G:\My Drive\crm automation\freshsales input",
        )
    ),
    reports_dir=Path(
        os.getenv(
            "REPORTS_DIR",
            r"G:\My Drive\crm automation\freshsales reports",
        )
    ),
    data_dir=base_dir / "data",
    poll_interval_seconds=int(os.getenv("POLL_INTERVAL_SECONDS", "60")),
    watch_interval_seconds=int(os.getenv("WATCH_INTERVAL_SECONDS", "15")),
    batch_size=int(os.getenv("BATCH_SIZE", "100")),
    auto_delete_input=os.getenv("AUTO_DELETE_INPUT", "true").lower() in ("true", "1", "yes"),
)
