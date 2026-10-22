from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import build_container, create_app
from app.schemas import BloodBank, NormalizedStockRecord, RouteResult
from app.services.stock_source import (SourceResult, SourceUnavailable, AccessNotPermitted)

NOW = datetime(2026, 10, 19, 10, 0, tzinfo=timezone.utc)


class FakeRoutes:
    """Distance grows with latitude so ordering is predictable."""
    def __init__(self): self.calls = 0
    async def driving(self, origin, dest):
        self.calls += 1
        m = int(abs(dest[0] - 24.0) * 1000)
        return RouteResult(meters=m, seconds=m // 10, retrieved_at=NOW)


class FailingRoutes:
    async def driving(self, origin, dest): return None


class StaticDirectory:
    def __init__(self, banks=None): self.banks = list(banks or [])
    async def discover(self, *, district, state, origin, radius_km): return list(self.banks)


class StaticAdapter:
    source_id, source_display_name, source_type = "static-test", "Static test source", "demo"
    source_label, official_url, provides_directory = "Static test source", None, False
    def __init__(self, records=None, exc=None):
        self.records, self.exc, self.calls = records or [], exc, 0
    def configured(self): return True, ""
    async def status(self):
        from app.services.stock_source import SourceStatusInfo
        return SourceStatusInfo("DEMO", "test")
    async def fetch_stock(self, bank_ids, blood_group, component, district, *, state=None, quantity=None):
        self.calls += 1
        if self.exc: raise self.exc
        return SourceResult(source_id=self.source_id, source_display_name=self.source_display_name,
                            source_type="demo", records=self.records, checked_bank_ids=bank_ids)


def rec(bank="alpha", group="O-", comp="whole_blood", qty=3, unit="units", status="available",
        age_h=4, source="static-test", rid=None, **kw):
    return NormalizedStockRecord(
        bank_source_id=f"demo-bank-{bank}", source_id=source, blood_group=group, component=comp,
        quantity=qty, quantity_unit=unit, reported_status=status,
        source_updated_at=None if age_h is None else NOW - timedelta(hours=age_h),
        retrieved_at=NOW, source_record_id=rid or f"{bank}-{group}-{comp}-{source}",
        source_url="https://demo.invalid/x", raw_record_hash="h", parser_version="t", **kw)


async def _nosleep(_): return None


@pytest.fixture
def make_client():
    def _make(stock=None, routes=None, directory=None, **settings):
        defaults = {"retry_base_delay_s": 0, "rate_limit_per_minute": 1000, "stock_source_mode": "demo"}
        defaults.update(settings)
        s = Settings(**defaults)
        c = build_container(s, stock=stock, routes=routes or FakeRoutes(), directory=directory,
                            clock=lambda: NOW, sleep=_nosleep)
        return TestClient(create_app(s, c)), c
    return _make


def payload(group="O-", comp="whole_blood", qty=2, district="Imphal West", mode="manual", **loc):
    location = {"mode": mode, "district": district, "state": "Manipur", **loc}
    return {"blood_group": group, "component": comp, "quantity": qty, "quantity_unit": "units",
            "location": location}


DEVICE = dict(mode="device", location_consent=True, latitude=24.80, longitude=93.94)


def start(client, body, key="key-12345678"):
    r = client.post("/api/runs", json=body, headers={"Idempotency-Key": key})
    assert r.status_code == 202, r.text
    j = r.json()
    return j["run_id"], {"X-Run-Token": j["run_token"]}


def device_payload(**kw):
    b = payload(**kw)
    b["location"] = {**b["location"], **DEVICE}
    return b


def cands(client, run_id, h):
    return client.get(f"/api/runs/{run_id}/candidates", headers=h).json()["candidates"]
