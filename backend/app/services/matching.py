"""Deterministic matching, freshness, conflict and ranking rules. No LLM, no I/O."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.schemas import (BloodGroup, Component, EvidenceRecord, EvidenceState, MatchState,
                         QuantityUnit, Section, StoredRequest)

RULE_VERSION = "match-rules-1.0.0"
CLOCK_SKEW = timedelta(minutes=5)
_MIN = datetime.min.replace(tzinfo=timezone.utc)

_GROUPS = {g.value for g in BloodGroup}
_COMPONENTS = {c.value for c in Component}
_UNITS = {u.value for u in QuantityUnit}


# Strict normalisers: unknown input -> None. No synonym guessing, no unit conversion.
def norm_group(v: str | None) -> str | None:
    v = (v or "").strip().upper()
    return v if v in _GROUPS else None


def norm_component(v: str | None) -> str | None:
    v = (v or "").strip().lower()
    return v if v in _COMPONENTS else None


def norm_unit(v: str | None) -> str | None:
    v = (v or "").strip().lower()
    return v if v in _UNITS else None


@dataclass
class BankMatch:
    bank_id: str
    state: MatchState
    codes: list[str]
    unresolved: list[str] = field(default_factory=list)
    considered: list[EvidenceRecord] = field(default_factory=list)
    selected: EvidenceRecord | None = None
    conflict: bool = False


@dataclass
class Decision:
    bank_id: str
    section: Section
    match_state: MatchState
    evidence_state: EvidenceState | None
    reason_codes: list[str]
    unresolved_fields: list[str]
    evidence_ids: list[str]
    selected: EvidenceRecord | None


def _ts_key(r: EvidenceRecord) -> datetime:
    return r.source_updated_at or _MIN


# ------------------------------------------------------------ T07: matching
def match_bank(bank_id: str, records: list[EvidenceRecord], req: StoredRequest) -> BankMatch:
    want_g, want_c, want_u = req.blood_group.value, req.component.value, req.quantity_unit.value
    exact = [r for r in records
             if norm_group(r.blood_group) == want_g and norm_component(r.component) == want_c]

    if not exact:
        if any(norm_group(r.blood_group) is None or norm_component(r.component) is None
               for r in records):
            return BankMatch(bank_id, MatchState.UNKNOWN, ["FIELD_UNRECOGNIZED"],
                             ["blood_group_or_component"], records)
        codes = []
        if not any(norm_component(r.component) == want_c for r in records):
            codes.append("COMPONENT_MISMATCH")
        if not any(norm_group(r.blood_group) == want_g for r in records):
            codes.append("GROUP_MISMATCH")
        return BankMatch(bank_id, MatchState.NOT_MATCHED, codes or ["COMPONENT_MISMATCH"], [], records)

    ordered = sorted(exact, key=_ts_key, reverse=True)
    sel = ordered[0]

    # Conflict = the newest report(s) from each source disagree. This includes two rows from the
    # SAME source carrying the same timestamp but different values (we never pick one silently).
    newest_by_source: dict[str, list[EvidenceRecord]] = {}
    for r in ordered:
        group = newest_by_source.setdefault(r.source_id, [])
        if not group or _ts_key(group[0]) == _ts_key(r):
            group.append(r)
    sig = {(r.reported_status, r.quantity, norm_unit(r.quantity_unit))
           for rows in newest_by_source.values() for r in rows}
    conflict = len(sig) > 1

    base = ["GROUP_MATCH", "COMPONENT_MATCH"]
    m = lambda state, codes, unresolved=(): BankMatch(  # noqa: E731
        bank_id, state, base + codes, list(unresolved), exact, sel, conflict)

    if not sel.source_id or not (sel.source_url or sel.source_record_id):
        return m(MatchState.UNKNOWN, ["PROVENANCE_INCOMPLETE"], ["source_identity"])
    unit = norm_unit(sel.quantity_unit)
    if unit is None or unit != want_u:
        return m(MatchState.UNKNOWN, ["UNIT_UNKNOWN"], ["quantity_unit"])
    if sel.quantity is None:
        return m(MatchState.UNKNOWN, ["QUANTITY_UNREPORTED"], ["quantity"])
    if sel.reported_status == "unavailable":
        return m(MatchState.NOT_MATCHED, ["STATUS_NOT_AVAILABLE"])
    if sel.reported_status != "available":
        return m(MatchState.UNKNOWN, ["STATUS_UNREPORTED"], ["reported_status"])
    if sel.quantity < req.quantity:
        return m(MatchState.INSUFFICIENT, ["UNIT_COMPATIBLE", "INSUFFICIENT_QUANTITY"])
    return m(MatchState.MATCHED_REPORTED,
             ["QUANTITY_SUFFICIENT", "UNIT_COMPATIBLE", "SOURCE_IDENTIFIED"])


# ------------------------------------------------------------ T08: freshness
def decide(match: BankMatch, now: datetime, freshness_hours: int) -> Decision:
    codes = list(match.codes)
    unresolved = list(match.unresolved)
    ev_state: EvidenceState | None = None

    if match.selected is not None:
        ts = match.selected.source_updated_at
        if ts is None:
            ev_state = EvidenceState.UNKNOWN_FRESHNESS
            codes.append("TIMESTAMP_MISSING"); unresolved.append("source_updated_at")
        elif ts > now + CLOCK_SKEW:
            ev_state = EvidenceState.UNKNOWN_FRESHNESS
            codes.append("TIMESTAMP_IN_FUTURE"); unresolved.append("source_updated_at")
        elif now - ts > timedelta(hours=freshness_hours):
            ev_state = EvidenceState.STALE
            codes += ["SOURCE_TIMESTAMP_PRESENT", "STALE_SOURCE"]
        else:
            ev_state = EvidenceState.FRESH
            codes += ["SOURCE_TIMESTAMP_PRESENT", "FRESHNESS_WITHIN_POLICY"]
        if match.conflict:
            ev_state = EvidenceState.CONFLICTING_REPORTS
            codes.append("SOURCE_CONFLICT")

    section = {
        MatchState.INSUFFICIENT: Section.C_INSUFFICIENT,
        MatchState.NOT_MATCHED: Section.D_MISMATCH,
        MatchState.UNKNOWN: Section.E_UNKNOWN,
    }.get(match.state)
    if section is None:  # MATCHED_REPORTED
        section = Section.A_ELIGIBLE if ev_state == EvidenceState.FRESH else Section.B_UNCERTAIN

    return Decision(match.bank_id, section, match.state, ev_state, codes, unresolved,
                    [r.evidence_id for r in match.considered], match.selected)


def unavailable_decision(bank_id: str, reason: str = "SOURCE_UNAVAILABLE") -> Decision:
    return Decision(bank_id, Section.E_UNKNOWN, MatchState.UNKNOWN,
                    EvidenceState.SOURCE_UNAVAILABLE, [reason], ["stock_source"], [], None)


def no_inventory_decision(bank_id: str) -> Decision:
    """Keep a directory facility visible when no inventory record supports a claim."""
    return Decision(bank_id, Section.E_UNKNOWN, MatchState.UNKNOWN, None,
                    ["NO_INVENTORY_EVIDENCE"], ["inventory"], [], None)


# ------------------------------------------------------------ ranking
def sort_key_eligible(d: Decision, route_m: int | None, route_s: int | None, completeness: int):
    """Freshness first, then driving distance, then driving time, then completeness."""
    ts = d.selected.source_updated_at if d.selected and d.selected.source_updated_at else _MIN
    return (-ts.timestamp(), route_m if route_m is not None else float("inf"),
            route_s if route_s is not None else float("inf"), -completeness)


# ------------------------------------------------------------ copy
def human_age(delta: timedelta) -> str:
    hours = int(delta.total_seconds() // 3600)
    if hours < 1:
        return "less than 1 hour ago"
    if hours < 48:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    return f"{hours // 24} days ago"


def display_note(d: Decision, req: StoredRequest, now: datetime) -> str:
    age = human_age(now - d.selected.source_updated_at) if (
        d.selected and d.selected.source_updated_at) else None
    if d.evidence_state == EvidenceState.SOURCE_UNAVAILABLE:
        return "We could not check this source; this is not evidence of no stock."
    if d.evidence_state == EvidenceState.CONFLICTING_REPORTS:
        return "Sources disagree; call the blood bank before travel."
    if d.section == Section.C_INSUFFICIENT and d.selected:
        return (f"Source reports {d.selected.quantity:g} {d.selected.quantity_unit}, "
                f"fewer than the {req.quantity} requested.")
    if d.section == Section.D_MISMATCH:
        return "Source record is for a different blood group or component; not a match."
    if d.section == Section.E_UNKNOWN:
        return "Not enough evidence in the source to match this request."
    if d.evidence_state == EvidenceState.STALE:
        return f"Last source update: {age}. Do not rely on this as current stock."
    if d.evidence_state == EvidenceState.UNKNOWN_FRESHNESS:
        return "The source does not provide a usable update time."
    return f"Source updated {age}. Call to reconfirm."
