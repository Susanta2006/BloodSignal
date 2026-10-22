from __future__ import annotations

import httpx
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import build_container, create_app
from app.services.eraktkosh import ERaktKoshAdapter
from tests.inv_helpers import Clock, NOW, tick


def test_live_search_bypasses_shared_cache_and_preserves_contact_and_raw_time():
    fixture = [{
        "hospitalname": "Public Hospital Blood Centre",
        "hospitaladd": "Main Road, Imphal West, Manipur",
        "hospitalcontact": "Phone: 0385-2220000, Fax: 1234567890, Email: centre@example.gov.in",
        "hospitalCode": "00025", "hospitalType": "Govt.", "entrydate": "2026-10-20 14:00:00",
        "components": {"Whole Blood": {"available_WithQty": "O-Ve : 3",
                                           "not_available_WithQty": "A+Ve : 0"}},
    }]
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=fixture, headers={
            "Cache-Control": "no-cache, no-store, max-age=0, must-revalidate",
            "Content-Type": "application/json",
        })
    adapter = ERaktKoshAdapter(transport=httpx.MockTransport(handler), source_timezone="Asia/Kolkata")
    settings = Settings(stock_source_mode="live", rate_limit_per_minute=1000)
    container = build_container(settings, stock=adapter, clock=Clock(), sleep=tick)
    client = TestClient(create_app(settings, container))
    response = client.get("/api/v1/blood-stock/search", params={
        "blood_group": "O-", "component": "whole_blood", "state": "Manipur", "district": "Imphal West",
    })
    assert response.status_code == 200
    assert "no-store" in response.headers["cache-control"]
    body = response.json()
    assert body["source_status"] == "LIVE" and body["cache_status"] == "BYPASS"
    bank = body["data"][0]["bank"]
    assert bank["bank_id"] == "eraktkosh-00025" and bank["phone"] == "0385-2220000"
    assert bank["emails"] == ["centre@example.gov.in"]
    assert bank["faxes"] == ["1234567890"]
    stock = body["data"][0]["stock"][0]
    assert stock["quantity"] == 3
    assert stock["source_updated_at_raw"] == "2026-10-20 14:00:00"
    assert stock["requested_state_code"] == "14"
    assert stock["requested_component_id"] == "11"
    assert stock["fetched_at"]
    assert container.inventory.repo.stock == {}
    assert container.inventory.repo.banks == {}
    assert len(seen) == 1
