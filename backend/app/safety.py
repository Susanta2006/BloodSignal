"""Domain safety: independent verifier, copy constants, input hygiene.

The verifier deliberately does NOT call matching.py helpers. It re-checks the raw
fields so a bug in the matcher cannot silently approve its own output.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from app.schemas import AppError, EvidenceState, MatchState, Section, StoredRequest
from app.services.matching import Decision

DEMO_LABEL = "Simulated demo data — not verified hospital inventory."
DISCLAIMER = (
    "Availability information is source-reported and may be outdated. This service does not "
    "confirm clinical suitability or guarantee stock. Call the blood bank to reconfirm "
    "availability, component, quantity and requirements before travelling. Follow your "
    "treating clinician and the blood bank's instructions."
)

_DIGIT_RUN = re.compile(r"\d[\d\s-]*\d")  # candidate phone/Aadhaar/MRN-like runs


def verify_eligible(d: Decision, req: StoredRequest, now: datetime, freshness_hours: int) -> list[str]:
    """Return violations for a decision placed in section A. Empty list == verified."""
    v: list[str] = []
    r = d.selected
    if d.section != Section.A_ELIGIBLE or d.match_state != MatchState.MATCHED_REPORTED:
        v.append("NOT_IN_ELIGIBLE_SECTION")
    if r is None:
        return v + ["NO_EVIDENCE_RECORD"]
    if (r.blood_group or "").strip().upper() != req.blood_group.value:
        v.append("VERIFY_GROUP_MISMATCH")
    if (r.component or "").strip().lower() != req.component.value:
        v.append("VERIFY_COMPONENT_MISMATCH")
    if (r.quantity_unit or "").strip().lower() != req.quantity_unit.value:
        v.append("VERIFY_UNIT_MISMATCH")
    if r.quantity is None or r.quantity < req.quantity:
        v.append("VERIFY_QUANTITY")
    if r.reported_status != "available":
        v.append("VERIFY_STATUS")
    if not r.source_id or not (r.source_url or r.source_record_id):
        v.append("VERIFY_PROVENANCE")
    if r.source_updated_at is None:
        v.append("VERIFY_TIMESTAMP_MISSING")
    elif now - r.source_updated_at > timedelta(hours=freshness_hours):
        v.append("VERIFY_STALE")
    elif r.source_updated_at > now + timedelta(minutes=5):
        v.append("VERIFY_TIMESTAMP_FUTURE")
    if d.evidence_state != EvidenceState.FRESH:
        v.append("VERIFY_EVIDENCE_STATE")
    return v


def check_note(note: str | None) -> str | None:
    """Reject notes that look like they carry patient/contact identifiers."""
    if note is None:
        return None
    note = note.strip()
    if any(sum(ch.isdigit() for ch in m.group()) >= 10 for m in _DIGIT_RUN.finditer(note)):
        raise AppError("NOTE_REJECTED", "Notes must not contain phone, ID or record numbers.",
                       status_code=422, user_action="Remove the number and keep the note short.")
    return note or None
