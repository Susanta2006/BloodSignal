"""Inventory domain models: statuses, stored rows, query, response envelope.

Semantics that matter (see docs/inventory-cache.md):
  * quantity None  == UNKNOWN. It is never rendered or stored as 0.
  * source_updated_at is ONLY what the source reported. fetched_at is when WE retrieved it.
  * demo rows are never persisted and never labelled live.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas import BloodGroup, Component

SCHEMA_VERSION = "inventory-1"


class SourceStatus(str, Enum):
    LIVE = "LIVE"                                  # retrieved from the source during THIS request
    CACHED = "CACHED"                              # served from persisted rows inside the TTL
    STALE = "STALE"                                # refresh failed; persisted rows older than TTL
    DEMO = "DEMO"
    UNAVAILABLE = "UNAVAILABLE"
    SOURCE_NOT_CONFIGURED = "SOURCE_NOT_CONFIGURED"
    SOURCE_ACCESS_DENIED = "SOURCE_ACCESS_DENIED"
    SOURCE_PARSE_FAILED = "SOURCE_PARSE_FAILED"
    SOURCE_RATE_LIMITED = "SOURCE_RATE_LIMITED"


class QualityFlag(str, Enum):
    VERIFIED_SOURCE_ID = "VERIFIED_SOURCE_ID"          # source supplied a record id
    MISSING_SOURCE_TIMESTAMP = "MISSING_SOURCE_TIMESTAMP"
    MISSING_QUANTITY = "MISSING_QUANTITY"
    UNKNOWN_QUANTITY_UNIT = "UNKNOWN_QUANTITY_UNIT"
    INCOMPLETE_CONTACT_DETAILS = "INCOMPLETE_CONTACT_DETAILS"
    CONFLICTING_SOURCE_DATA = "CONFLICTING_SOURCE_DATA"
    DUPLICATE_CANDIDATE = "DUPLICATE_CANDIDATE"
    NOT_IN_LATEST_REFRESH = "NOT_IN_LATEST_REFRESH"    # row survived from an earlier refresh of this scope
    FUTURE_SOURCE_TIMESTAMP = "FUTURE_SOURCE_TIMESTAMP"


_WS = re.compile(r"\s+")


def norm_text(v: str | None) -> str | None:
    if v is None:
        return None
    v = _WS.sub(" ", v.strip()).casefold()
    return v or None


class StockQuery(BaseModel):
    """Validated, normalised search parameters. Never carries credentials or user identifiers."""
    model_config = ConfigDict(extra="forbid")
    state: str | None = Field(None, max_length=80)
    district: str | None = Field(None, max_length=80)
    blood_group: BloodGroup
    component: Component
    centre_query: str | None = Field(None, max_length=80)
    page: int = Field(1, ge=1, le=1000)
    page_size: int = Field(20, ge=1, le=100)

    @field_validator("state", "district", "centre_query")
    @classmethod
    def _clean(cls, v: str | None) -> str | None:
        return _WS.sub(" ", v.strip()) or None if v else None

    def scope_key(self, source_id: str) -> str:
        """Deterministic key for ONE upstream fetch.

        Deliberately excludes `page` and `centre_query`: they are applied locally to persisted rows
        and never change what the source is asked, so including them would multiply upstream calls.
        """
        parts = [SCHEMA_VERSION, source_id, norm_text(self.state) or "", norm_text(self.district) or "",
                 self.blood_group.value, self.component.value]
        return "|".join(parts)


class StoredBank(BaseModel):
    source: str
    source_bank_id: str
    name: str
    address: str | None = None
    phone: str | None = None
    phones: list[str] = []
    emails: list[str] = []
    faxes: list[str] = []
    contact_raw: str | None = None
    email: str | None = None
    category: str | None = None
    type: str | None = None
    state: str | None = None               # source-provided only; NULL until the contract supplies it
    district: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    source_updated_at: datetime | None = None
    first_seen_at: datetime
    last_seen_at: datetime
    last_fetched_at: datetime
    raw_record_hash: str
    record_status: Literal["VERIFIED", "INCOMPLETE"] = "VERIFIED"


class StoredStock(BaseModel):
    scope_key: str
    source: str
    source_bank_id: str
    blood_group: str
    component: str
    quantity: float | None = None
    quantity_unit: str | None = None
    reported_status: str | None = None
    source_updated_at: datetime | None = None
    fetched_at: datetime
    raw_record_hash: str
    source_record_id: str | None = None
    source_url: str | None = None
    parser_version: str = ""
    source_blood_group_label: str | None = None
    source_component_label: str | None = None
    source_updated_at_raw: str | None = None
    requested_state_code: str | None = None
    requested_component_id: str | None = None
    flags: list[str] = []

    @property
    def row_key(self) -> tuple[str, str, str, str]:
        return (self.scope_key, self.source_bank_id, self.blood_group, self.component)

    @property
    def evidence_ref(self) -> str:
        h = hashlib.sha256("|".join(self.row_key + (self.raw_record_hash,)).encode()).hexdigest()
        return f"ev_{h[:16]}"


class CacheMeta(BaseModel):
    cache_key: str
    query_parameters: dict
    last_successful_refresh_at: datetime
    expires_at: datetime
    source_status: str
    record_count: int


class SyncState(BaseModel):
    source: str
    status: str                            # OK | ERROR
    last_attempt_at: datetime
    last_success_at: datetime | None = None
    last_error_code: str | None = None
    last_error_summary: str | None = None  # safe text only; never credentials or response bodies
    records_fetched: int = 0
    records_upserted: int = 0
    schema_version: str = SCHEMA_VERSION


# ------------------------------------------------------------------ response
class BankOut(BaseModel):
    bank_id: str
    name: str
    address: str | None
    phone: str | None
    phone_href: str | None
    email: str | None
    category: str | None
    type: str | None
    state: str | None
    district: str | None
    flags: list[str] = []
    phones: list[str] = []
    emails: list[str] = []
    faxes: list[str] = []
    contact_raw: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class StockOut(BaseModel):
    blood_group: str
    component: str
    quantity: float | None
    quantity_unit: str | None
    quantity_state: Literal["REPORTED", "UNKNOWN"]
    reported_status: str | None
    source_updated_at: datetime | None
    source_updated_at_raw: str | None = None
    requested_state_code: str | None = None
    requested_component_id: str | None = None
    fetched_at: datetime
    source_age_seconds: int | None
    source_freshness: Literal["WITHIN_WINDOW", "OLD", "UNKNOWN"]
    source_blood_group_label: str | None
    source_component_label: str | None
    source_url: str | None
    evidence_ref: str
    flags: list[str] = []


class BankStockOut(BaseModel):
    bank: BankOut
    stock: list[StockOut]


class Pagination(BaseModel):
    page: int
    page_size: int
    total: int


class ErrorOut(BaseModel):
    code: str
    message: str
    retryable: bool = False


class InventoryResponse(BaseModel):
    data: list[BankStockOut]
    source: str
    source_label: str | None
    source_status: SourceStatus
    cache_status: Literal["HIT", "MISS", "REFRESHED", "STALE_FALLBACK", "BYPASS"]
    fetched_at: datetime | None            # last successful retrieval backing this response
    expires_at: datetime | None
    is_stale: bool
    is_demo: bool
    stale_age_seconds: int | None
    refresh_error: ErrorOut | None = None  # why a refresh failed, even when stale data is served
    warnings: list[str] = []
    error: ErrorOut | None = None
    pagination: Pagination
    disclaimer: str
    demo_label: str | None = None


def phone_href(phone: str | None) -> str | None:
    if not phone:
        return None
    digits = re.sub(r"[^\d+]", "", phone)
    return f"tel:{digits}" if sum(c.isdigit() for c in digits) >= 5 else None
