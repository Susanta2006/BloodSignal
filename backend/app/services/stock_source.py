"""Stock source interface. Read-only. Blocked/CAPTCHA/paywalled => stop, never bypass.

Demo and live adapters are separate classes; nothing here ever falls back from one to the other.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Protocol

from pydantic import BaseModel

from app.schemas import BloodBank, NormalizedStockRecord, utcnow
from app.services.demo_data import DEMO_SOURCE_ID, DEMO_SOURCE_NAME, demo_stock


class SourceError(Exception):
    code = "SOURCE_ERROR"
    retryable = False


class SourceUnavailable(SourceError):       # timeout / network / 5xx: bounded retry allowed
    code, retryable = "SOURCE_UNAVAILABLE", True


class AccessNotPermitted(SourceError):      # 401/403/redirect-to-login: never retried
    code = "ACCESS_NOT_PERMITTED"


class SourceNotConfigured(SourceError):
    code = "NOT_CONFIGURED"


class SourceRateLimited(SourceError):       # source (429) or our own limiter: never hammered
    code = "SOURCE_RATE_LIMITED"


class SourceMalformed(SourceError):
    code = "MALFORMED_RESPONSE"


class SourceRequestRejected(SourceError):   # 4xx other than 401/403/429: our request/contract is wrong
    code = "SOURCE_REQUEST_REJECTED"


@dataclass
class SourceStatusInfo:
    state: str          # NOT_CONFIGURED | READY | UNAVAILABLE | ACCESS_DENIED | DEMO
    reason: str = ""    # safe for display: never contains credentials


class SourceResult(BaseModel):
    source_id: str
    source_display_name: str
    source_type: str                 # "demo" | "live"
    source_label: str | None = None
    official_url: str | None = None
    records: list[NormalizedStockRecord]
    banks: list[BloodBank] = []      # only for sources that supply their own bank identity
    checked_bank_ids: list[str] = []
    skipped_unsupported: int = 0     # valid source rows outside our supported groups/components
    invalid_rows: int = 0            # malformed rows we could not use


class StockSourceAdapter(Protocol):
    source_id: str
    source_display_name: str
    source_type: str
    source_label: str | None
    official_url: str | None
    provides_directory: bool

    def configured(self) -> tuple[bool, str]: ...
    async def status(self) -> SourceStatusInfo: ...
    async def fetch_stock(self, bank_ids: list[str], blood_group: str, component: str,
                          district: str | None, *, state: str | None = None,
                          quantity: int | None = None) -> SourceResult: ...


class DemoStockAdapter:
    source_id = DEMO_SOURCE_ID
    source_display_name = DEMO_SOURCE_NAME
    source_type = "demo"
    source_label = "Synthetic demo fixtures"
    official_url = None
    provides_directory = False

    def __init__(self, clock: Callable[[], datetime] = utcnow):
        self._clock = clock

    def configured(self) -> tuple[bool, str]:
        return True, ""

    async def status(self) -> SourceStatusInfo:
        return SourceStatusInfo("DEMO", "Synthetic demo fixtures; not live stock")

    async def fetch_stock(self, bank_ids, blood_group, component, district, *, state=None,
                          quantity=None) -> SourceResult:
        if (district or "").strip().lower() == "demo timeout":
            raise SourceUnavailable("simulated source timeout")
        now = self._clock()
        data = demo_stock(now, bank_ids, blood_group, component, requested_quantity=quantity)
        # Returns every record for the banks (not pre-filtered) so the rules engine,
        # not the adapter, is what demonstrates mismatch/stale/insufficient handling.
        recs = [r for b in bank_ids for r in data.get(b, [])]
        return SourceResult(source_id=self.source_id, source_display_name=self.source_display_name,
                            source_type="demo", source_label=self.source_label, records=recs,
                            checked_bank_ids=list(bank_ids))
