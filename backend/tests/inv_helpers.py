from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from app.config import Settings
from app.inventory.models import StockQuery
from app.inventory.repository import MemoryInventoryRepository
from app.inventory.service import InventoryService
from app.schemas import BloodBank, BloodGroup, Component, NormalizedStockRecord
from app.services.stock_source import SourceResult

NOW = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self, t=NOW):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, **kw):
        self.t += timedelta(**kw)


async def tick(_):                       # yields to the loop so concurrent tasks can progress
    await asyncio.sleep(0.005)


class FakeLive:
    """Test double standing in for the (still unverified) live e-RaktKosh adapter."""
    source_id, source_display_name, source_type = "eraktkosh", "e-RaktKosh (test double)", "live"
    source_label, official_url, provides_directory = "e-RaktKosh", None, True

    def __init__(self):
        self.banks, self.records, self.exc, self.calls, self.delay = [], [], None, 0, 0.0

    def configured(self):
        return True, ""

    async def status(self):
        from app.services.stock_source import SourceStatusInfo
        return SourceStatusInfo("READY", "test")

    async def fetch_stock(self, bank_ids, blood_group, component, district, *, state=None, quantity=None):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc:
            raise self.exc
        return SourceResult(source_id=self.source_id, source_display_name=self.source_display_name,
                            source_type="live", source_label=self.source_label, records=list(self.records),
                            banks=list(self.banks), checked_bank_ids=[b.bank_id for b in self.banks])


def bank(i="b1", name="City Blood Bank", phone="0385-2220000", address="1 Main Rd"):
    return BloodBank(bank_id=i, name=name, address=address, phone=phone, source_type="live")


def srec(bank_id="b1", group="O-", comp="whole_blood", qty=5.0, unit="units", status="available",
         updated="default", rid="r1", h=None, **kw):
    if updated == "default":
        updated = NOW - timedelta(hours=1)
    return NormalizedStockRecord(
        bank_source_id=bank_id, source_id="eraktkosh", blood_group=group, component=comp, quantity=qty,
        quantity_unit=unit, reported_status=status, source_updated_at=updated, retrieved_at=NOW,
        source_record_id=rid, source_url="https://example.gov.in/x", raw_record_hash=h or f"h-{bank_id}-{qty}-{rid}",
        parser_version="t", **kw)


def query(**kw):
    d = dict(district="Imphal West", state="Manipur", blood_group=BloodGroup.O_NEG,
             component=Component.WHOLE_BLOOD)
    d.update(kw)
    return StockQuery(**d)


def live_settings(**kw):
    return Settings(stock_source_mode="live", retry_base_delay_s=0, rate_limit_per_minute=1000, **kw)


def make(clock=None, adapter=None, repo=None, **settings):
    clock = clock or Clock()
    adapter = adapter or FakeLive()
    if not adapter.banks and not adapter.records and isinstance(adapter, FakeLive):
        adapter.banks, adapter.records = [bank()], [srec()]
    repo = repo if repo is not None else MemoryInventoryRepository(clock)
    svc = InventoryService(settings=live_settings(**settings), adapter=adapter, repo=repo,
                           clock=clock, sleep=tick)
    return svc, adapter, repo, clock


def run(coro):
    return asyncio.run(coro)
