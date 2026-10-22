"""Cache, freshness, stale fallback, failure classification, persistence, single-flight, safety."""
from __future__ import annotations

import asyncio

import pytest

from app.config import Settings
from app.inventory.models import InventoryResponse, Pagination, SourceStatus as S, StockOut
from app.inventory.repository import MemoryInventoryRepository
from app.inventory.safety import enforce
from app.inventory.service import InventoryService
from app.schemas import BloodGroup
from app.services.demo_data import demo_banks_for
from app.services.stock_source import (AccessNotPermitted, DemoStockAdapter, SourceMalformed,
                                       SourceNotConfigured, SourceRateLimited, SourceUnavailable)
from tests.inv_helpers import FakeLive, NOW, bank, make, query, run, srec, tick


# ---------------------------------------------------------------- cache flow (#15-#20)
def test_miss_then_refresh_is_live_and_persisted():
    svc, ad, repo, _ = make()
    r = run(svc.search(query()))
    assert (r.source_status, r.cache_status, ad.calls) == (S.LIVE, "REFRESHED", 1)
    assert r.data[0].stock[0].quantity == 5 and r.fetched_at is not None and not r.is_stale
    assert len(repo.stock) == 1 and len(repo.banks) == 1


def test_hit_inside_ttl_is_cached_without_source_call():
    svc, ad, _, clock = make()
    run(svc.search(query()))
    clock.advance(minutes=19, seconds=59)
    r = run(svc.search(query()))
    assert (r.source_status, r.cache_status, ad.calls) == (S.CACHED, "HIT", 1)
    assert r.fetched_at == NOW                      # original retrieval time, not "now"


def test_ttl_expiry_triggers_refresh():
    svc, ad, _, clock = make()
    run(svc.search(query()))
    clock.advance(minutes=20, seconds=1)
    r = run(svc.search(query()))
    assert (r.source_status, ad.calls) == (S.LIVE, 2)


def test_default_ttl_is_1200s_and_stale_ceiling_documented_default():
    s = Settings()
    assert s.stock_cache_ttl_seconds == 1200 and s.max_stale_age_seconds == 7200


def test_cache_key_normalisation_and_scope():
    a = query(district=" Imphal  West ", state="MANIPUR")
    b = query(district="imphal west", state="manipur", centre_query="x", page=3)
    assert a.scope_key("src") == b.scope_key("src")                # page / centre_query are local filters
    assert a.scope_key("src") != query(blood_group=BloodGroup.A_POS).scope_key("src")
    assert a.scope_key("src") != a.scope_key("other")


# ---------------------------------------------------------------- stale fallback (#21-#23)
def test_failed_refresh_serves_stale_with_age_and_original_timestamps():
    svc, ad, _, clock = make()
    first = run(svc.search(query()))
    clock.advance(minutes=21)
    ad.exc = SourceUnavailable("down")
    r = run(svc.search(query()))
    assert r.source_status is S.STALE and r.is_stale and r.cache_status == "STALE_FALLBACK"
    assert r.stale_age_seconds == 21 * 60 and r.refresh_error.code == "SOURCE_UNAVAILABLE"
    row0, row1 = first.data[0].stock[0], r.data[0].stock[0]
    assert row1.source_updated_at == row0.source_updated_at and row1.fetched_at == row0.fetched_at
    assert r.fetched_at == NOW                                      # never bumped to "now"


def test_no_cache_and_source_down_is_unavailable_not_zero_stock():
    svc, ad, _, _ = make()
    ad.exc = SourceUnavailable("down")
    r = run(svc.search(query()))
    assert r.source_status is S.UNAVAILABLE and r.data == [] and r.error.code == "SOURCE_UNAVAILABLE"
    assert r.pagination.total == 0 and r.is_demo is False


def test_stale_beyond_max_age_is_not_served():
    svc, ad, _, clock = make(max_stale_age_seconds=3600)
    run(svc.search(query()))
    clock.advance(hours=1, seconds=1)
    ad.exc = SourceUnavailable("down")
    r = run(svc.search(query()))
    assert r.source_status is S.UNAVAILABLE and r.data == []


