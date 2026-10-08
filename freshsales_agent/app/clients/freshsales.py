from __future__ import annotations

import time
from typing import Any
import requests

from app.config import settings
from app.exceptions import FreshsalesAPIError, FreshsalesTransportError


class FreshsalesClient:
    def __init__(self, api_key: str | None = None, domain: str | None = None):
        self.api_key = api_key or settings.api_key
        self.base_url = (domain or settings.base_url).rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Token token={self.api_key}",
            "Content-Type": "application/json",
        })
        self.min_interval = 0.72  # ~5,000 requests / hour safe rate
        self.last_request_time = 0.0

    def _wait_for_rate_limit(self) -> None:
        elapsed = time.time() - self.last_request_time
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self.last_request_time = time.time()

    def _request(self, method: str, path: str, max_retries: int = 3, **kwargs: Any) -> dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        for attempt in range(max_retries + 1):
            self._wait_for_rate_limit()
            try:
                response = self.session.request(method, url, timeout=60, **kwargs)
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", 60))
                    if attempt < max_retries:
                        time.sleep(retry_after)
                        continue
                    raise FreshsalesAPIError(f"Freshsales rate limit exceeded (429): {response.text}")
                if response.status_code == 404:
                    return {}
                response.raise_for_status()
                if response.content:
                    return response.json()
                return {}
            except requests.HTTPError as exc:
                if attempt == max_retries or response.status_code not in (500, 502, 503, 504):
                    raise FreshsalesAPIError(f"Freshsales HTTP {response.status_code}: {response.text}") from exc
                time.sleep(2 ** attempt)
            except requests.RequestException as exc:
                if attempt == max_retries:
                    raise FreshsalesTransportError(f"Freshsales connection error: {exc}") from exc
                time.sleep(2 ** attempt)
        return {}

    def get_account_filters(self) -> list[dict[str, Any]]:
        """
        Retrieves available account filters/views from /api/sales_accounts/filters.
        """
        res = self._request("GET", "api/sales_accounts/filters")
        return res.get("filters", [])

    def fetch_sales_accounts(self, view_id: int | str | None = None, page: int = 1, per_page: int = 100) -> dict[str, Any]:
        """
        Fetches a paginated page of sales accounts from standard list or specific view.
        """
        if view_id:
            path = f"api/sales_accounts/view/{view_id}"
        else:
            path = "api/sales_accounts"
        return self._request("GET", path, params={"page": page, "per_page": per_page})

    def get_all_accounts_view_id(self) -> int | str | None:
        """
        Finds the View ID for 'All Accounts'.
        """
        if hasattr(self, "_all_accounts_view_id") and self._all_accounts_view_id:
            return self._all_accounts_view_id
        filters = self.get_account_filters()
        for f in filters:
            if str(f.get("name", "")).strip().lower() == "all accounts":
                self._all_accounts_view_id = f.get("id")
                return self._all_accounts_view_id
        if filters:
            self._all_accounts_view_id = filters[0].get("id")
            return self._all_accounts_view_id
        return None

    def fetch_sales_accounts_scroll(self, view_id: int | str | None = None, last_fetched_id: int | str | None = None, limit: int = 100) -> dict[str, Any]:
        """
        Fetches sales accounts using the Freshsales Scroll endpoint.
        """
        target_view = view_id or self.get_all_accounts_view_id()
        if target_view:
            path = f"api/sales_accounts/scroll/{target_view}"
        else:
            path = "api/sales_accounts/scroll"
        params: dict[str, Any] = {"limit": limit}
        if last_fetched_id is not None:
            params["last_fetched_id"] = last_fetched_id
        return self._request("GET", path, params=params)

    def lookup_contact_by_email(self, email: str) -> dict[str, Any] | None:
        """
        Looks up a single contact by email in Freshsales using /api/search.
        """
        clean_email = email.lower().strip()
        try:
            results = self._request("GET", "api/search", params={"q": clean_email, "include": "contact"})
            if isinstance(results, list):
                for item in results:
                    if item.get("type") == "contact" and str(item.get("email", "")).lower().strip() == clean_email:
                        contact_id = item.get("id")
                        if contact_id:
                            full = self._request("GET", f"api/contacts/{contact_id}")
                            return full.get("contact", full)
                        return item
        except Exception:
            pass
        return None

    def batch_lookup_emails(self, emails: list[str]) -> dict[str, dict[str, Any]]:
        """
        Looks up a list of emails in Freshsales, returning {email: contact_data}.
        """
        found_map: dict[str, dict[str, Any]] = {}
        for email in emails:
            res = self.lookup_contact_by_email(email)
            if res:
                found_map[email.lower().strip()] = res
        return found_map

    def bulk_upsert_contacts(self, contacts: list[dict[str, Any]]) -> str:
        """
        Submits a batch of contacts to Freshsales Bulk Upsert API.
        Each contact item must be formatted as:
          {"emails": "<email>", "data": {...}} or {"id": "<id>", "data": {...}}
        Returns the job status ID.
        """
        path = "api/contacts/bulk_upsert"
        payload = {"contacts": contacts}
        res = self._request("POST", path, json=payload)
        
        job_status_url = res.get("job_status_url", "")
        if job_status_url:
            job_id = job_status_url.rstrip("/").split("/")[-1]
            return job_id

        job_id = res.get("job_id") or res.get("id") or str(res.get("job", {}).get("id", ""))
        return str(job_id)

    def get_job_status(self, job_id: str) -> dict[str, Any]:
        """
        Polls status of a bulk job via /api/job_statuses/{job_id}.
        """
        path = f"api/job_statuses/{job_id}"
        return self._request("GET", path)

    def create_or_update_contact_single(self, email: str, payload: dict[str, Any], existing_id: int | str | None = None) -> dict[str, Any]:
        """
        Direct single contact fallback upsert.
        """
        if existing_id:
            path = f"api/contacts/{existing_id}"
            return self._request("PUT", path, json={"contact": payload})
        else:
            path = "api/contacts"
            payload["emails"] = email
            return self._request("POST", path, json={"contact": payload})

    def close(self) -> None:
        self.session.close()

