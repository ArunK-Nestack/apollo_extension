from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class ContactPayload:
    email: str
    first_name: str | None = None
    last_name: str | None = None
    job_title: str | None = None
    company_name: str | None = None
    work_number: str | None = None
    mobile_number: str | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    owner_id: str | None = None
    tags: list[str] = field(default_factory=list)
    custom_fields: dict[str, Any] = field(default_factory=dict)
    sales_account: dict[str, Any] = field(default_factory=dict)

    def to_contact_data_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        if self.first_name:
            data["first_name"] = self.first_name
        if self.last_name:
            data["last_name"] = self.last_name
        if self.job_title:
            data["job_title"] = self.job_title
        if self.company_name:
            data["company_name"] = self.company_name
        if self.work_number:
            data["work_number"] = self.work_number
        if self.mobile_number:
            data["mobile_number"] = self.mobile_number
        if self.address:
            data["address"] = self.address
        if self.city:
            data["city"] = self.city
        if self.state:
            data["state"] = self.state
        if self.country:
            data["country"] = self.country
        if self.owner_id:
            data["owner_id"] = self.owner_id
        if self.tags:
            data["tags"] = self.tags
        if self.custom_fields:
            data["custom_field"] = self.custom_fields
        if self.sales_account:
            data["sales_account"] = self.sales_account
        return data

    def to_freshsales_dict(self) -> dict[str, Any]:
        data = self.to_contact_data_dict()
        data["emails"] = self.email
        return data



@dataclass
class FileProcessingResult:
    source_filename: str
    tag_applied: str
    apollo_login_owner: str | None
    total_input_rows: int = 0
    file_duplicates_skipped: int = 0
    tld_filtered_count: int = 0
    contacts_sent_to_crm: int = 0
    freshly_created_count: int = 0
    updated_in_crm_count: int = 0
    failed_errors_count: int = 0
    status: str = "in_progress"  # 'in_progress', 'completed', 'failed'
    report_file_path: str | None = None
    error_message: str | None = None
    started_at: str = field(default_factory=lambda: datetime.now().isoformat())
    completed_at: str | None = None

    @property
    def updated_percentage(self) -> float:
        total_existing = self.updated_in_crm_count
        if total_existing == 0:
            return 0.0
        return 100.0

    @property
    def overall_success_percentage(self) -> float:
        if self.contacts_sent_to_crm == 0:
            return 0.0
        success = self.freshly_created_count + self.updated_in_crm_count
        return round((success / self.contacts_sent_to_crm) * 100.0, 2)

    @property
    def wastage_percentage(self) -> float:
        """
        Wastage / yield calculation: (updated + created) / 100 as specified.
        """
        return round((self.updated_in_crm_count + self.freshly_created_count) / 100.0, 2)