def test_stale_fallback_can_be_disabled():
    svc, ad, _, clock = make(max_stale_age_seconds=0)
    run(svc.search(query()))
    clock.advance(minutes=30)
    ad.exc = SourceUnavailable("down")
    assert run(svc.search(query())).source_status is S.UNAVAILABLE


# ---------------------------------------------------------------- error classification (#2,#3,#6,...)
@pytest.mark.parametrize("exc,status,calls", [
    (AccessNotPermitted("no"), S.SOURCE_ACCESS_DENIED, 1),          # permanent: never retried
    (SourceRateLimited("429"), S.SOURCE_RATE_LIMITED, 1),
    (SourceMalformed("bad"), S.SOURCE_PARSE_FAILED, 1),
    (SourceNotConfigured("cfg"), S.SOURCE_NOT_CONFIGURED, 1),
    (SourceUnavailable("net"), S.UNAVAILABLE, 3),                   # retryable: bounded to 3 attempts
])
def test_failure_status_and_retry_policy(exc, status, calls):
    svc, ad, _, _ = make()
    ad.exc = exc
    r = run(svc.search(query()))
    assert r.source_status is status and ad.calls == calls and r.data == []


def test_timeout_is_a_failure_not_zero():
    svc, ad, _, _ = make(stock_source_timeout_s=0.01, stock_source_max_attempts=1)
    ad.delay = 1
    r = run(svc.search(query()))
    assert r.source_status is S.UNAVAILABLE and r.data == []


def test_circuit_breaker_stops_hammering_source():
    svc, ad, _, _ = make(breaker_failure_threshold=2, stock_source_max_attempts=1)
    ad.exc = AccessNotPermitted("no")
    for _ in range(5):
        r = run(svc.search(query()))
    assert ad.calls == 2 and r.source_status is S.SOURCE_ACCESS_DENIED


def test_sync_state_records_failure_without_erasing_last_success():
    svc, ad, repo, clock = make()
    run(svc.search(query()))
    clock.advance(minutes=21)
    ad.exc = AccessNotPermitted("no")
    run(svc.search(query()))
    st = run(repo.get_sync("eraktkosh"))
    assert st.status == "ERROR" and st.last_error_code == "ACCESS_NOT_PERMITTED"
    assert st.last_success_at == NOW and "token" not in (st.last_error_summary or "").lower()


# ---------------------------------------------------------------- empty / partial / duplicates / validation
def test_empty_success_is_not_an_outage_and_not_zero():
    svc, ad, _, _ = make(adapter=FakeLive())
    ad.banks, ad.records = [bank()], []
    ad.records = []
    r = run(svc.search(query()))
    assert r.source_status is S.LIVE and r.error is None and r.data == []
    assert any("not evidence of zero stock" in w for w in r.warnings)


def test_zero_and_unknown_quantity_stay_distinct():
    ad = FakeLive()
    ad.banks = [bank("b1"), bank("b2", "Other Bank")]
    ad.records = [srec("b1", qty=0.0, rid="a"), srec("b2", qty=None, unit=None, rid="b")]
    svc, *_ = make(adapter=ad)
    r = run(svc.search(query()))
    by = {i.bank.bank_id: i.stock[0] for i in r.data}
    assert (by["b1"].quantity, by["b1"].quantity_state) == (0.0, "REPORTED")
    assert (by["b2"].quantity, by["b2"].quantity_state) == (None, "UNKNOWN")
    assert "MISSING_QUANTITY" in by["b2"].flags


def test_missing_source_timestamp_stays_unknown_never_fetch_time():
    ad = FakeLive()
    ad.banks, ad.records = [bank()], [srec(updated=None)]
    svc, *_ = make(adapter=ad)
    s = run(svc.search(query())).data[0].stock[0]
    assert s.source_updated_at is None and s.source_age_seconds is None
    assert s.source_freshness == "UNKNOWN" and s.fetched_at == NOW
    assert "MISSING_SOURCE_TIMESTAMP" in s.flags


