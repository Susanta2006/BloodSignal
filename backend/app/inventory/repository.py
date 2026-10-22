"""Persistence interface for the inventory cache and its process-local memory implementation.

Agents and routes never touch these directly: only InventoryService writes.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable, Protocol

from app.inventory.models import CacheMeta, StoredBank, StoredStock, SyncState
from app.schemas import utcnow

MAX_SCOPE_ROWS = 5000


class RepositoryError(Exception):
    """Any storage failure. Message is safe to log: never contains keys or bodies."""
    code = "DATABASE_UNAVAILABLE"


class InventoryRepository(Protocol):
    async def ping(self) -> None: ...
    async def upsert_banks(self, banks: list[StoredBank]) -> int: ...
    async def upsert_stock(self, rows: list[StoredStock]) -> int: ...
    async def load_scope(self, scope_key: str) -> tuple[list[StoredBank], list[StoredStock]]: ...
    async def list_banks(self, source: str) -> list[StoredBank]: ...
    async def bank_stock(self, source: str, source_bank_id: str) -> list[StoredStock]: ...
    async def get_cache_meta(self, cache_key: str) -> CacheMeta | None: ...
    async def put_cache_meta(self, meta: CacheMeta) -> None: ...
    async def record_sync(self, state: SyncState) -> None: ...
    async def get_sync(self, source: str) -> SyncState | None: ...
    async def acquire_lock(self, key: str, owner: str, ttl_s: int) -> bool: ...
    async def release_lock(self, key: str, owner: str) -> None: ...


class MemoryInventoryRepository:
    """Reference implementation for tests/local dev. NOT durable and NOT shared across instances,
    and is not shared across processes."""

    def __init__(self, clock: Callable[[], datetime] = utcnow):
        self._clock = clock
        self.banks: dict[tuple[str, str], StoredBank] = {}
        self.stock: dict[tuple, StoredStock] = {}
        self.meta: dict[str, CacheMeta] = {}
        self.sync: dict[str, SyncState] = {}
        self.locks: dict[str, tuple[str, datetime]] = {}
        self.fail: Exception | None = None            # test hook: simulate a DB outage

    def _chk(self) -> None:
        if self.fail:
            raise RepositoryError("simulated database failure")

    async def ping(self) -> None:
        self._chk()

    async def upsert_banks(self, banks):
        self._chk()
        for b in banks:
            old = self.banks.get((b.source, b.source_bank_id))
            if old:
                b = b.model_copy(update={"first_seen_at": old.first_seen_at})
            self.banks[(b.source, b.source_bank_id)] = b
        return len(banks)

    async def upsert_stock(self, rows):
        self._chk()
        for r in rows:
            self.stock[r.row_key] = r
        return len(rows)

    async def load_scope(self, scope_key):
        self._chk()
        rows = [r for r in self.stock.values() if r.scope_key == scope_key][:MAX_SCOPE_ROWS]
        ids = {(r.source, r.source_bank_id) for r in rows}
        return [self.banks[i] for i in ids if i in self.banks], rows

    async def list_banks(self, source):
        self._chk()
        return [b for b in self.banks.values() if b.source == source]

    async def bank_stock(self, source, source_bank_id):
        self._chk()
        return [r for r in self.stock.values() if r.source == source and r.source_bank_id == source_bank_id]

    async def get_cache_meta(self, cache_key):
        self._chk()
        return self.meta.get(cache_key)

    async def put_cache_meta(self, meta):
        self._chk()
        self.meta[meta.cache_key] = meta

    async def record_sync(self, state):
        self._chk()
        old = self.sync.get(state.source)
        if state.last_success_at is None and old:           # failure must not erase the last success
            state = state.model_copy(update={"last_success_at": old.last_success_at})
        self.sync[state.source] = state

    async def get_sync(self, source):
        self._chk()
        return self.sync.get(source)

    async def acquire_lock(self, key, owner, ttl_s):
        self._chk()
        now, cur = self._clock(), self.locks.get(key)
        if cur and cur[1] > now:
            return False
        self.locks[key] = (owner, now + timedelta(seconds=ttl_s))
        return True

    async def release_lock(self, key, owner):
        cur = self.locks.get(key)
        if cur and cur[0] == owner:
            del self.locks[key]


