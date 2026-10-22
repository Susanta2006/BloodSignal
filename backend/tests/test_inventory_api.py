"""HTTP contract, security, readiness and deployment-config tests for /api/v1."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import build_container, create_app
from app.services.stock_source import AccessNotPermitted, SourceUnavailable
from tests.inv_helpers import Clock, FakeLive, bank, srec, tick

SEARCH = "/api/v1/blood-stock/search"
P = {"blood_group": "O-", "component": "whole_blood", "district": "Imphal West", "state": "Manipur"}
SECRET = "SECRET-KEY-MUST-NOT-LEAK"


def client(adapter=None, **kw):
    ad = adapter or FakeLive()
    if adapter is None:
        ad.banks, ad.records = [bank()], [srec()]
    clock = Clock()
    s = Settings(stock_source_mode=kw.pop("mode", "live"), retry_base_delay_s=0, rate_limit_per_minute=1000,
                 llm_api_key=SECRET, eraktkosh_api_key=SECRET, **kw)
    c = build_container(s, stock=ad, clock=clock, sleep=tick)
    return TestClient(create_app(s, c)), ad, clock


def test_search_envelope_fields_and_live_then_cached():
    c, ad, clock = client()
    r = c.get(SEARCH, params=P)
    j = r.json()
    assert r.status_code == 200 and j["source_status"] == "LIVE" and j["cache_status"] == "REFRESHED"
    for k in ("data", "source", "source_status", "fetched_at", "cache_status", "is_stale", "is_demo",
              "stale_age_seconds", "warnings", "error", "pagination", "disclaimer"):
        assert k in j
    row = j["data"][0]["stock"][0]
    assert row["quantity_state"] == "REPORTED" and row["source_updated_at"] and row["evidence_ref"]
    j2 = c.get(SEARCH, params=P).json()
    assert j2["source_status"] == "CACHED" and ad.calls == 1 and j2["fetched_at"] == j["fetched_at"]


def test_stale_is_http_200_so_ui_can_render_and_unavailable_is_503():
    c, ad, clock = client()
    c.get(SEARCH, params=P)
    clock.advance(minutes=25)
    ad.exc = SourceUnavailable("x")
    r = c.get(SEARCH, params=P)
    assert r.status_code == 200 and r.json()["is_stale"] and r.json()["refresh_error"]["code"] == "SOURCE_UNAVAILABLE"
    clock.advance(hours=3)
    r = c.get(SEARCH, params=P)
    assert r.status_code == 503 and r.json()["data"] == [] and r.json()["source_status"] == "UNAVAILABLE"
    assert r.headers.get("retry-after")


def test_access_denied_maps_to_503_with_specific_status():
    c, ad, _ = client()
    ad.exc = AccessNotPermitted("no")
    r = c.get(SEARCH, params=P)
    assert r.status_code == 503 and r.json()["source_status"] == "SOURCE_ACCESS_DENIED"


def test_request_validation():
    c, *_ = client()
    assert c.get(SEARCH, params={"blood_group": "O-", "component": "whole_blood"}).status_code == 422
    assert c.get(SEARCH, params={**P, "blood_group": "Z+"}).status_code == 422
    assert c.get(SEARCH, params={**P, "page_size": 1000}).status_code == 422
    assert c.get(SEARCH, params={**P, "district": "x" * 500}).status_code == 422
    assert c.get(SEARCH, params={**P, "component": "'; DROP TABLE blood_stock;--"}).status_code == 422


def test_sql_looking_text_in_filters_is_just_text():
    c, ad, _ = client()
    r = c.get(SEARCH, params={**P, "centre_query": "'; DROP TABLE blood_banks;--"})
    assert r.status_code == 200 and r.json()["data"] == []


def test_manual_refresh_disabled_without_token_and_protected_with_it():
    c, ad, clock = client()
    body = {"blood_group": "O-", "component": "whole_blood", "district": "Imphal West"}
    assert c.post("/api/v1/blood-stock/refresh", json=body).status_code == 404
    c2, ad2, clock2 = client(refresh_admin_token="op-token")
    assert c2.post("/api/v1/blood-stock/refresh", json=body).status_code == 404
    assert c2.post("/api/v1/blood-stock/refresh", json=body,
                   headers={"Authorization": "Bearer wrong"}).status_code == 404
    ok = c2.post("/api/v1/blood-stock/refresh", json=body, headers={"Authorization": "Bearer op-token"})
    assert ok.status_code == 200 and ok.json()["source_status"] == "LIVE"
    c2.post("/api/v1/blood-stock/refresh", json=body, headers={"Authorization": "Bearer op-token"})
    assert ad2.calls == 1                                       # min-interval blocks hammering


def test_no_secret_in_any_public_response():
    c, ad, _ = client()
    for path, kw in [(SEARCH, dict(params=P)), ("/health/ready", {}), ("/health/live", {}),
                     ("/api/source", {}), ("/api/v1/blood-banks", {})]:
        assert SECRET not in c.get(path, **kw).text
    ad.exc = AccessNotPermitted(SECRET)         # even if an upstream error text contained it, we never echo it
    assert SECRET not in c.get(SEARCH, params={**P, "district": "Other"}).text


def test_directory_lists_persisted_banks_without_calling_source():
    c, ad, _ = client()
    assert c.get("/api/v1/blood-banks").status_code == 503      # nothing synced yet: not "empty directory"
    c.get(SEARCH, params=P)
    calls = ad.calls
    j = c.get("/api/v1/blood-banks", params={"query": "city"}).json()
    assert ad.calls == calls and j["pagination"]["total"] == 1 and j["source_status"] == "CACHED"
    j = c.get("/api/v1/blood-banks", params={"state": "Manipur"}).json()
    assert j["pagination"]["total"] == 0 and any("source-provided" in w for w in j["warnings"])


def test_bank_detail_has_per_row_timestamps_and_404():
    c, *_ = client()
    c.get(SEARCH, params=P)
    r = c.get("/api/v1/blood-banks/b1")
    assert r.status_code == 200 and r.json()["data"][0]["stock"][0]["fetched_at"]
    assert c.get("/api/v1/blood-banks/nope").status_code == 404


def test_demo_mode_response_is_clearly_demo_over_http():
    c, *_ = client(adapter=None, mode="demo")
    s = Settings(stock_source_mode="demo", rate_limit_per_minute=1000)
    c = TestClient(create_app(s, build_container(s, clock=Clock(), sleep=tick)))
    j = c.get(SEARCH, params=P).json()
    assert j["source_status"] == "DEMO" and j["is_demo"] and j["demo_label"]


# ---- readiness / deployment (#46-#50)
def test_ready_ok_with_memory_locally_and_reports_storage():
    c, *_ = client()
    j = c.get("/health/ready").json()
    assert j["inventory_storage"]["state"] == "OK"


def test_ready_fails_in_production_without_durable_storage():
    c, *_ = client(app_env="production")
    r = c.get("/health/ready")
    assert r.status_code == 503 and r.json()["inventory_storage"]["state"] == "OK"
    assert r.json()["inventory_storage"]["backend"] == "memory"
    assert r.json()["workflow_storage"]["state"] == "NOT_DURABLE"


def test_ready_fails_when_db_ping_fails():
    c, ad, _ = client()
    c.app.state.container.inventory.repo.fail = RuntimeError("x")
    assert c.get("/health/ready").status_code == 503


def test_cors_allows_only_configured_origin():
    c, *_ = client(frontend_origin="https://app.example.com")
    ok = c.get(SEARCH, params=P, headers={"Origin": "https://app.example.com"})
    bad = c.get(SEARCH, params=P, headers={"Origin": "https://evil.example"})
    assert ok.headers.get("access-control-allow-origin") == "https://app.example.com"
    assert "access-control-allow-origin" not in bad.headers


def test_agent_endpoint_end_to_end_and_bad_input():
    c, *_ = client()
    r = c.post("/api/v1/agents/search", json={"filters": {**{k: P[k] for k in ("district", "state")},
                                                           "blood_group": "O-", "component": "whole_blood"}})
    j = r.json()
    assert r.status_code == 200 and j["status"] == "COMPLETED" and j["used_llm"] is False
    assert r.headers["cache-control"].startswith("no-store")
    assert j["approval_required"] is True and j["action_taken"] is False
    assert j["contact_plan"][0]["caveat"] and j["evidence_refs"]
    assert j["contact_plan"][0]["approval_status"] == "PENDING_HUMAN_REVIEW"
    assert "source_updated_at" in j["contact_plan"][0] and "freshness" in j["contact_plan"][0]
    assert c.post("/api/v1/agents/search", json={"text": "x" * 600}).status_code == 422
    assert c.post("/api/v1/agents/search", json={"filters": {}, "surprise": 1}).status_code == 422
    assert c.post("/api/v1/agents/search", content=b"x" * 20000,
                  headers={"Content-Type": "application/json"}).status_code == 413
