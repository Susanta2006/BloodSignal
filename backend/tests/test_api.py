import pytest
from concurrent.futures import ThreadPoolExecutor

from app.schemas import TaskStatus
from app.main import RequestBodyLimitMiddleware
from tests.conftest import (DEVICE, AccessNotPermitted, FailingRoutes, SourceUnavailable,
                            StaticAdapter, StaticDirectory, cands, device_payload, payload, rec, start)


# ---------------------------------------------------------- official test cases
def test_tc1_demo_scenario_end_to_end(make_client):
    client, _ = make_client()
    run_id, h = start(client, device_payload())
    run = client.get(f"/api/runs/{run_id}", headers=h).json()
    assert run["status"] == "COMPLETED"
    assert run["result_state"] == "DEMO_RESULTS_ONLY"
    assert "Synthetic demo inventory" in run["summary"]["user_action"]
    assert run["demo_label"] == "Simulated demo data — not verified hospital inventory."
    by = {c["bank"]["name"]: c for c in cands(client, run_id, h)}
    a = by["JNIMS Hospital Blood Bank"]
    assert a["section"] == "A_ELIGIBLE" and not a["verified_for_search"] and a["rank"] == 1
    assert a["source_type"] == "demo"
    assert a["ranking_tier"] == 4
    assert a["distance_type"] == "straight_line" and a["distance_km"] is not None
    assert a["source_updated_at"] and a["retrieved_at"] and a["route_meters"] is not None
    assert by["RIMS Hospital Blood Bank"]["evidence_state"] == "STALE"                 # TC2
    assert by["RIMS Hospital Blood Bank"]["section"] == "B_UNCERTAIN"
    assert by["Shija Blood Bank and Transfusion Services"]["section"] == "C_INSUFFICIENT"
    assert by["Shija Hospitals and Research Institute"]["evidence_state"] == "UNKNOWN_FRESHNESS"
    ev = client.get(f"/api/evidence/{a['evidence_ids'][0]}", headers=h).json()
    assert ev["source_url"] and ev["raw_record_hash"] and ev["parser_version"]
    assert ev["source_updated_at"] != ev["retrieved_at"]      # two distinct timestamps
    assert all(t["status"] == "SUCCEEDED" for t in run["tasks"][:10])


def test_tc3_platelets_vs_whole_blood(make_client):
    client, _ = make_client()
    run_id, h = start(client, payload(comp="platelets"))
    cs = cands(client, run_id, h)
    assert cs and all(c["section"] == "D_MISMATCH" and "COMPONENT_MISMATCH" in c["reason_codes"] for c in cs)
    assert not any(c["section"] == "A_ELIGIBLE" for c in cs)


def test_tc4_no_match_offers_nearby_without_false_hope(make_client):
    client, _ = make_client()
    run_id, h = start(client, payload(district="Thoubal"))
    run = client.get(f"/api/runs/{run_id}", headers=h).json()
    assert run["result_state"] == "NO_MATCH_FOUND" and run["status"] == "COMPLETED"
    assert "Imphal West" in run["summary"]["suggested_districts"]
    assert "no claim" in run["summary"]["expansion_note"]
    assert run["summary"]["areas_checked"] == ["Thoubal"]


# ---------------------------------------------------------- ranking
def test_ranking_freshness_before_distance(make_client):
    adapter = StaticAdapter([rec("alpha", age_h=6), rec("gamma", age_h=1)])
    client, _ = make_client(stock=adapter)
    run_id, h = start(client, device_payload())
    a = [c for c in cands(client, run_id, h) if c["section"] == "A_ELIGIBLE"]
    # gamma is newer but alpha is closer -> freshness wins
    assert [c["bank"]["name"] for c in a] == ["Shija Blood Bank and Transfusion Services", "JNIMS Hospital Blood Bank"]


