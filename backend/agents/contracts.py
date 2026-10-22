"""Typed contracts shared by every agent, plus the execution budget."""
from __future__ import annotations

import time
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.inventory.models import InventoryResponse
from app.schemas import BloodGroup, Component


class AgentStatus(str, Enum):
    OK = "OK"
    PARTIAL = "PARTIAL"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class AgentResult(BaseModel):
    agent: str
    status: AgentStatus
    data: dict[str, Any] = {}
    evidence_refs: list[str] = []
    warnings: list[str] = []
    errors: list[str] = []
    started_at: datetime
    completed_at: datetime


class BudgetExceeded(Exception):
    def __init__(self, what: str):
        super().__init__(what)
        self.what = what


class Budget:
    """Hard limits for one workflow: tool calls and wall-clock. No unbounded loops are possible
    because every tool call is charged here."""

    def __init__(self, max_tool_calls: int, max_seconds: float):
        self.max_calls, self.calls = max_tool_calls, 0
        self._deadline = time.monotonic() + max_seconds
        self.log: list[str] = []

    def charge(self, tool: str) -> None:
        if time.monotonic() > self._deadline:
            raise BudgetExceeded("workflow time limit")
        if self.calls >= self.max_calls:
            raise BudgetExceeded("tool-call limit")
        self.calls += 1
        self.log.append(tool)


class Filters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: str | None = Field(None, max_length=80)
    district: str | None = Field(None, max_length=80)
    blood_group: BloodGroup | None = None
    component: Component | None = None
    quantity: int | None = Field(None, ge=1, le=20)
    centre_query: str | None = Field(None, max_length=80)
    page: int = Field(1, ge=1, le=1000)
    page_size: int = Field(20, ge=1, le=100)
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)

    @model_validator(mode="after")
    def validate_coordinate_pair(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be provided together")
        return self


class AgentSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str | None = Field(None, min_length=3, max_length=500)
    filters: Filters = Filters()


class Candidate(BaseModel):
    rank: int
    bank_id: str
    name: str
    evidence_quality: str            # GOOD | LIMITED | UNRELIABLE
    match_type: str                  # EXACT_SCOPE | EXACT_SCOPE_TEXT_FILTER
    reasons: list[str]
    flags: list[str]
    evidence_refs: list[str]
    source_updated_at: datetime | None = None
    fetched_at: datetime | None = None
    freshness: str = "UNKNOWN"
    reported_blood_group: str | None = None
    reported_component: str | None = None
    reported_quantity: float | None = None
    reported_quantity_unit: str | None = None
    distance_km: float | None = None
    distance_type: str | None = None


class ContactEntry(BaseModel):
    rank: int
    bank_id: str
    name: str
    phone: str | None
    phone_href: str | None
    email: str | None
    address: str | None
    why: list[str]
    checklist: list[str]
    evidence_refs: list[str]
    source_updated_at: datetime | None = None
    fetched_at: datetime | None = None
    freshness: str = "UNKNOWN"
    reported_blood_group: str | None = None
    reported_component: str | None = None
    reported_quantity: float | None = None
    reported_quantity_unit: str | None = None
    distance_km: float | None = None
    distance_type: str | None = None
    approval_required: bool = True
    approval_status: str = "PENDING_HUMAN_REVIEW"
    caveat: str = ("Suggestion for human review. Reported stock is not a reservation or guarantee; "
                   "confirm directly with the blood bank before travelling.")


class AgentSearchResponse(BaseModel):
    run_id: str
    status: str                      # COMPLETED | NEEDS_CLARIFICATION | UNAVAILABLE | TIMED_OUT | LIMIT_REACHED
    used_llm: bool
    approval_required: bool = True
    action_taken: bool = False
    clarification: str | None = None
    inventory: InventoryResponse | None = None
    candidates: list[Candidate] = []
    contact_plan: list[ContactEntry] = []
    evidence_refs: list[str] = []
    warnings: list[str] = []
    tool_calls: list[str] = []
    steps: list[AgentResult] = []
    started_at: datetime
    completed_at: datetime
