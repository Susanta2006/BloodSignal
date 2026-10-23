"""Deterministic matching, freshness, conflict and ranking rules. No LLM, no I/O."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import logging
import math

from app.schemas import (BloodGroup, Component, EvidenceRecord, EvidenceState, MatchState,
                         QuantityUnit, Section, StoredRequest)

RULE_VERSION = "match-rules-1.1.0"
log = logging.getLogger("matching")
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
_QUANTITY_FIELDS = ("available_units", "reported_units", "units", "quantity")


def _record_value(record, field_name: str):
    return record.get(field_name) if isinstance(record, dict) else getattr(record, field_name, None)


def _number(value) -> float | None:
    """Parse a stock count without accepting booleans, NaN, infinity or malformed text."""
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _reported_units(record) -> float | None:
    """Resolve a reported stock count conservatively across legacy/current schemas.

    Precedence for conflicting *positive* reports is available_units, reported_units,
    units, then quantity. An explicit zero/negative value in any supported field wins over
    positive values in other fields: it may be a newer depletion report while another field
    is stale. A malformed value in the highest-priority populated field is UNKNOWN rather
    than permission to fall back to a lower-priority, potentially stale value. Null fields
    are treated as absent. The input record is never mutated.
    """
    raw_values: list[tuple[str, object, float | None]] = []
    for field_name in _QUANTITY_FIELDS:
        raw = _record_value(record, field_name)
        if raw is not None:
            raw_values.append((field_name, raw, _number(raw)))

    # Fail closed if any explicit quantity says the stock is depleted or negative.
    for _field_name, _raw, parsed in raw_values:
        if parsed is not None and parsed <= 0:
            return parsed

    # Use the best-ranked populated field; malformed authoritative data stays unknown.
    if raw_values:
        return raw_values[0][2]
    return None


def _as_value(value):
    return getattr(value, "value", value)


def _record_identifier(record, index: int) -> str:
    for name in ("bank_id", "id", "facility_id", "code"):
        value = _record_value(record, name)
        if value is not None and str(value).strip():
            return str(value).strip()[:80]
    return f"index:{index}"


def match_blood_inventory(records, *args, **kwargs):
    """Return only known positive-stock records that match supplied request constraints.

    Supported optional request inputs are ``blood_group``/``requested_blood_group``,
    ``component``/``requested_component``, and ``required_units``/``requested_units``.
    A request object may also be passed as the first positional argument or ``request=``.
    When a constraint is omitted, the helper preserves its historical positive-stock-only
    behavior; the primary application pipeline uses ``match_bank`` with a typed request.
    """
    request = kwargs.get("request") or kwargs.get("req")
    if request is None and args and any(hasattr(args[0], key) for key in ("blood_group", "component")):
        request = args[0]

    target_group = kwargs.get("blood_group", kwargs.get("requested_blood_group"))
    target_component = kwargs.get("component", kwargs.get("requested_component"))
    required = kwargs.get("required_units", kwargs.get("requested_units", kwargs.get("quantity")))
    if request is not None:
        target_group = target_group if target_group is not None else getattr(request, "blood_group", None)
        target_component = target_component if target_component is not None else getattr(request, "component", None)
        required = required if required is not None else getattr(request, "quantity", None)

    group_was_supplied = target_group is not None
    component_was_supplied = target_component is not None
    target_group = norm_group(str(_as_value(target_group))) if group_was_supplied else None
    target_component = norm_component(str(_as_value(target_component))) if component_was_supplied else None
    if (group_was_supplied and target_group is None) or (component_was_supplied and target_component is None):
        return []
    required_units = _number(required) if required is not None else None
    if required is not None and (required_units is None or required_units < 1):
        log.debug("inventory_filter_excluded request reason=invalid_required_quantity")
        return []
    if target_group is not None and target_group not in _GROUPS:
        return []
    if target_component is not None and target_component not in _COMPONENTS:
        return []

    matched = []
    for index, record in enumerate(records or []):
        quantity = _reported_units(record)
        reason = None
        if quantity is None:
            reason = "stock_unknown"
        elif quantity <= 0:
            reason = "stock_non_positive"
        elif required_units is not None and quantity < required_units:
            reason = "insufficient_quantity"

        if reason is None and target_group is not None:
            actual_group = norm_group(str(_as_value(
                _record_value(record, "blood_group") or _record_value(record, "bloodGroup")
                or _record_value(record, "group") or "")))
            if actual_group != target_group:
                reason = "blood_group_mismatch_or_unknown"
        if reason is None and target_component is not None:
            actual_component = norm_component(str(_as_value(
                _record_value(record, "component") or _record_value(record, "blood_component") or "")))
            if actual_component != target_component:
                reason = "component_mismatch_or_unknown"

        if reason is None:
            matched.append(record)
        else:
            log.debug("inventory_record_excluded record_id=%s reason=%s",
                      _record_identifier(record, index), reason)
    return matched


def match_bank(bank_id: str, records: list[EvidenceRecord], req: StoredRequest) -> BankMatch:
    # Keep zero reports in the evidence set: dropping them here would hide a current depletion
    # report or erase a same-timestamp disagreement with a positive report from the same source.
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
    if sel.quantity == 0:
        return m(MatchState.INSUFFICIENT, ["ZERO_STOCK", "INSUFFICIENT_QUANTITY"])
    if sel.quantity < 0:
        return m(MatchState.UNKNOWN, ["INVALID_NEGATIVE_QUANTITY"], ["quantity"])
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
    if "ZERO_STOCK" in d.reason_codes and d.selected:
        return "The source reports zero available units for this request; this is not a stock match."
    if "STATUS_NOT_AVAILABLE" in d.reason_codes:
        return "The source explicitly reports this stock as unavailable."
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