def test_ranking_distance_breaks_equal_freshness(make_client):
    adapter = StaticAdapter([rec("delta", age_h=3), rec("gamma", age_h=3)])  # gamma nearer
    client, _ = make_client(stock=adapter)
    run_id, h = start(client, device_payload())
    a = [c for c in cands(client, run_id, h) if c["section"] == "A_ELIGIBLE"]
    assert a[0]["bank"]["name"].endswith("Shija Blood Bank and Transfusion Services")


# ---------------------------------------------------------- failure behaviour
def test_source_timeout_is_not_no_stock_and_retries_are_bounded(make_client):
    adapter = StaticAdapter(exc=SourceUnavailable("t"))
    client, _ = make_client(stock=adapter)
    run_id, h = start(client, payload())
    run = client.get(f"/api/runs/{run_id}", headers=h).json()
    assert adapter.calls == 3
    assert run["result_state"] == "SOURCE_UNAVAILABLE" and run["status"] == "NEEDS_REVIEW"
    t05 = next(t for t in run["tasks"] if t["task_code"] == "T05_FETCH_STOCK")
    assert t05["status"] == "FAILED" and t05["attempt"] == 3 and t05["safe_to_retry"]
    assert all(c["evidence_state"] == "SOURCE_UNAVAILABLE" and c["section"] == "E_UNKNOWN"
               for c in cands(client, run_id, h))
    assert "not evidence of no stock" in run["summary"]["user_action"]


def test_demo_timeout_district(make_client):
    client, _ = make_client()
    run_id, h = start(client, payload(district="Demo Timeout"))
    assert client.get(f"/api/runs/{run_id}", headers=h).json()["result_state"] == "SOURCE_UNAVAILABLE"


def test_blocked_source_is_never_retried(make_client):
    adapter = StaticAdapter(exc=AccessNotPermitted("captcha"))
    client, _ = make_client(stock=adapter)
    run_id, h = start(client, payload())
    run = client.get(f"/api/runs/{run_id}", headers=h).json()
    assert adapter.calls == 1 and run["error_code"] == "ACCESS_NOT_PERMITTED"


def test_live_mode_uses_bulk_e_raktkosh_without_demo_fallback(make_client):
    import httpx
    from app.services.eraktkosh import ERaktKoshAdapter

    payload_rows = [{
        "hospitalname": "Live Source Hospital", "hospitaladd": "Main Road, Imphal West, Manipur",
        "hospitalcontact": "Phone: 7005448827, Fax: 1234567890",
        "hospitalCode": "25002", "entrydate": "2026-10-19 13:00:00",
        "components": {"Whole Blood": {"available_WithQty": "O-Ve : 3",
                                           "not_available_WithQty": "A+Ve : 0"}},
    }]
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=payload_rows)
    adapter = ERaktKoshAdapter(transport=httpx.MockTransport(handler), source_timezone="Asia/Kolkata")
    client, c = make_client(stock_source_mode="live", stock=adapter)
    run_id, h = start(client, payload())
    run = client.get(f"/api/runs/{run_id}", headers=h).json()
    results = cands(client, run_id, h)
    assert len(seen) == 1
    assert dict(seen[0].url.params) == {"stateCode": "14", "componentId": "11"}
    assert run["result_state"] == "ELIGIBLE_FOUND"
    assert results and results[0]["bank"]["name"] == "Live Source Hospital"
    assert results[0]["source_type"] == "live" and results[0]["verified_for_search"] is True
    assert all(candidate["source_type"] != "demo" for candidate in results)
    assert client.get("/health/ready").status_code == 200


