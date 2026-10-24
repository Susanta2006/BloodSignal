"""Repeatable, explicitly labelled Ziro Digital Blood Availability Finder cases.

All inventory in these tests is synthetic and must never be represented as live stock.
"""
from __future__ import annotations

from tests.conftest import cands, device_payload, payload, start


def test_tc1_o_negative_district_ranking_contacts_and_original_timestamps(make_client):
    """TC1: rank O-negative results and retain contact/timestamp fields without inventing data."""
    client, _ = make_client()
    run_id, headers = start(client, device_payload(group="O-", comp="whole_blood", district="Imphal West"))
    run = client.get(f"/api/runs/{run_id}", headers=headers).json()
    results = cands(client, run_id, headers)
    alpha = next(c for c in results if c["bank"]["name"] == "JNIMS Hospital Blood Bank")

    assert run["demo_label"] == "Simulated demo data — not verified hospital inventory."
    assert alpha["section"] == "A_ELIGIBLE"
    assert alpha["rank"] == 2
    gamma = next(c for c in results if c["bank"]["name"] == "Shija Blood Bank and Transfusion Services")
    assert gamma["rank"] == 1
    assert alpha["source_updated_at"] == "2026-10-19T06:00:00Z"
    assert alpha["retrieved_at"] == "2026-10-19T10:00:00Z"
    # Contact keys must be exposed, but synthetic records intentionally have no dialable phone.
    assert "phone" in alpha["bank"]
    assert alpha["bank"]["phone"] == "0385-2443142"
    assert alpha["bank"]["latitude"] is not None
    assert alpha["bank"]["longitude"] is not None
    assert alpha["source_type"] == "demo"  # inventory is simulated even at a real facility
    assert alpha["evidence_ids"]


def test_tc2_three_day_old_stock_is_stale_and_original_timestamp_preserved(make_client):
    """TC2: 72-hour evidence is stale; retrieval must not rewrite source_updated_at."""
    client, _ = make_client()
    run_id, headers = start(client, payload(group="O-", comp="whole_blood", district="Imphal West"))
    beta = next(c for c in cands(client, run_id, headers)
                if c["bank"]["name"] == "RIMS Hospital Blood Bank")

    assert beta["evidence_state"] == "STALE"
    assert beta["section"] == "B_UNCERTAIN"
    assert beta["source_updated_at"] == "2026-10-16T10:00:00Z"
    assert beta["retrieved_at"] == "2026-10-19T10:00:00Z"


def test_tc3_platelets_request_never_matches_whole_blood(make_client):
    """TC3: component mismatch is deterministic and fail-closed."""
    client, _ = make_client()
    run_id, headers = start(client, payload(group="O-", comp="platelets", district="Imphal West"))
    results = cands(client, run_id, headers)

    assert results
    assert all(c["section"] == "D_MISMATCH" for c in results)
    assert all("COMPONENT_MISMATCH" in c["reason_codes"] for c in results)
    assert not any(c["section"] == "A_ELIGIBLE" for c in results)


def test_tc4_no_stock_offers_nearby_districts_without_claiming_availability(make_client):
    """TC4: nearby districts are search suggestions, never inferred inventory."""
    client, _ = make_client()
    run_id, headers = start(client, payload(group="O-", comp="whole_blood", district="Thoubal"))
    run = client.get(f"/api/runs/{run_id}", headers=headers).json()

    assert run["result_state"] == "NO_MATCH_FOUND"
    assert run["status"] == "COMPLETED"
    assert run["summary"]["areas_checked"] == ["Thoubal"]
    assert run["summary"]["suggested_districts"]
    assert "no claim" in run["summary"]["expansion_note"].lower()
    assert "Imphal West" in run["summary"]["suggested_districts"]
