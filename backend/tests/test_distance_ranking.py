from datetime import timedelta

import pytest

from app.schemas import (
    BloodBank,
    Candidate,
    EvidenceState,
    MatchState,
    Section,
    utcnow,
)
from app.services.ranking import add_distance_metadata, haversine_distance_km, rank_candidates


def candidate(
    name,
    *,
    source_type="live",
    section=Section.A_ELIGIBLE,
    match_state=MatchState.MATCHED_REPORTED,
    evidence_state=EvidenceState.FRESH,
    updated_hours=2,
    quantity=2,
    latitude=24.80,
    longitude=93.94,
):
    bank = BloodBank(
        bank_id=name.casefold().replace(" ", "-"),
        name=name,
        latitude=latitude,
        longitude=longitude,
        source_type="demo" if source_type == "demo" else "live",
    )
    now = utcnow()
    return Candidate(
        run_id="test-run",
        bank=bank,
        section=section,
        match_state=match_state,
        evidence_state=evidence_state,
        reason_codes=[],
        source_type=source_type,
        source_updated_at=now - timedelta(hours=updated_hours) if updated_hours is not None else None,
        quantity_reported=quantity,
    )


def rank(items, *, origin=(24.80, 93.94), location_mode="device", now=None, **overrides):
    now = now or utcnow()
    add_distance_metadata(items, origin=origin, location_mode=location_mode)
    options = {
        "now": now,
        "requested_quantity": 2,
        "freshness_hours": 24,
        "distance_scale_km": 100,
        "freshness_weight": 0.5,
        "quantity_weight": 0.3,
        "distance_weight": 0.2,
        **overrides,
    }
    rank_candidates(items, **options)
    return sorted(items, key=lambda item: item.rank or 0)


def test_evidence_tiers_precede_distance_and_fresh_sufficient_uses_distance_as_tiebreaker():
    nearby = candidate("Nearby confirmed", latitude=24.801, longitude=93.941)
    distant = candidate("Distant confirmed", latitude=25.8, longitude=93.94)
    nearby_unknown = candidate(
        "Nearby unknown",
        section=Section.E_UNKNOWN,
        match_state=MatchState.UNKNOWN,
        evidence_state=None,
        updated_hours=None,
        quantity=None,
        latitude=24.8001,
        longitude=93.9401,
    )

    ranked = rank([distant, nearby_unknown, nearby])

    assert [item.bank.name for item in ranked[:2]] == ["Nearby confirmed", "Distant confirmed"]
    assert ranked[2].bank.name == "Nearby unknown"
    assert ranked[2].ranking_tier == 3
    assert "unknown" in ranked[2].ranking_explanation.lower()


def test_fresh_partial_is_after_confirmed_sufficient_and_before_stale():
    confirmed = candidate("Confirmed", latitude=24.801, longitude=93.941)
    partial = candidate(
        "Partial",
        section=Section.C_INSUFFICIENT,
        match_state=MatchState.INSUFFICIENT,
        quantity=1,
        latitude=24.8001,
        longitude=93.9401,
    )
    stale = candidate(
        "Stale",
        section=Section.B_UNCERTAIN,
        evidence_state=EvidenceState.STALE,
        updated_hours=72,
        latitude=24.8001,
        longitude=93.9401,
    )

    ranked = rank([stale, partial, confirmed])

    assert [item.bank.name for item in ranked] == ["Confirmed", "Partial", "Stale"]
    assert [item.ranking_tier for item in ranked] == [1, 2, 3]


def test_synthetic_inventory_never_outranks_live_unknown_stock():
    synthetic = candidate("Synthetic", source_type="demo")
    unknown_live = candidate(
        "Unknown live",
        section=Section.E_UNKNOWN,
        match_state=MatchState.UNKNOWN,
        evidence_state=None,
        updated_hours=None,
        quantity=None,
    )

    ranked = rank([synthetic, unknown_live])

    assert [item.bank.name for item in ranked] == ["Unknown live", "Synthetic"]
    assert ranked[1].ranking_tier == 4
    assert "demo-only" in ranked[1].ranking_explanation.lower()


def test_haversine_rejects_missing_or_invalid_origin_and_facility_coordinates():
    assert haversine_distance_km(None, (24.8, 93.9)) is None
    assert haversine_distance_km((91, 0), (24.8, 93.9)) is None
    assert haversine_distance_km((24.8, 93.9), (24.8, 181)) is None

    missing = candidate("Missing coordinates", latitude=None, longitude=None)
    invalid_bank = BloodBank.model_construct(
        bank_id="invalid-coordinate-bank",
        name="Invalid coordinates",
        latitude=120,
        longitude=190,
        source_type="live",
    )
    invalid = candidate("Placeholder")
    invalid.bank = invalid_bank
    items = [missing, invalid]

    ranked = rank(items)

    assert len(ranked) == 2
    assert all(item.distance_km is None for item in ranked)
    assert all(item.distance_type is None for item in ranked)
    assert all(item.distance_unavailable_reason for item in ranked)


def test_district_search_does_not_use_a_centroid_or_claim_an_exact_distance():
    item = candidate("District result")
    ranked = rank([item], origin=None, location_mode="manual")

    assert ranked[0].distance_km is None
    assert ranked[0].distance_unavailable_reason
    assert "district" in ranked[0].distance_unavailable_reason.lower()


def test_sorting_is_deterministic_and_weights_are_configurable():
    first = candidate("One", latitude=24.801, longitude=93.941)
    second = candidate("Two", latitude=24.802, longitude=93.942)
    options = {"freshness_weight": 0.7, "quantity_weight": 0.2, "distance_weight": 0.1}

    result_a = rank([second.model_copy(deep=True), first.model_copy(deep=True)], **options)
    result_b = rank([first.model_copy(deep=True), second.model_copy(deep=True)], **options)

    assert [(c.bank.bank_id, c.rank, c.ranking_score) for c in result_a] == [
        (c.bank.bank_id, c.rank, c.ranking_score) for c in result_b
    ]


@pytest.mark.parametrize(
    "weights",
    [
        {"freshness_weight": 0, "quantity_weight": 0, "distance_weight": 0},
    ],
)
def test_ranking_function_rejects_all_zero_weight_configuration(weights):
    item = candidate("One")
    with pytest.raises(ValueError, match="At least one ranking weight"):
        rank([item], **weights)