def test_unconfigured_inventory_preserves_directory_facilities(make_client):
    from app.schemas import BloodBank

    directory = StaticDirectory([BloodBank(bank_id="directory-1", name="Listed Blood Bank",
                                           address="Main Road", phone="+91 12345 67890",
                                           source_type="live")])
    client, _ = make_client(stock_source_mode="live", stock=StaticAdapter(exc=AccessNotPermitted("mock denied")),
                            directory_source_mode="demo", directory=directory)
    run_id, headers = start(client, payload())
    run = client.get(f"/api/runs/{run_id}", headers=headers).json()
    results = cands(client, run_id, headers)

    assert run["status"] == "NEEDS_REVIEW"
    assert len(results) == 1
    assert results[0]["bank"]["name"] == "Listed Blood Bank"
    assert results[0]["evidence_state"] == "SOURCE_UNAVAILABLE"
    assert results[0]["source_id"] is None


def test_directory_facilities_remain_when_inventory_has_no_matching_rows(make_client):
    from app.schemas import BloodBank

    directory = StaticDirectory([BloodBank(bank_id="directory-2", name="Directory Only Bank",
                                           address="Market Road", source_type="live")])
    client, _ = make_client(stock=StaticAdapter(records=[]), directory=directory)
    run_id, headers = start(client, payload())
    result = cands(client, run_id, headers)[0]

    assert result["bank"]["name"] == "Directory Only Bank"
    assert result["section"] == "E_UNKNOWN"
    assert result["quantity_reported"] is None
    assert result["source_id"] is None
    assert "not enough evidence" in result["display_note"].lower()


def test_backend_issues_idempotency_keys(make_client):
    client, _ = make_client()
    first = client.get("/api/idempotency-key").json()["idempotency_key"]
    second = client.get("/api/idempotency-key").json()["idempotency_key"]
    assert len(first) >= 24 and first != second


def test_denied_gps_needs_location(make_client):
    client, _ = make_client()
    body = payload(mode="device", district=None)
    body["location"]["location_consent"] = False
    run_id, h = start(client, body)
    run = client.get(f"/api/runs/{run_id}", headers=h).json()
    assert run["status"] == "NEEDS_LOCATION"
    assert next(t for t in run["tasks"] if t["task_code"] == "T04_DISCOVER_BANKS")["status"] == "SKIPPED"


def test_route_failure_keeps_match_with_route_unknown(make_client):
    client, _ = make_client(routes=FailingRoutes())
    run_id, h = start(client, device_payload())
    a = next(c for c in cands(client, run_id, h) if c["section"] == "A_ELIGIBLE")
    assert a["route_meters"] is None and a["route_seconds"] is None
    assert "ROUTE_UNKNOWN" in a["reason_codes"]


def test_manual_location_never_fabricates_route(make_client):
    client, c = make_client()
    run_id, h = start(client, payload())
    a = next(x for x in cands(client, run_id, h) if x["section"] == "A_ELIGIBLE")
    assert a["route_meters"] is None and "ROUTE_UNKNOWN" in a["reason_codes"]


def test_coordinates_are_not_persisted(make_client):
    client, c = make_client()
    start(client, device_payload())
    dump = " ".join(str(r.model_dump()) for r in c.store.requests.values())
    assert "24.8" not in dump and "93.94" not in dump
    assert "latitude" not in dump


def test_consented_device_location_adds_facility_distance_without_returning_origin(make_client):
    from app.schemas import BloodBank

    directory = StaticDirectory([
        BloodBank(bank_id="measured-bank", name="Measured Blood Bank", latitude=24.81,
                  longitude=93.94, source_type="live")
    ])
    client, container = make_client(stock_source_mode="live", stock=StaticAdapter(exc=AccessNotPermitted("mock denied")), directory=directory)
    run_id, headers = start(client, device_payload())
    candidate = cands(client, run_id, headers)[0]

    assert candidate["distance_type"] == "straight_line"
    assert candidate["distance_is_approximate"] is True
    assert candidate["distance_km"] == pytest.approx(1.112, abs=0.02)
    assert candidate["ranking_tier"] == 3
    assert "inventory is unknown" in candidate["ranking_explanation"].lower()
    assert "origin" not in candidate
    assert candidate["bank"]["latitude"] == 24.81
    stored_request = next(iter(container.store.requests.values())).model_dump()
    assert "latitude" not in stored_request and "longitude" not in stored_request