def test_future_source_timestamp_is_flagged_and_freshness_unknown():
    from datetime import timedelta
    ad = FakeLive()
    ad.banks, ad.records = [bank()], [srec(updated=NOW + timedelta(days=2))]
    svc, *_ = make(adapter=ad)
    s = run(svc.search(query())).data[0].stock[0]
    assert "FUTURE_SOURCE_TIMESTAMP" in s.flags and s.source_freshness == "UNKNOWN"


def test_old_source_timestamp_marked_old():
    from datetime import timedelta
    ad = FakeLive()
    ad.banks, ad.records = [bank()], [srec(updated=NOW - timedelta(hours=30))]
    svc, *_ = make(adapter=ad)
    assert run(svc.search(query())).data[0].stock[0].source_freshness == "OLD"


def test_invalid_rows_are_rejected_not_repaired():
    ad = FakeLive()
    ad.banks = [bank()]
    ad.records = [srec(rid="ok"), srec(group=None, rid="g"), srec(qty=-3.0, rid="n"),
                  srec(bank_id="ghost", rid="x")]
    svc, _, repo, _ = make(adapter=ad)
    r = run(svc.search(query()))
    assert len(r.data[0].stock) == 1 and len(repo.stock) == 1
    assert any("rejected" in w for w in r.warnings)


def test_off_scope_records_are_never_persisted_under_the_scope():
    ad = FakeLive()
    ad.banks, ad.records = [bank()], [srec(rid="a"), srec(group="O+", rid="b"), srec(comp="plasma", rid="c")]
    svc, _, repo, _ = make(adapter=ad)
    run(svc.search(query()))
    assert [(r.blood_group, r.component) for r in repo.stock.values()] == [("O-", "whole_blood")]


def test_identical_duplicates_collapse_conflicts_are_flagged():
    ad = FakeLive()
    ad.banks = [bank()]
    ad.records = [srec(rid="a", h="same"), srec(rid="a", h="same"), srec(qty=9.0, rid="z", h="other")]
    svc, *_ = make(adapter=ad)
    r = run(svc.search(query()))
    assert len(r.data[0].stock) == 1 and "CONFLICTING_SOURCE_DATA" in r.data[0].stock[0].flags
    assert any("conflict" in w for w in r.warnings)


def test_partial_refresh_never_deletes_and_flags_rows_not_in_latest():
    ad = FakeLive()
    ad.banks = [bank("b1"), bank("b2", "Second Bank")]
    ad.records = [srec("b1", rid="1"), srec("b2", rid="2")]
    svc, _, repo, clock = make(adapter=ad)
    run(svc.search(query()))
    clock.advance(minutes=21)
    ad.banks, ad.records = [bank("b1")], [srec("b1", rid="1", qty=7.0)]
    r = run(svc.search(query()))
    assert len(repo.stock) == 2                                    # b2 NOT deleted
    by = {i.bank.bank_id: i.stock[0] for i in r.data}
    assert "NOT_IN_LATEST_REFRESH" in by["b2"].flags and "NOT_IN_LATEST_REFRESH" not in by["b1"].flags
    assert by["b1"].quantity == 7.0


def test_upsert_prevents_duplicates_and_preserves_first_seen():
    svc, _, repo, clock = make()
    run(svc.search(query()))
    first_seen = next(iter(repo.banks.values())).first_seen_at
    clock.advance(minutes=21)
    run(svc.search(query()))
    assert len(repo.stock) == 1 and len(repo.banks) == 1
    assert next(iter(repo.banks.values())).first_seen_at == first_seen


# ---------------------------------------------------------------- contact + pagination
def test_contact_details_preserved_exactly_and_tel_href():
    ad = FakeLive()
    ad.banks = [bank(phone="0385 222-0000", address="Lamphel, Imphal")]
    ad.records = [srec()]
    svc, *_ = make(adapter=ad)
    b = run(svc.search(query())).data[0].bank
    assert (b.phone, b.address, b.phone_href) == ("0385 222-0000", "Lamphel, Imphal", "tel:03852220000")


