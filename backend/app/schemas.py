"""Pydantic schemas, enums and the shared error type."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


class AppError(Exception):
    """Raised anywhere; rendered as the documented error contract."""

    def __init__(self, code: str, message: str, *, status_code: int = 400,
                 retryable: bool = False, user_action: str = ""):
        super().__init__(message)
        self.code, self.message = code, message
        self.status_code, self.retryable, self.user_action = status_code, retryable, user_action


# ---------------------------------------------------------------- enums
class BloodGroup(str, Enum):
    O_NEG = "O-"; O_POS = "O+"; A_NEG = "A-"; A_POS = "A+"
    B_NEG = "B-"; B_POS = "B+"; AB_NEG = "AB-"; AB_POS = "AB+"


class Component(str, Enum):
    # Legacy broad values remain accepted for older clients; the current UI uses exact e-RaktKosh
    # component selections wherever the source catalog distinguishes separate products.
    WHOLE_BLOOD = "whole_blood"
    PRBC = "prbc"
    PLATELETS = "platelets"
    PLASMA = "plasma"
    CRYO_POOR_PLASMA = "cryo_poor_plasma"
    CRYOPRECIPITATE = "cryoprecipitate"
    FRESH_FROZEN_PLASMA = "fresh_frozen_plasma"
    IRRADIATED_RBC = "irradiated_rbc"
    LEUKOREDUCED_RBC = "leukoreduced_rbc"
    PLATELET_CONCENTRATE = "platelet_concentrate"
    PLATELET_RICH_PLASMA = "platelet_rich_plasma"
    RANDOM_DONOR_PLATELETS = "random_donor_platelets"
    SAGM_PACKED_RED_BLOOD_CELLS = "sagm_packed_red_blood_cells"
    SINGLE_DONOR_PLASMA = "single_donor_plasma"
    SINGLE_DONOR_PLATELETS = "single_donor_platelets"


class QuantityUnit(str, Enum):
    UNITS = "units"; ML = "ml"


class RunStatus(str, Enum):
    ACCEPTED = "ACCEPTED"; NEEDS_LOCATION = "NEEDS_LOCATION"; PLANNING = "PLANNING"
    RUNNING = "RUNNING"; VERIFYING = "VERIFYING"; AWAITING_APPROVAL = "AWAITING_APPROVAL"
    COMPLETED = "COMPLETED"; NEEDS_REVIEW = "NEEDS_REVIEW"; FAILED = "FAILED"


class TaskStatus(str, Enum):
    PENDING = "PENDING"; RUNNING = "RUNNING"; SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"; SKIPPED = "SKIPPED"; UNKNOWN = "UNKNOWN"


TASK_CODES = [
    "T01_VALIDATE", "T02_RESOLVE_LOCATION", "T03_PLAN_SEARCH", "T04_DISCOVER_BANKS",
    "T05_FETCH_STOCK", "T06_NORMALIZE_EVIDENCE", "T07_MATCH_REQUIREMENTS",
    "T08_CHECK_FRESHNESS", "T09_CALCULATE_ROUTES", "T10_VERIFY_RESULTS",
    "T11_PREPARE_ACTION", "T12_AWAIT_APPROVAL", "T13_RECORD_OUTCOME",
]


class MatchState(str, Enum):
    MATCHED_REPORTED = "MATCHED_REPORTED"; NOT_MATCHED = "NOT_MATCHED"
    INSUFFICIENT = "INSUFFICIENT"; UNKNOWN = "UNKNOWN"


class EvidenceState(str, Enum):
    FRESH = "FRESH"; STALE = "STALE"
    UNKNOWN_FRESHNESS = "UNKNOWN_FRESHNESS"; CONFLICTING_REPORTS = "CONFLICTING_REPORTS"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"


class Section(str, Enum):
    A_ELIGIBLE = "A_ELIGIBLE"; B_UNCERTAIN = "B_UNCERTAIN"; C_INSUFFICIENT = "C_INSUFFICIENT"
    D_MISMATCH = "D_MISMATCH"; E_UNKNOWN = "E_UNKNOWN"


class OutcomeType(str, Enum):
    CONFIRMED_AVAILABLE = "confirmed_available"; UNAVAILABLE = "unavailable"
    NO_ANSWER = "no_answer"; OTHER = "other"


# ---------------------------------------------------------------- request
class LocationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["device", "manual"]
    district: str | None = Field(None, max_length=80)
    state: str | None = Field(None, max_length=80)
    country: str = Field("IN", min_length=2, max_length=2)
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    location_consent: bool = False

    @field_validator("district", "state")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        return v.strip() or None if v else None

    @model_validator(mode="after")
    def _check(self) -> "LocationIn":
        if self.mode == "manual" and not (self.district and self.state):
            raise ValueError("district and state are required for manual location")
        if self.mode == "device" and self.location_consent and (
                self.latitude is None or self.longitude is None):
            raise ValueError("latitude and longitude are required when device location is consented")
        return self


class SearchOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    include_nearby_districts: bool = True
    max_radius_km: int = Field(50, ge=1, le=500)


class RunCreate(BaseModel):
    # extra=forbid: patient names/IDs sent by mistake are rejected, never stored.
    model_config = ConfigDict(extra="forbid")
    blood_group: BloodGroup
    component: Component
    quantity: int = Field(..., ge=1, le=20)
    quantity_unit: QuantityUnit = QuantityUnit.UNITS
    location: LocationIn
    search_options: SearchOptions = SearchOptions()


class StoredRequest(BaseModel):
    """Persisted request. Coordinates are deliberately NOT part of this model."""
    request_id: str
    blood_group: BloodGroup
    component: Component
    quantity: int
    quantity_unit: QuantityUnit
    location_mode: str
    district: str | None
    state: str | None
    country: str
    location_consent: bool
    include_nearby_districts: bool
    max_radius_km: int
    idempotency_key: str
    created_at: datetime


# ---------------------------------------------------------------- domain
class BloodBank(BaseModel):
    bank_id: str
    provider_place_id: str | None = None
    name: str
    address: str | None = None
    phone: str | None = None            # Source-provided and validated only; never guessed
    phones: list[str] = []
    emails: list[str] = []
    faxes: list[str] = []
    contact_raw: str | None = None
    facility_type: str | None = None

    @field_validator("phone")
    @classmethod
    def _validated_phone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = " ".join(value.strip().split())
        if clean.casefold() in {"", "na", "n/a", "not available", "unknown", "null", "none", "nil", "-"}:
            return None
        # Accept common international/national formatting, but only 7–15 digits.
        import re
        if not re.fullmatch(r"\+?[0-9().\s-]+", clean):
            return None
        digits = re.sub(r"\D", "", clean)
        if not 7 <= len(digits) <= 15 or ("+" in clean and not clean.startswith("+")):
            return None
        if clean.count("+") > 1:
            return None
        return clean
    latitude: float | None = None
    longitude: float | None = None
    location_precision: Literal["FACILITY", "LOCALITY"] | None = None
    location_note: str | None = None
    distance_km: float | None = None   # nearest-source distance when geocoding is not performed
    opening_hours: list[str] | None = None
    city: str | None = None
    district: str | None = None
    state: str | None = None
    source_type: Literal["demo", "live"] = "live"
    directory_source: str | None = None
    directory_reference: str | None = None
    directory_retrieved_at: datetime | None = None


class NormalizedStockRecord(BaseModel):
    bank_source_id: str
    source_id: str
    blood_group: str | None
    component: str | None
    quantity: float | None
    quantity_unit: str | None
    reported_status: str | None         # "available" | "unavailable" | None
    source_updated_at: datetime | None
    retrieved_at: datetime
    source_record_id: str | None
    source_url: str | None
    raw_record_hash: str
    parser_version: str
    limitations: list[str] = []
    source_updated_at_raw: str | None = None
    requested_state_code: str | None = None
    requested_component_id: str | None = None
    source_blood_group_label: str | None = None   # label exactly as the source printed it
    source_component_label: str | None = None


class EvidenceRecord(NormalizedStockRecord):
    evidence_id: str = Field(default_factory=new_id)
    run_id: str = ""
    bank_id: str = ""
    source_display_name: str = ""
    source_type: Literal["demo", "live"] = "live"
    source_label: str | None = None          # "e-RaktKosh" ONLY for genuine e-RaktKosh records
    official_url: str | None = None


class RouteResult(BaseModel):
    meters: int
    seconds: int
    mode: str = "DRIVE"
    retrieved_at: datetime


class Task(BaseModel):
    task_id: str = Field(default_factory=new_id)
    run_id: str
    task_code: str
    status: TaskStatus = TaskStatus.PENDING
    attempt: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_code: str | None = None
    output_summary: str | None = None
    safe_to_retry: bool = False


class Run(BaseModel):
    run_id: str
    request_id: str
    status: RunStatus = RunStatus.ACCEPTED
    plan_version: int = 1
    plan: dict[str, Any] = {}
    started_at: datetime
    completed_at: datetime | None = None
    error_code: str | None = None
    token_hash: str
    result_state: str | None = None
    summary: dict[str, Any] = {}
    status_history: list[dict[str, Any]] = []
    demo_mode: bool = True


class Candidate(BaseModel):
    candidate_id: str = Field(default_factory=new_id)
    run_id: str
    bank: BloodBank
    section: Section
    rank: int | None = None
    match_state: MatchState
    evidence_state: EvidenceState | None = None
    reason_codes: list[str]
    unresolved_fields: list[str] = []
    evidence_ids: list[str] = []
    source_id: str | None = None
    source_type: Literal["demo", "live", "unknown"] = "unknown"
    source_label: str | None = None
    source_display_name: str | None = None
    official_source_url: str | None = None
    source_blood_group_label: str | None = None
    source_component_label: str | None = None
    blood_group_reported: str | None = None
    component_reported: str | None = None
    quantity_reported: float | None = None
    quantity_unit_reported: str | None = None
    source_updated_at: datetime | None = None
    source_updated_at_raw: str | None = None
    retrieved_at: datetime | None = None
    route_meters: int | None = None
    route_seconds: int | None = None
    route_mode: str | None = None
    route_retrieved_at: datetime | None = None
    distance_km: float | None = None
    distance_type: Literal["straight_line"] | None = None
    distance_is_approximate: bool = False
    distance_unavailable_reason: str | None = None
    ranking_tier: int | None = None
    ranking_score: float | None = None
    ranking_version: str | None = None
    ranking_explanation: str = ""
    verified_for_search: bool = False
    display_note: str = ""
    rule_version: str = ""


class ProposalItem(BaseModel):
    candidate_id: str
    bank_id: str
    bank_name: str
    phone: str | None
    section: Section
    evidence_state: EvidenceState | None
    source_updated_at: datetime | None
    source_updated_at_raw: str | None = None
    display_note: str
    address: str | None = None
    facility_source_type: Literal["demo", "live"] = "live"
    directory_source: str | None = None
    retrieved_at: datetime | None = None
    source_name: str | None = None
    source_type: Literal["demo", "live", "unknown"] = "unknown"
    source_url: str | None = None
    blood_group: str | None = None
    component: str | None = None
    quantity: float | None = None
    quantity_unit: str | None = None
    distance_km: float | None = None
    distance_type: str | None = None


class Approval(BaseModel):
    approval_id: str = Field(default_factory=new_id)
    run_id: str
    proposal_version: int
    items_hash: str
    proposal_hash: str
    items: list[ProposalItem]
    status: Literal["PENDING", "APPROVED", "SUPERSEDED"] = "PENDING"
    requested_at: datetime
    approved_at: datetime | None = None
    approved_by: str | None = None


class Outcome(BaseModel):
    verification_id: str = Field(default_factory=new_id)
    run_id: str
    candidate_id: str
    approval_id: str
    intended_action: str = "user_calls_blood_bank"
    observed_outcome: OutcomeType
    outcome_type: Literal["user_reported"] = "user_reported"
    occurred_at: datetime
    note: str | None = None
    recorded_at: datetime
    source_evidence_ids: list[str] = []


class AuditEvent(BaseModel):
    event_id: str = Field(default_factory=new_id)
    run_id: str | None
    actor_type: str
    actor_id: str
    event_type: str
    entity_id: str | None = None
    before_hash: str | None = None
    after_hash: str | None = None
    created_at: datetime


# ---------------------------------------------------------------- API bodies
class ProposalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_ids: list[str] | None = Field(None, max_length=20)


class ApprovalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proposal_version: int
    proposal_hash: str


class OutcomeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: str
    outcome: OutcomeType
    occurred_at: datetime | None = None
    note: str | None = Field(None, max_length=280)


class IntakeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(..., min_length=3, max_length=500)


class IntakeDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    blood_group: BloodGroup | None = None
    component: Component | None = None
    quantity: int | None = Field(None, ge=1, le=20)
    quantity_unit: QuantityUnit | None = None
