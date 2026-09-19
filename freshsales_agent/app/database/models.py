from __future__ import annotations

from datetime import datetime
from sqlalchemy import Column, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class FileRunMetric(Base):
    __tablename__ = "file_run_metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_file = Column(String(255), nullable=False)
    owner = Column(String(255), nullable=True)
    tag = Column(String(255), nullable=False)
    input = Column(Integer, default=0)
    tld_block = Column(Integer, default=0)
    created = Column(Integer, default=0)
    data = Column(Integer, default=0)  # updated in CRM
    failed = Column(Integer, default=0)
    error = Column(Text, nullable=True)
    success_percentage = Column(Float, default=0.0)
    wastage_percentage = Column(Float, default=0.0)
    status = Column(String(50), default="in_progress")
    timestamp = Column(String(50), default=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    # Extra audit metadata
    report_file_path = Column(Text, nullable=True)

    audit_records = relationship("ContactAuditTrail", back_populates="run_metric", cascade="all, delete-orphan")


class ContactAuditTrail(Base):
    __tablename__ = "contact_audit_trail"

    audit_id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, ForeignKey("file_run_metrics.id"), nullable=False)
    email = Column(String(255), nullable=False)
    action = Column(String(50), nullable=False)  # 'created', 'updated', 'skipped_duplicate', 'excluded_tld', 'failed'
    fields_filled_count = Column(Integer, default=0)
    fields_filled_names = Column(Text, nullable=True)
    error_reason = Column(Text, nullable=True)
    created_at = Column(String(50), default=lambda: datetime.now().isoformat())

    run_metric = relationship("FileRunMetric", back_populates="audit_records")