def test_missing_phone_flagged_not_invented():
    ad = FakeLive()
    ad.banks, ad.records = [bank(phone=None)], [srec()]
    svc, *_ = make(adapter=ad)
    b = run(svc.search(query())).data[0].bank
    assert b.phone is None and b.phone_href is None and "INCOMPLETE_CONTACT_DETAILS" in b.flags


def test_pagination_and_centre_text_filter():
    ad = FakeLive()
    ad.banks = [bank(f"b{i}", f"Bank {c}") for i, c in enumerate("CABD")]
    ad.records = [srec(f"b{i}", rid=str(i)) for i in range(4)]
    svc, *_ = make(adapter=ad)
    p1 = run(svc.search(query(page_size=3)))
    assert [i.bank.name for i in p1.data] == ["Bank A", "Bank B", "Bank C"] and p1.pagination.total == 4
    assert [i.bank.name for i in run(svc.search(query(page=2, page_size=3))).data] == ["Bank D"]
    f = run(svc.search(query(centre_query="bank b")))
    assert [i.bank.name for i in f.data] == ["Bank B"] and ad.calls == 1    # local filter, no refetch


# ---------------------------------------------------------------- restart / DB failure (#24)
def test_restart_with_persisted_records_serves_cache_without_source():
    svc, ad, repo, clock = make()
    run(svc.search(query()))
    ad2 = FakeLive()
    svc2 = InventoryService(settings=svc.s, adapter=ad2, repo=repo, clock=clock, sleep=tick)  # "new process"
    r = run(svc2.search(query()))
    assert r.source_status is S.CACHED and ad2.calls == 0 and len(r.data) == 1


def test_db_down_still_serves_live_data_honestly():
    svc, ad, repo, _ = make()
    repo.fail = RuntimeError("x")
    r = run(svc.search(query()))
    assert r.source_status is S.LIVE and len(r.data) == 1
    assert any("could not be saved" in w for w in r.warnings)


def test_db_and_source_both_down_is_unavailable():
    svc, ad, repo, _ = make()
    repo.fail, ad.exc = RuntimeError("x"), SourceUnavailable("down")
    r = run(svc.search(query()))
    assert r.source_status is S.UNAVAILABLE and r.data == []


# ---------------------------------------------------------------- single flight / manual refresh (#25)
def test_concurrent_identical_queries_trigger_one_source_call():
    ad = FakeLive()
    ad.banks, ad.records, ad.delay = [bank()], [srec()], 0.05
    svc, *_ = make(adapter=ad)

    async def both():
        return await asyncio.gather(svc.search(query()), svc.search(query()))
    a, b = run(both())
    assert ad.calls == 1 and sorted([a.source_status.value, b.source_status.value]) == ["CACHED", "LIVE"]
    assert len(a.data) == len(b.data) == 1


def test_lock_held_elsewhere_without_cache_is_unavailable_with_retry():
    svc, ad, repo, _ = make()
    key = query().scope_key("eraktkosh")
    assert run(repo.acquire_lock(key, "other-instance", 60))
    r = run(svc.search(query()))
    assert ad.calls == 0 and r.source_status is S.UNAVAILABLE and r.error.code == "REFRESH_IN_PROGRESS"


def test_lock_held_elsewhere_with_stale_cache_serves_stale():
    svc, ad, repo, clock = make()
    run(svc.search(query()))
    clock.advance(minutes=25)
    assert run(repo.acquire_lock(query().scope_key("eraktkosh"), "other", 60))
    r = run(svc.search(query()))
    assert r.source_status is S.STALE and ad.calls == 1


def test_expired_lock_can_be_taken_over():
    svc, _, repo, clock = make()
    assert run(repo.acquire_lock("k", "a", 10)) and not run(repo.acquire_lock("k", "b", 10))
    clock.advance(seconds=11)
    assert run(repo.acquire_lock("k", "b", 10))


def test_lock_released_after_refresh_even_on_failure():
    svc, ad, repo, _ = make()
    ad.exc = SourceUnavailable("x")
    run(svc.search(query()))
    assert repo.locks == {}