def test_manual_district_fallback_keeps_facilities_without_estimated_distance(make_client):
    from app.schemas import BloodBank

    directory = StaticDirectory([
        BloodBank(bank_id="district-bank", name="District Blood Bank", latitude=24.81,
                  longitude=93.94, source_type="live")
    ])
    client, _ = make_client(stock_source_mode="live", stock=StaticAdapter(exc=AccessNotPermitted("mock denied")), directory=directory)
    run_id, headers = start(client, payload())
    candidate = cands(client, run_id, headers)[0]

    assert candidate["bank"]["name"] == "District Blood Bank"
    assert candidate["distance_km"] is None
    assert candidate["distance_type"] is None
    assert "district" in candidate["distance_unavailable_reason"].lower()


# ---------------------------------------------------------- idempotency / validation / auth
def test_duplicate_submit_returns_same_run(make_client):
    client, c = make_client()
    body = payload()
    r1 = client.post("/api/runs", json=body, headers={"Idempotency-Key": "dup-key-0001"})
    r2 = client.post("/api/runs", json=body, headers={"Idempotency-Key": "dup-key-0001"})
    assert r2.headers["Idempotent-Replay"] == "true"
    assert r1.json()["run_id"] == r2.json()["run_id"] and len(c.store.runs) == 1
    r3 = client.post("/api/runs", json=payload(qty=3), headers={"Idempotency-Key": "dup-key-0001"})
    assert r3.status_code == 409 and r3.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_validation_errors_are_field_level(make_client):
    client, _ = make_client()
    bad = payload(group="C+", qty=0)
    r = client.post("/api/runs", json=bad, headers={"Idempotency-Key": "valid-key-001"})
    fields = {f["field"] for f in r.json()["error"]["fields"]}
    assert r.status_code == 422 and {"blood_group", "quantity"} <= fields


def test_patient_fields_rejected(make_client):
    client, c = make_client()
    body = payload(); body["patient_name"] = "X"
    r = client.post("/api/runs", json=body, headers={"Idempotency-Key": "valid-key-002"})
    assert r.status_code == 422 and not c.store.runs


def test_missing_idempotency_key(make_client):
    client, _ = make_client()
    assert client.post("/api/runs", json=payload()).status_code == 400


def test_run_token_required_and_scoped(make_client):
    client, _ = make_client()
    run_id, h = start(client, payload())
    assert client.get(f"/api/runs/{run_id}").status_code == 401
    assert client.get(f"/api/runs/{run_id}", headers={"X-Run-Token": "nope"}).status_code == 404


# ---------------------------------------------------------- approval / outcome

class DistrictLiveAdapter:
    """Deterministic live-source fixture; district contents are test data, not real inventory."""
    source_id = "district-live-test"
    source_display_name = "District live test source"
    source_type = "live"
    source_label = "Test inventory source"
    official_url = "https://example.gov.in/inventory"
    provides_directory = True

    def __init__(self, districts):
        self.districts = districts
        self.calls = []

    def configured(self):
        return True, ""

    async def status(self):
        from app.services.stock_source import SourceStatusInfo
        return SourceStatusInfo("READY", "test fixture")

    async def fetch_stock(self, bank_ids, blood_group, component, district, *, state=None, quantity=None):
        from app.services.stock_source import SourceResult
        self.calls.append(district)
        banks, records = self.districts.get((district or "").casefold(), ([], []))
        return SourceResult(
            source_id=self.source_id,
            source_display_name=self.source_display_name,
            source_type=self.source_type,
            source_label=self.source_label,
            official_url=self.official_url,
            records=list(records),
            banks=list(banks),
            checked_bank_ids=[bank.bank_id for bank in banks],
        )


