"""InventoryService: the ONLY writer of the inventory cache.

Flow: fresh cache -> CACHED | miss/expired -> single-flight refresh -> LIVE | refresh failure ->
STALE (within max_stale_age) | UNAVAILABLE. Demo mode is a separate path that never persists.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
from datetime import datetime, timedelta
from typing import Awaitable, Callable

from app.config import Settings
from app.inventory.models import (BankOut, BankStockOut, CacheMeta, ErrorOut, InventoryResponse,
                                  Pagination, QualityFlag as Q, SourceStatus as S,
                                  StockOut, StockQuery, StoredBank, StoredStock, SyncState, norm_text,
                                  phone_href)
from app.inventory.repository import InventoryRepository, RepositoryError
from app.inventory.resilience import CircuitBreaker, call_with_retry
from app.safety import DEMO_LABEL, DISCLAIMER
from app.schemas import new_id, utcnow
from app.services.stock_source import (AccessNotPermitted, SourceError, SourceMalformed,
                                       SourceNotConfigured, SourceRateLimited, SourceResult,
                                       StockSourceAdapter)

log = logging.getLogger("inventory")
MIN_FORCE_INTERVAL_S = 60          # manual refresh cannot hit the source more often than this per scope
_FUTURE_SKEW = timedelta(minutes=5)

_STATUS_FOR = [(AccessNotPermitted, S.SOURCE_ACCESS_DENIED), (SourceRateLimited, S.SOURCE_RATE_LIMITED),
               (SourceMalformed, S.SOURCE_PARSE_FAILED), (SourceNotConfigured, S.SOURCE_NOT_CONFIGURED)]


def status_for(e: Exception) -> S:
    for cls, st in _STATUS_FOR:
        if isinstance(e, cls):
            return st
    return S.UNAVAILABLE


class Refreshed:
    def __init__(self, banks, rows, warnings, fetched, upserted, persisted):
        self.banks, self.rows, self.warnings = banks, rows, warnings
        self.fetched, self.upserted, self.persisted = fetched, upserted, persisted


# ------------------------------------------------------------------ normalisation
def normalize(result: SourceResult, query: StockQuery, scope_key: str,
              now: datetime) -> tuple[list[StoredBank], list[StoredStock], list[str]]:
    """Validate + convert adapter output. Never fabricates a value; rejects rather than repairs."""
    warnings: list[str] = []
    bank_map: dict[str, StoredBank] = {}
    for b in result.banks:
        incomplete = not (b.address and b.phone)
        h = hashlib.sha256(json.dumps([b.bank_id, b.name, b.address, b.phone], default=str).encode()).hexdigest()
        bank_map[b.bank_id] = StoredBank(
            source=result.source_id, source_bank_id=b.bank_id, name=b.name, address=b.address,
            phone=b.phone, phones=list(b.phones), emails=list(b.emails), faxes=list(b.faxes),
            contact_raw=b.contact_raw, email=(b.emails[0] if b.emails else None),
            type=b.facility_type, latitude=b.latitude, longitude=b.longitude, first_seen_at=now,
            last_seen_at=now, last_fetched_at=now, raw_record_hash=h,
            record_status="INCOMPLETE" if incomplete else "VERIFIED")
    want_g, want_c = query.blood_group.value, query.component.value
    rows: dict[tuple, StoredStock] = {}
    rejected = off_scope = conflicts = 0
    for r in result.records:
        if r.blood_group is None or r.component is None or r.bank_source_id not in bank_map:
            rejected += 1
            continue
        if r.quantity is not None and (math.isnan(r.quantity) or r.quantity < 0):
            rejected += 1
            continue
        if r.blood_group != want_g or r.component != want_c:
            off_scope += 1                 # adapter may over-return; never persist it under this scope
            continue
        flags: list[str] = []
        if r.source_record_id:
            flags.append(Q.VERIFIED_SOURCE_ID.value)
        if r.source_updated_at is None:
            flags.append(Q.MISSING_SOURCE_TIMESTAMP.value)
        elif r.source_updated_at > now + _FUTURE_SKEW:
            flags.append(Q.FUTURE_SOURCE_TIMESTAMP.value)
        if r.quantity is None:
            flags.append(Q.MISSING_QUANTITY.value)
        if r.quantity is not None and not r.quantity_unit:
            flags.append(Q.UNKNOWN_QUANTITY_UNIT.value)
        row = StoredStock(
            scope_key=scope_key, source=result.source_id, source_bank_id=r.bank_source_id,
            blood_group=r.blood_group, component=r.component, quantity=r.quantity,
            quantity_unit=r.quantity_unit, reported_status=r.reported_status,
            source_updated_at=r.source_updated_at, source_updated_at_raw=r.source_updated_at_raw,
            requested_state_code=r.requested_state_code, requested_component_id=r.requested_component_id,
            fetched_at=now, raw_record_hash=r.raw_record_hash,
            source_record_id=r.source_record_id, source_url=r.source_url, parser_version=r.parser_version,
            source_blood_group_label=r.source_blood_group_label,
            source_component_label=r.source_component_label, flags=flags)
        old = rows.get(row.row_key)
        if old is None:
            rows[row.row_key] = row
        elif old.raw_record_hash != row.raw_record_hash:       # same key, different content: flag, keep newer
            conflicts += 1
            keep = row if (row.source_updated_at or datetime.min.replace(tzinfo=now.tzinfo)) > \
                          (old.source_updated_at or datetime.min.replace(tzinfo=now.tzinfo)) else old
            keep.flags = sorted(set(keep.flags) | {Q.CONFLICTING_SOURCE_DATA.value})
            rows[row.row_key] = keep
    if rejected:
        warnings.append(f"{rejected} source record(s) rejected: unrecognised group/component, invalid "
                        "quantity or unknown bank identity.")
    if conflicts:
        warnings.append(f"{conflicts} record(s) conflict within the source response; flagged "
                        "CONFLICTING_SOURCE_DATA until reconciled.")
    if result.invalid_rows:
        warnings.append(f"{result.invalid_rows} malformed source row(s) ignored.")
    used = {r.source_bank_id for r in rows.values()}
    return [b for k, b in bank_map.items() if k in used], list(rows.values()), warnings


# ------------------------------------------------------------------ service
class InventoryService:
    def __init__(self, *, settings: Settings, adapter: StockSourceAdapter, repo: InventoryRepository,
                 demo_banks: Callable[[str | None], list] | None = None,
                 clock: Callable[[], datetime] = utcnow,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self.s, self.adapter, self.repo = settings, adapter, repo
        self._clock, self._sleep, self._demo_banks = clock, sleep, demo_banks
        self.breaker = CircuitBreaker(settings.breaker_failure_threshold, settings.breaker_cooldown_seconds)
        self.counters: dict[str, int] = {"cache_hit": 0, "cache_miss": 0, "refresh_ok": 0,
                                         "refresh_failed": 0, "stale_served": 0, "demo_requests": 0,
                                         "lock_lost": 0, "safety_suppressed": 0}

    # ---- public
    async def search(self, query: StockQuery, *, force_refresh: bool = False) -> InventoryResponse:
        if self.s.demo_mode:
            return await self._demo(query)
        # The observed e-RaktKosh endpoint sends no-store directives. Do not serve or write
        # shared inventory cache rows for this provider; normalize only for this response.
        if getattr(self.adapter, "no_store", False):
            return await self._search_eraktkosh_no_store(query)
        now = self._clock()
        key = query.scope_key(self.adapter.source_id)
        meta = None
        try:
            meta = await self.repo.get_cache_meta(key)
        except RepositoryError as e:
            log.warning("cache_meta_read_failed code=%s", e.code)
            # without metadata we cannot judge freshness: try the source, never invent cache state
        if meta and now < meta.expires_at:
            recent = (now - meta.last_successful_refresh_at).total_seconds() < MIN_FORCE_INTERVAL_S
            if not force_refresh or recent:
                try:
                    banks, rows = await self.repo.load_scope(key)
                    self.counters["cache_hit"] += 1
                    return self._compose(query, banks, rows, S.CACHED, "HIT", meta, now)
                except RepositoryError as e:
                    log.warning("cache_read_failed code=%s", e.code)
        self.counters["cache_miss"] += 1
        return await self._refresh_or_fallback(query, key, meta, now)

    async def _search_eraktkosh_no_store(self, query: StockQuery) -> InventoryResponse:
        now = self._clock()
        try:
            result = await self.adapter.fetch_stock([], query.blood_group.value, query.component.value,
                                                    query.district, state=query.state)
        except SourceError as exc:
            return self._empty(query, status_for(exc), ErrorOut(
                code=exc.code,
                message=("e-RaktKosh denied access to this request. No inventory conclusion can be made."
                         if isinstance(exc, AccessNotPermitted) else
                         "e-RaktKosh could not return a usable response. This is not evidence of zero stock."),
                retryable=exc.retryable), now)
        banks, rows, warnings = normalize(result, query, "ephemeral", now)
        warnings.append("Live e-RaktKosh response; inventory is not cached by BloodSignal.")
        if not rows:
            warnings.append("No matching stock records were returned for this state/component/group. "
                            "This does not prove that no blood is available elsewhere.")
        return self._compose(query, banks, rows, S.LIVE, "BYPASS", None, now, warnings=warnings,
                             fetched_at_override=now)

    async def freshness(self, query: StockQuery) -> dict:
        key = query.scope_key(self.adapter.source_id)
        meta = await self.repo.get_cache_meta(key)
        sync = await self.repo.get_sync(self.adapter.source_id)
        now = self._clock()
        return {"cache_key_present": meta is not None,
                "last_successful_refresh_at": meta.last_successful_refresh_at if meta else None,
                "expires_at": meta.expires_at if meta else None,
                "is_fresh": bool(meta and now < meta.expires_at),
                "sync": sync.model_dump(mode="json") if sync else None}

    # ---- refresh path
    async def _refresh_or_fallback(self, query, key, meta, now) -> InventoryResponse:
        owner = new_id()
        try:
            got = await self.repo.acquire_lock(key, owner, self.s.refresh_lock_ttl_seconds)
        except RepositoryError:
            got = True      # lock store down: proceed (worst case duplicate fetch, bounded by the breaker)
            owner = ""
        if not got:
            self.counters["lock_lost"] += 1
            waited = await self._wait_for_other_refresh(key, meta)
            if waited is not None:
                try:
                    banks, rows = await self.repo.load_scope(key)
                    return self._compose(query, banks, rows, S.CACHED, "HIT", waited, self._clock(),
                                         warnings=["Another request refreshed this data moments ago."])
                except RepositoryError:
                    pass
            return await self._fallback(query, key, meta, S.UNAVAILABLE, "REFRESH_IN_PROGRESS",
                                  "A refresh is already running; try again shortly.", retryable=True)
        try:
            ref = await self._do_refresh(query, key, now)
        except Exception as e:  # noqa: BLE001 - classified below; message is never echoed raw
            self.counters["refresh_failed"] += 1
            code = e.code if isinstance(e, SourceError) else type(e).__name__
            await self._record_sync_failure(code, now)
            log.warning("refresh_failed code=%s", code)
            return await self._fallback(query, key, meta, status_for(e), code,
                                  "The source could not be refreshed.", retryable=getattr(e, "retryable", True))
        finally:
            if owner:
                try:
                    await self.repo.release_lock(key, owner)
                except RepositoryError:
                    pass                                   # lock expires on its own
        self.counters["refresh_ok"] += 1
        new_meta = CacheMeta(cache_key=key, query_parameters=self._params(query),
                             last_successful_refresh_at=now,
                             expires_at=now + timedelta(seconds=self.s.stock_cache_ttl_seconds),
                             source_status=S.LIVE.value, record_count=len(ref.rows))
        warnings = list(ref.warnings)
        if ref.persisted:
            try:
                await self.repo.put_cache_meta(new_meta)
            except RepositoryError:
                warnings.append("Retrieved data could not be cached; the next request will refetch.")
        else:
            warnings.append("Retrieved data could not be saved; showing it for this request only.")
        if not ref.rows:
            warnings.append("The source returned no matching records. This is not evidence of zero stock.")
        banks, rows = ref.banks, ref.rows
        if ref.persisted:
            try:        # one view of the scope whether it came from cache or refresh (keeps older rows, flagged)
                banks, rows = await self.repo.load_scope(key)
            except RepositoryError:
                pass
        return self._compose(query, banks, rows, S.LIVE, "REFRESHED", new_meta, now, warnings=warnings)

    async def _do_refresh(self, query, key, now) -> Refreshed:
        a = self.adapter
        result = await call_with_retry(
            lambda: a.fetch_stock([], query.blood_group.value, query.component.value, query.district,
                                  state=query.state),
            breaker=self.breaker, attempts=self.s.stock_source_max_attempts,
            base_delay_s=self.s.retry_base_delay_s, timeout_s=self.s.stock_source_timeout_s * 2,
            sleep=self._sleep)
        banks, rows, warnings = normalize(result, query, key, now)
        persisted, upserted = True, 0
        try:
            await self.repo.upsert_banks(banks)           # banks first: stock has a FK to banks
            upserted = await self.repo.upsert_stock(rows)
            await self.repo.record_sync(SyncState(
                source=a.source_id, status="OK", last_attempt_at=now, last_success_at=now,
                records_fetched=len(result.records), records_upserted=upserted))
        except RepositoryError as e:
            persisted = False
            log.warning("persist_failed code=%s", e.code)
        return Refreshed(banks, rows, warnings, len(result.records), upserted, persisted)

    async def _record_sync_failure(self, code: str, now: datetime) -> None:
        try:
            await self.repo.record_sync(SyncState(
                source=self.adapter.source_id, status="ERROR", last_attempt_at=now,
                last_error_code=code, last_error_summary=f"refresh failed ({code})"))
        except RepositoryError:
            pass

    async def _wait_for_other_refresh(self, key, meta) -> CacheMeta | None:
        before = meta.last_successful_refresh_at if meta else None
        for _ in range(int(self.s.refresh_wait_seconds / 0.25)):
            await self._sleep(0.25)
            try:
                m = await self.repo.get_cache_meta(key)
            except RepositoryError:
                return None
            if m and (before is None or m.last_successful_refresh_at > before):
                return m
        return None

    # ---- fallback
    async def _fallback(self, query, key, meta, status, code, message, *, retryable) -> InventoryResponse:
        """Refresh failed. Serve persisted rows ONLY if inside max_stale_age; else UNAVAILABLE."""
        now = self._clock()
        err = ErrorOut(code=code, message=message, retryable=retryable)
        if meta and self.s.max_stale_age_seconds > 0:
            age = (now - meta.last_successful_refresh_at).total_seconds()
            if age <= self.s.max_stale_age_seconds:
                try:
                    banks, rows = await self.repo.load_scope(key)
                except RepositoryError:
                    return self._empty(query, status, err, now)
                self.counters["stale_served"] += 1
                resp = self._compose(query, banks, rows, S.STALE, "STALE_FALLBACK", meta, now,
                                     warnings=[f"Refresh failed ({code}). Showing data last retrieved "
                                               f"{int(age)}s ago; it may be out of date."])
                return resp.model_copy(update={"refresh_error": err})
        return self._empty(query, status, err, now)

    def _empty(self, query, status, err, now, *, demo=False) -> InventoryResponse:
        return InventoryResponse(
            data=[], source=self.adapter.source_id, source_label=self.adapter.source_label,
            source_status=status, cache_status="MISS", fetched_at=None, expires_at=None, is_stale=False,
            is_demo=demo, stale_age_seconds=None, error=err, warnings=[],
            pagination=Pagination(page=query.page, page_size=query.page_size, total=0),
            disclaimer=DISCLAIMER, demo_label=DEMO_LABEL if demo else None)

    @staticmethod
    def _params(q: StockQuery) -> dict:
        return {"state": norm_text(q.state), "district": norm_text(q.district),
                "blood_group": q.blood_group.value, "component": q.component.value}

    # ---- demo (separate path; never persisted, never labelled live)
    async def _demo(self, query: StockQuery) -> InventoryResponse:
        self.counters["demo_requests"] += 1
        now = self._clock()
        banks_in = self._demo_banks(query.district) if self._demo_banks else []
        try:
            res = await self.adapter.fetch_stock([b.bank_id for b in banks_in], query.blood_group.value,
                                                 query.component.value, query.district, state=query.state)
        except SourceError as e:
            return self._empty(query, S.UNAVAILABLE, ErrorOut(code=e.code, message="Demo source failed.",
                                                                retryable=e.retryable), now, demo=True)
        res.banks = banks_in
        banks, rows, warnings = normalize(res, query, "demo", now)
        resp = self._compose(query, banks, rows, S.DEMO, "BYPASS", None, now,
                             warnings=warnings + [DEMO_LABEL], demo=True)
        return resp

    # ---- composition
    def _compose(self, query, banks, rows, status, cache_status, meta, now, *, warnings=None,
                 demo=False, fetched_at_override=None) -> InventoryResponse:
        from app.inventory.safety import enforce  # local import: safety imports models only
        refreshed_at = meta.last_successful_refresh_at if meta else fetched_at_override
        by_bank: dict[str, list[StoredStock]] = {}
        for r in rows:
            by_bank.setdefault(r.source_bank_id, []).append(r)
        needle = norm_text(query.centre_query)
        items: list[BankStockOut] = []
        for b in sorted(banks, key=lambda x: ((norm_text(x.name) or ""), x.source_bank_id)):
            if needle and needle not in (norm_text(b.name) or ""):      # text filter only, not identity
                continue
            flags = [] if b.phone else [Q.INCOMPLETE_CONTACT_DETAILS.value]
            stock = []
            for r in sorted(by_bank.get(b.source_bank_id, []), key=lambda x: (x.blood_group, x.component)):
                fl = list(r.flags)
                if refreshed_at and r.fetched_at < refreshed_at and not demo:
                    fl.append(Q.NOT_IN_LATEST_REFRESH.value)
                age = None
                if r.source_updated_at is not None and Q.FUTURE_SOURCE_TIMESTAMP.value not in fl:
                    age = max(0, int((now - r.source_updated_at).total_seconds()))
                fresh = "UNKNOWN" if age is None else (
                    "WITHIN_WINDOW" if age <= self.s.freshness_hours * 3600 else "OLD")
                stock.append(StockOut(
                    blood_group=r.blood_group, component=r.component, quantity=r.quantity,
                    quantity_unit=r.quantity_unit, quantity_state="UNKNOWN" if r.quantity is None else "REPORTED",
                    reported_status=r.reported_status, source_updated_at=r.source_updated_at,
                    source_updated_at_raw=r.source_updated_at_raw,
                    requested_state_code=r.requested_state_code,
                    requested_component_id=r.requested_component_id, fetched_at=r.fetched_at, source_age_seconds=age, source_freshness=fresh,
                    source_blood_group_label=r.source_blood_group_label,
                    source_component_label=r.source_component_label, source_url=r.source_url,
                    evidence_ref=r.evidence_ref, flags=fl))
            items.append(BankStockOut(bank=BankOut(
                bank_id=b.source_bank_id, name=b.name, address=b.address, phone=b.phone,
                phone_href=phone_href(b.phone), email=b.email, category=b.category, type=b.type,
                state=b.state, district=b.district, flags=flags, phones=b.phones, emails=b.emails,
                faxes=b.faxes, contact_raw=b.contact_raw, latitude=b.latitude, longitude=b.longitude), stock=stock))
        total = len(items)
        start = (query.page - 1) * query.page_size
        stale_age = None
        if status is S.STALE and refreshed_at:
            stale_age = max(0, int((now - refreshed_at).total_seconds()))
        resp = InventoryResponse(
            data=items[start:start + query.page_size], source=self.adapter.source_id,
            source_label=self.adapter.source_label, source_status=status, cache_status=cache_status,
            fetched_at=refreshed_at, expires_at=meta.expires_at if meta else None,
            is_stale=status is S.STALE, is_demo=demo, stale_age_seconds=stale_age,
            warnings=warnings or [], pagination=Pagination(page=query.page, page_size=query.page_size, total=total),
            disclaimer=DISCLAIMER, demo_label=DEMO_LABEL if demo else None)
        resp, suppressed = enforce(resp, now)
        self.counters["safety_suppressed"] += suppressed
        return resp