def test_force_refresh_respects_minimum_interval():
    svc, ad, _, clock = make()
    run(svc.search(query()))
    run(svc.search(query(), force_refresh=True))
    assert ad.calls == 1                                            # <60s: served from cache
    clock.advance(seconds=61)
    r = run(svc.search(query(), force_refresh=True))
    assert ad.calls == 2 and r.source_status is S.LIVE


# ---------------------------------------------------------------- demo isolation (#28, #50)
def _demo_service():
    from tests.inv_helpers import Clock
    clock, repo = Clock(), MemoryInventoryRepository()
    s = Settings(stock_source_mode="demo", retry_base_delay_s=0)
    return InventoryService(settings=s, adapter=DemoStockAdapter(clock), repo=repo,
                            demo_banks=demo_banks_for, clock=clock, sleep=tick), repo


def test_demo_is_labelled_isolated_and_never_persisted():
    svc, repo = _demo_service()
    r = run(svc.search(query()))
    assert r.source_status is S.DEMO and r.is_demo and r.demo_label and r.cache_status == "BYPASS"
    assert r.is_demo and r.data and all(i.bank.bank_id.startswith("demo-bank-") for i in r.data)
    assert not repo.stock and not repo.banks and not repo.meta


def test_demo_failure_never_becomes_live():
    svc, _ = _demo_service()
    r = run(svc.search(query(district="Demo Timeout")))
    assert r.source_status is S.UNAVAILABLE and r.data == []
    assert r.is_demo and r.demo_label and r.source_status not in (S.LIVE, S.CACHED, S.STALE)


def test_live_failure_never_substitutes_demo():
    svc, ad, _, _ = make()
    ad.exc = SourceUnavailable("down")
    r = run(svc.search(query()))
    assert r.source_status is not S.DEMO and not r.is_demo and r.data == []


def test_live_is_default_for_hackathon_and_explicit_demo_is_honoured():
    assert Settings(app_env="production").stock_source_mode == "live"
    assert Settings(app_env="production", stock_source_mode="demo").stock_source_mode == "demo"
    assert Settings(app_env="local").stock_source_mode == "live"


# ---------------------------------------------------------------- deterministic safety gate (#32, #34)
def _resp(**kw):
    base = dict(data=[], source="s", source_label="s", source_status=S.LIVE, cache_status="REFRESHED",
                fetched_at=NOW, expires_at=None, is_stale=False, is_demo=False, stale_age_seconds=None,
                pagination=Pagination(page=1, page_size=20, total=0), disclaimer="d")
    base.update(kw)
    return InventoryResponse(**base)


@pytest.mark.parametrize("kw", [
    dict(source_status=S.CACHED, fetched_at=None),
    dict(source_status=S.STALE, is_stale=True, stale_age_seconds=None),
    dict(source_status=S.CACHED, expires_at=NOW),                   # expired cache labelled current
    dict(source_status=S.LIVE, is_demo=True),                       # demo labelled live
    dict(source_status=S.DEMO, is_demo=False),
])
def test_safety_downgrades_inconsistent_responses(kw):
    out, n = enforce(_resp(**kw), NOW)
    assert n == 1 and out.source_status is S.UNAVAILABLE and out.error.code == "SAFETY_CHECK_FAILED"


def test_safety_suppresses_bad_rows():
    from app.inventory.models import BankOut, BankStockOut
    bad = StockOut(blood_group="O-", component="whole_blood", quantity=None, quantity_unit=None,
                   quantity_state="REPORTED", reported_status=None, source_updated_at=None, fetched_at=NOW,
                   source_age_seconds=None, source_freshness="UNKNOWN", source_blood_group_label=None,
                   source_component_label=None, source_url=None, evidence_ref="ev_x")
    b = BankOut(bank_id="b", name="n", address=None, phone=None, phone_href=None, email=None,
                category=None, type=None, state=None, district=None)
    out, n = enforce(_resp(data=[BankStockOut(bank=b, stock=[bad])]), NOW)
    assert n == 1 and out.data[0].stock == [] and any("withheld" in w for w in out.warnings)
