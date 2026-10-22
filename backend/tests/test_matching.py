from datetime import timedelta

from app.schemas import (BloodGroup, Component, EvidenceRecord, EvidenceState, MatchState,
                         QuantityUnit, Section, StoredRequest)
from app.safety import verify_eligible
from app.services.matching import decide, match_bank
from tests.conftest import NOW, rec


def req(group="O-", comp="whole_blood", qty=2):
    return StoredRequest(request_id="r", blood_group=BloodGroup(group), component=Component(comp),
                         quantity=qty, quantity_unit=QuantityUnit.UNITS, location_mode="manual",
                         district="d", state="s", country="IN", location_consent=False,
                         include_nearby_districts=True, max_radius_km=50, idempotency_key="k",
                         created_at=NOW)


def ev(**kw):
    return EvidenceRecord(**rec(**kw).model_dump(), run_id="run", bank_id="b")


def dec(records, **rq):
    return decide(match_bank("b", records, req(**rq)), NOW, 24)


def test_tc1_exact_fresh_match():
    d = dec([ev(age_h=4)])
    assert (d.section, d.match_state, d.evidence_state) == (
        Section.A_ELIGIBLE, MatchState.MATCHED_REPORTED, EvidenceState.FRESH)
    assert {"GROUP_MATCH", "COMPONENT_MATCH", "QUANTITY_SUFFICIENT", "FRESHNESS_WITHIN_POLICY"} <= set(d.reason_codes)


def test_tc2_three_day_old_is_stale_not_eligible():
    d = dec([ev(age_h=72)])
    assert d.evidence_state == EvidenceState.STALE and d.section == Section.B_UNCERTAIN
    assert "STALE_SOURCE" in d.reason_codes


def test_freshness_boundary():
    assert dec([ev(age_h=24)]).section == Section.A_ELIGIBLE      # exactly at threshold: not stale
    assert dec([ev(age_h=25)]).section == Section.B_UNCERTAIN


def test_tc3_platelets_not_matched_by_whole_blood():
    d = dec([ev(comp="whole_blood")], comp="platelets")
    assert d.match_state == MatchState.NOT_MATCHED and d.section == Section.D_MISMATCH
    assert "COMPONENT_MISMATCH" in d.reason_codes


def test_group_never_substituted():
    d = dec([ev(group="O+")])
    assert d.section == Section.D_MISMATCH and "GROUP_MISMATCH" in d.reason_codes


def test_missing_timestamp_is_unknown_freshness():
    d = dec([ev(age_h=None)])
    assert d.evidence_state == EvidenceState.UNKNOWN_FRESHNESS and d.section == Section.B_UNCERTAIN


def test_future_timestamp_not_fresh():
    d = dec([ev(age_h=-3)])
    assert d.evidence_state == EvidenceState.UNKNOWN_FRESHNESS and "TIMESTAMP_IN_FUTURE" in d.reason_codes


def test_insufficient_quantity():
    d = dec([ev(qty=1)])
    assert d.match_state == MatchState.INSUFFICIENT and d.section == Section.C_INSUFFICIENT


def test_unknown_unit_no_conversion():
    d = dec([ev(unit="bags")])
    assert d.match_state == MatchState.UNKNOWN and "UNIT_UNKNOWN" in d.reason_codes
    assert dec([ev(unit="ml")]).match_state == MatchState.UNKNOWN   # ml != units, never converted


def test_unreported_status_is_unknown():
    assert dec([ev(status=None)]).match_state == MatchState.UNKNOWN
    assert dec([ev(status="unavailable")]).match_state == MatchState.NOT_MATCHED


def test_conflict_keeps_both_records_and_is_not_eligible():
    d = dec([ev(qty=5, source="s1", rid="a"), ev(qty=0, status="unavailable", source="s2", rid="b")])
    assert d.evidence_state == EvidenceState.CONFLICTING_REPORTS and d.section != Section.A_ELIGIBLE
    assert len(d.evidence_ids) == 2


def test_unrecognised_group_is_unknown_not_mismatch():
    d = dec([ev(group="Rh-null")])
    assert d.match_state == MatchState.UNKNOWN and "FIELD_UNRECOGNIZED" in d.reason_codes


def test_verifier_independently_rejects_tampered_decision():
    d = dec([ev(age_h=4)])
    assert verify_eligible(d, req(), NOW, 24) == []
    d.selected.component = "platelets"            # simulate matcher bug
    assert "VERIFY_COMPONENT_MISMATCH" in verify_eligible(d, req(), NOW, 24)
    d2 = dec([ev(age_h=4)])
    d2.selected.source_updated_at = NOW - timedelta(hours=90)
    assert "VERIFY_STALE" in verify_eligible(d2, req(), NOW, 24)


def test_note_check_digit_runs():
    import pytest
    from app.safety import check_note
    from app.schemas import AppError
    for bad in ("9876543210", "call 98765 43210", "id 1234-5678-9012"):
        with pytest.raises(AppError):
            check_note(bad)
    assert check_note("spoke at 2026-10-19, 2 units held") == "spoke at 2026-10-19, 2 units held"


def test_same_source_same_timestamp_disagreement_is_conflict_not_silent_pick():
    d = dec([ev(qty=5, source="s1", rid="a"), ev(qty=0, status="unavailable", source="s1", rid="b")])
    assert d.evidence_state == EvidenceState.CONFLICTING_REPORTS and d.section != Section.A_ELIGIBLE


def test_same_source_newer_report_supersedes_older():
    d = dec([ev(qty=0, status="unavailable", age_h=30, source="s1", rid="old"),
             ev(qty=5, age_h=2, source="s1", rid="new")])
    assert d.section == Section.A_ELIGIBLE and len(d.evidence_ids) == 2