def _live_area_fixture(*, local_qty=3, neighbor_qty=4, duplicate_neighbor=False, include_second_local=False):
    from app.schemas import BloodBank

    local_bank = BloodBank(bank_id="demo-bank-local", name="Local Test Blood Bank",
                           address="Lamphel, Imphal West, Manipur", district="Imphal West",
                           state="Manipur", source_type="live")
    neighbor_bank = BloodBank(bank_id="demo-bank-neighbor", name="Nearby Test Blood Bank",
                              address="Imphal East, Manipur", district="Imphal East",
                              state="Manipur", source_type="live")
    local_row = rec(bank="local", qty=local_qty, age_h=2, source="district-live-test", rid="local-row")
    neighbor_row = rec(bank="neighbor", qty=neighbor_qty, age_h=2,
                       source="district-live-test", rid="neighbor-row")
    local_row = local_row.model_copy(update={"source_url": "https://example.gov.in/inventory"})
    neighbor_row = neighbor_row.model_copy(update={"source_url": "https://example.gov.in/inventory"})
    local_banks, local_rows = [local_bank], [local_row]
    if include_second_local:
        second_bank = BloodBank(bank_id="demo-bank-local-alt", name="Second Local Test Blood Bank",
                                address="Imphal West, Manipur", district="Imphal West",
                                state="Manipur", source_type="live")
        second_row = rec(bank="local-alt", qty=3, age_h=3, source="district-live-test", rid="local-alt-row")
        second_row = second_row.model_copy(update={"source_url": "https://example.gov.in/inventory"})
        local_banks.append(second_bank)
        local_rows.append(second_row)
    neighbor_rows = [neighbor_row, neighbor_row.model_copy()] if duplicate_neighbor else [neighbor_row]
    return DistrictLiveAdapter({
        "imphal west": (local_banks, local_rows),
        "imphal east": ([neighbor_bank], neighbor_rows),
        "thoubal": ([], []),
        "bishnupur": ([], []),
    })

def _proposal(client, run_id, h, ids=None):
    return client.post(f"/api/runs/{run_id}/proposal", json={"candidate_ids": ids}, headers=h)


def test_ineligible_candidate_ids_cannot_bypass_proposal_filter(make_client):
    client, _ = make_client()
    run_id, headers = start(client, payload(comp="platelets"))
    mismatched = next(candidate for candidate in cands(client, run_id, headers)
                      if candidate["section"] == "D_MISMATCH")
    response = _proposal(client, run_id, headers, [mismatched["candidate_id"]])
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "CANDIDATE_NOT_REVIEWABLE"


def test_live_search_expands_only_when_local_stock_is_insufficient_and_deduplicates(make_client):
    adapter = _live_area_fixture(local_qty=2, neighbor_qty=4, duplicate_neighbor=True)
    client, _ = make_client(stock=adapter, stock_source_mode="live")
    run_id, headers = start(client, payload(qty=5))
    run = client.get(f"/api/runs/{run_id}", headers=headers).json()
    results = cands(client, run_id, headers)

    assert adapter.calls == ["Imphal West", "Imphal East", "Thoubal", "Bishnupur"]
    assert run["summary"]["search_level"] == "expanded_nearest_districts"
    assert run["summary"]["areas_checked"] == ["Imphal West", "Imphal East", "Thoubal", "Bishnupur"]
    assert run["result_state"] == "INSUFFICIENT_STOCK"
    assert len(results) == 2
    assert {item["bank"]["bank_id"] for item in results} == {"demo-bank-local", "demo-bank-neighbor"}
    assert all(item["section"] == "C_INSUFFICIENT" for item in results)
    assert "no single facility" in run["summary"]["user_action"].lower()


