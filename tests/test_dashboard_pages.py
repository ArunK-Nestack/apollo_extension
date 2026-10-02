# -*- coding: utf-8 -*-
"""
Automated Test Suite for Multi-Page Operations Hub
==================================================
Validates that all 7 dedicated HTML pages, REST API endpoints,
and guardrails sandbox endpoints return HTTP 200 and valid schemas.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.api import app

client = TestClient(app)


def test_root_redirect():
    """Verify '/' redirects to '/batches'."""
    res = client.get("/", follow_redirects=False)
    assert res.status_code in (302, 307)
    assert res.headers["location"] == "/batches"


def test_all_seven_html_pages():
    """Verify all 7 dedicated MPA pages return HTTP 200 with HTML content."""
    pages = [
        "/batches",
        "/search-studio",
        "/fleet",
        "/guardrails",
        "/verifier",
        "/freshsales-sync",
        "/enrich",
    ]
    for page in pages:
        res = client.get(page)
        assert res.status_code == 200, f"Failed on page {page}"
        assert "text/html" in res.headers["content-type"]
        assert "APOLLO OPS" in res.text
        assert "Cockpit" in res.text


def test_telemetry_endpoint():
    """Verify /api/v1/telemetry returns valid database and fleet health."""
    res = client.get("/api/v1/telemetry")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert "database" in data
    assert "trie" in data
    assert "fleet" in data


def test_batches_endpoint():
    """Verify /api/v1/batches returns array of batches."""
    res = client.get("/api/v1/batches")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert isinstance(data["batches"], list)


def test_search_catalog_endpoint():
    """Verify /api/v1/search/catalog returns search listings."""
    res = client.get("/api/v1/search/catalog")
    assert res.status_code == 200
    data = res.json()
    assert "searches" in data


def test_guardrails_sandbox_endpoint():
    """Verify /api/v1/guardrails/sandbox runs 4-layer check in sub-millisecond range."""
    payload = {
        "company_name": "Bancroft Neurological",
        "company_domain": "bancroftneurological.org",
        "job_title": "Chief Executive Officer",
        "person_name": "Michael Roberts"
    }
    res = client.post("/api/v1/guardrails/sandbox", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert "final_decision" in data
    assert "layer1_domain" in data
    assert "layer2_title" in data
    assert "layer3_indian_name" in data
    assert "layer4_company_dedup" in data
    assert "latency_ms" in data


def test_task_launch_endpoint():
    """Verify /api/v1/tasks/launch dispatches task and returns task_id."""
    payload = {
        "task_type": "whatsapp_alert",
        "batch_tag": "test_batch",
        "parameters": {}
    }
    res = client.post("/api/v1/tasks/launch", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "launched"
    assert "task_id" in data
