"""Deterministic final gate for every inventory response (including agent-composed ones).

Fails CLOSED: a response that violates an invariant is downgraded to UNAVAILABLE; a bad row is
suppressed with a visible warning. Nothing here trusts upstream layers or any LLM.
"""
from __future__ import annotations

import logging
from datetime import datetime

from app.inventory.models import (ErrorOut, InventoryResponse, Pagination, QualityFlag as Q,
                                  SourceStatus as S, StockOut)

log = logging.getLogger("inventory.safety")
_LIVE_LIKE = {S.LIVE, S.CACHED, S.STALE}


def response_violations(r: InventoryResponse, now: datetime) -> list[str]:
    v: list[str] = []
    if r.is_demo != (r.source_status is S.DEMO) and r.source_status is not S.UNAVAILABLE:
        v.append("DEMO_FLAG_STATUS_MISMATCH")
    if r.source_status in _LIVE_LIKE:
        if r.is_demo:
            v.append("DEMO_LABELLED_LIVE")
        if r.fetched_at is None:
            v.append("MISSING_FETCHED_AT")
    if r.source_status is S.STALE and (not r.is_stale or r.stale_age_seconds is None):
        v.append("STALE_WITHOUT_AGE")
    if r.source_status is not S.STALE and r.is_stale:
        v.append("STALE_FLAG_WRONG_STATUS")
    if r.source_status is S.CACHED and (r.expires_at is None or r.expires_at <= now):
        v.append("EXPIRED_CACHE_LABELLED_CURRENT")
    if r.source_status is S.UNAVAILABLE and r.data:
        v.append("DATA_WITH_UNAVAILABLE")
    return v


def row_violations(s: StockOut) -> list[str]:
    v: list[str] = []
    if s.quantity is None and s.quantity_state != "UNKNOWN":
        v.append("UNKNOWN_QUANTITY_NOT_MARKED")
    if s.quantity is not None and (s.quantity_state != "REPORTED" or s.quantity < 0):
        v.append("BAD_QUANTITY")
    if s.source_updated_at is None and (s.source_age_seconds is not None or s.source_freshness != "UNKNOWN"):
        v.append("INVENTED_SOURCE_FRESHNESS")
    if s.source_updated_at is not None and s.source_age_seconds is None \
            and Q.FUTURE_SOURCE_TIMESTAMP.value not in s.flags:
        v.append("SOURCE_AGE_MISSING")
    if not s.evidence_ref:
        v.append("MISSING_EVIDENCE_REF")
    return v


def enforce(r: InventoryResponse, now: datetime) -> tuple[InventoryResponse, int]:
    rv = response_violations(r, now)
    if rv:
        log.error("response_violation codes=%s", ",".join(rv))
        return r.model_copy(update={
            "data": [], "source_status": S.UNAVAILABLE, "cache_status": "MISS", "is_stale": False,
            "is_demo": False, "stale_age_seconds": None, "fetched_at": None, "expires_at": None,
            "pagination": Pagination(page=r.pagination.page, page_size=r.pagination.page_size, total=0),
            "error": ErrorOut(code="SAFETY_CHECK_FAILED", message="Response failed integrity checks.",
                              retryable=True)}), 1
    suppressed, items = 0, []
    for it in r.data:
        keep = []
        for s in it.stock:
            bad = row_violations(s)
            if bad:
                suppressed += 1
                log.error("row_suppressed codes=%s", ",".join(bad))
            else:
                keep.append(s)
        items.append(it.model_copy(update={"stock": keep}))
    if suppressed:
        r = r.model_copy(update={"data": items, "warnings": r.warnings + [
            f"{suppressed} stock row(s) were withheld because they failed integrity checks."]})
    return r, suppressed