def test_nearby_search_runs_when_local_stock_is_split_but_no_bank_meets_request(make_client):
    adapter = _live_area_fixture(local_qty=2, neighbor_qty=4, include_second_local=True)
    client, _ = make_client(stock=adapter, stock_source_mode="live")
    run_id, headers = start(client, payload(qty=5))
    run = client.get(f"/api/runs/{run_id}", headers=headers).json()
    results = cands(client, run_id, headers)

    assert adapter.calls == ["Imphal West", "Imphal East", "Thoubal", "Bishnupur"]
    assert run["summary"]["search_level"] == "expanded_nearest_districts"
    assert {item["bank"]["bank_id"] for item in results} == {
        "demo-bank-local",
        "demo-bank-local-alt",
        "demo-bank-neighbor",
    }
    assert all(item["quantity_reported"] > 0 for item in results)


def test_approval_flow_and_version_invalidation(make_client):
    client, _ = make_client(stock=_live_area_fixture(include_second_local=True), stock_source_mode="live")
    run_id, h = start(client, payload())
    cs = cands(client, run_id, h)
    a_id = next(c["candidate_id"] for c in cs if c["section"] == "A_ELIGIBLE")
    p1 = _proposal(client, run_id, h).json()
    assert p1["proposal_version"] == 1 and "does not place calls" in p1["notice"]
    # same list again -> same proposal, no new version
    assert _proposal(client, run_id, h).json()["approval_id"] == p1["approval_id"]
    # list changes -> v2, v1 can no longer be approved
    p2 = _proposal(client, run_id, h, [a_id]).json()
    assert p2["proposal_version"] == 2
    stale = client.post(f"/api/approvals/{p1['approval_id']}", headers=h,
                        json={"proposal_version": 1, "proposal_hash": p1["proposal_hash"]})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "PROPOSAL_SUPERSEDED"
    wrong = client.post(f"/api/approvals/{p2['approval_id']}", headers=h,
                        json={"proposal_version": 2, "proposal_hash": "0" * 64})
    assert wrong.json()["error"]["code"] == "PROPOSAL_HASH_MISMATCH"
    ok = client.post(f"/api/approvals/{p2['approval_id']}", headers=h,
                     json={"proposal_version": 2, "proposal_hash": p2["proposal_hash"]})
    again = client.post(f"/api/approvals/{p2['approval_id']}", headers=h,
                        json={"proposal_version": 2, "proposal_hash": p2["proposal_hash"]})
    assert ok.json()["status"] == "APPROVED" and again.json()["approved_at"] == ok.json()["approved_at"]
    events = [e["event_type"] for e in client.get(f"/api/runs/{run_id}/audit", headers=h).json()["events"]]
    assert events.count("approval.approved") == 1 and "approval.superseded" in events


