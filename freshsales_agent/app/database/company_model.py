import os
from datetime import datetime
from sqlalchemy import Column, DateTime, Integer, String, Text
from app.database.models import Base

TABLE_NAME = os.getenv("DB_TABLE", "").strip() or "freshsales_companies"


class FreshsalesCompany(Base):
    __tablename__ = TABLE_NAME

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(String(64), unique=True, index=True, nullable=False)
    company_name = Column(String(255), index=True, nullable=False)
    domain = Column(String(255), index=True, nullable=True)
    website = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def __repr__(self) -> str:
        return f"<FreshsalesCompany(id={self.id}, name='{self.company_name}', domain='{self.domain}')>"