def test_outcome_requires_approval_and_is_idempotent(make_client):
    client, c = make_client(stock=_live_area_fixture(include_second_local=True), stock_source_mode="live")
    run_id, h = start(client, payload())
    cid = next(x["candidate_id"] for x in cands(client, run_id, h) if x["section"] == "A_ELIGIBLE")
    body = {"candidate_id": cid, "outcome": "confirmed_available"}
    hk = {**h, "Idempotency-Key": "outcome-key-1"}
    assert client.post(f"/api/runs/{run_id}/outcomes", json=body, headers=hk).status_code == 409
    p = _proposal(client, run_id, h).json()
    client.post(f"/api/approvals/{p['approval_id']}", headers=h,
                json={"proposal_version": 1, "proposal_hash": p["proposal_hash"]})
    r1 = client.post(f"/api/runs/{run_id}/outcomes", json=body, headers=hk)
    r2 = client.post(f"/api/runs/{run_id}/outcomes", json=body, headers=hk)
    assert r1.status_code == 201 and r2.status_code == 200
    assert r1.json()["verification_id"] == r2.json()["verification_id"]
    assert len(c.store.outcomes[run_id]) == 1
    assert "not independently verified" in r1.json()["label"] and r1.json()["outcome_type"] == "user_reported"
    assert client.get(f"/api/runs/{run_id}", headers=h).json()["status"] == "COMPLETED"
    bad = client.post(f"/api/runs/{run_id}/outcomes", headers=hk,
                      json={**body, "note": "patient 9876543210"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "NOTE_REJECTED"


def test_outcome_note_with_number_rejected(make_client):
    client, _ = make_client()
    run_id, h = start(client, payload())
    cid = next(x["candidate_id"] for x in cands(client, run_id, h) if x["section"] == "A_ELIGIBLE")
    r = client.post(f"/api/runs/{run_id}/outcomes", headers={**h, "Idempotency-Key": "outcome-key-2"},
                    json={"candidate_id": cid, "outcome": "other", "note": "call 98765 43210"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "NOTE_REJECTED"


# ---------------------------------------------------------- ops
def test_health_metrics_and_headers(make_client):
    client, _ = make_client(metrics_token="s3cret-token")
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/health/ready").json()["demo_label"] == "Simulated demo data — not verified hospital inventory."
    assert client.get("/metrics").status_code == 404
    assert client.get("/metrics", headers={"Authorization": "Bearer s3cret-token"}).status_code == 200
    r = client.get("/health/live")
    assert r.headers["X-Content-Type-Options"] == "nosniff" and "X-Request-ID" in r.headers


def test_intake_disabled_by_default(make_client):
    client, _ = make_client()
    r = client.post("/api/intake/draft", json={"text": "two units of O negative whole blood"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "LLM_DISABLED"


def test_api_post_rate_limit_and_security_headers(make_client):
    client, _ = make_client(rate_limit_per_minute=1)
    first = client.post("/api/v1/agents/search", json={})
    second = client.post("/api/v1/agents/search", json={})
    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "RATE_LIMITED"
    assert second.headers["x-content-type-options"] == "nosniff"
    assert second.headers["cache-control"] == "no-store"


def test_run_and_idempotency_credentials_are_not_stored_raw(make_client):
    client, container = make_client(token_hash_secret="test-pepper-value")
    body = payload()
    key = "sensitive-idempotency-key"
    response = client.post("/api/runs", json=body, headers={"Idempotency-Key": key})
    assert response.status_code == 202
    result = response.json()
    assert result["run_token"] not in repr(container.store.runs)
    assert key not in repr(container.store.idem_runs)
    assert key not in repr(container.store.requests)
    replay = client.post("/api/runs", json=body, headers={"Idempotency-Key": key})
    assert replay.json()["run_token"] == result["run_token"]


def test_voice_upload_rejects_non_audio_and_keeps_provider_optional(make_client):
    client, _ = make_client()
    invalid = client.post("/api/transcribe", files={"file": ("notes.txt", b"abc", "text/plain")})
    assert invalid.status_code == 415
    unavailable = client.post("/api/transcribe", files={"file": ("note.webm", b"audio", "audio/webm")})
    assert unavailable.status_code == 503
    assert "API key" not in unavailable.text


def test_interactive_api_docs_are_disabled_in_production(make_client):
    client, _ = make_client(app_env="production", token_hash_secret="t" * 32)
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_rate_limit_stays_within_budget_under_concurrent_requests(make_client):
    client, _ = make_client(rate_limit_per_minute=8)
    with ThreadPoolExecutor(max_workers=12) as pool:
        responses = list(pool.map(lambda _: client.post("/api/v1/agents/search", json={}), range(32)))
    assert sum(r.status_code == 200 for r in responses) == 8
    assert sum(r.status_code == 429 for r in responses) == 24


@pytest.mark.asyncio
async def test_streaming_request_without_content_length_is_still_bounded():
    chunks = iter([b"x" * 700, b"x" * 500])
    sent = []

    async def app(scope, receive, send):
        while True:
            message = await receive()
            if not message.get("more_body"):
                return

    async def receive():
        try:
            chunk = next(chunks)
            return {"type": "http.request", "body": chunk, "more_body": True}
        except StopIteration:
            return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    middleware = RequestBodyLimitMiddleware(app, regular_limit=1024, upload_limit=4096)
    await middleware({"type": "http", "path": "/api/search"}, receive, send)
    assert sent[0]["status"] == 413
