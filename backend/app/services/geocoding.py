"""Address-first Nominatim geocoding for e-RaktKosh facilities with relay fallback + Redis caching."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from collections import OrderedDict
from typing import Any

import httpx

log = logging.getLogger("geocoding")

_FACILITY_TYPES = {"hospital", "clinic", "healthcare", "doctors", "blood_bank", "building"}
_LOCALITY_TYPES = {
    "city", "town", "village", "municipality", "county", "administrative",
    "suburb", "borough", "hamlet", "locality",
}
_LOCALITY_ADDRESS_FIELDS = (
    "district", "county", "city_district", "city", "municipality", "town",
    "village", "suburb", "state_district",
)


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[^\W_]+", value.casefold(), flags=re.UNICODE))


def _matches_area(expected: str | None, actual_values: list[object]) -> bool:
    """Match an area against structured address parts, not the free-form display label."""
    expected_tokens = _tokens(expected or "")
    if not expected_tokens:
        return True
    actual_tokens: set[str] = set()
    for value in actual_values:
        if isinstance(value, str):
            actual_tokens.update(_tokens(value))
    return expected_tokens.issubset(actual_tokens)


def area_hints(address: str | None, state: str | None) -> dict[str, str | None]:
    """Extract city and district from an e-RaktKosh address ending in `..., area, district, state`.

    e-RaktKosh format: HOSPITAL_NAME, locality/area, DISTRICT, STATE
    Only trusted when the final part matches the requested state. Returns the two parts
    before state, or (None, None) if the address is free-form or does not match state.
    """
    if not address or not state:
        return {"city": None, "district": None}
    parts = [p.strip() for p in address.split(",") if p.strip()]
    parts = [p for p in parts if p.casefold() != "india" and not re.fullmatch(r"\d{6}", p)]
    if len(parts) < 3:
        return {"city": None, "district": None}
    # Last part must match state; district is second-to-last, city/locality is third-to-last.
    if not _tokens(state) <= _tokens(parts[-1]):
        return {"city": None, "district": None}
    return {"city": parts[-3] if len(parts) >= 3 else None, "district": parts[-2]}


class NominatimGeocoder:
    def __init__(
        self,
        *,
        contact: str = "",
        base_url: str = "https://nominatim.openstreetmap.org/search",
        relay_url: str = "",
        relay_secret: str = "",
        application_url: str = "https://bloodsignal.vercel.app",
        enabled: bool = False,
        timeout_s: float = 2.0,
        max_cache_entries: int = 1000,
        cooldown_s: float = 900.0,
        redis_client: Any = None,
        clock=time.monotonic,
        sleep=asyncio.sleep,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.enabled = enabled
        self.contact = contact.strip()
        self.base_url = base_url.rstrip("?")
        self.relay_url = relay_url.rstrip("/") if relay_url else ""
        self.relay_secret = relay_secret.strip()
        self.application_url = application_url.strip().rstrip("/") or "https://bloodsignal.vercel.app"
        self.timeout_s = timeout_s
        self.max_cache_entries = max_cache_entries
        self.cooldown_s = cooldown_s
        self.redis_client = redis_client
        self._memory_cache: OrderedDict[str, tuple[float, float, str]] = OrderedDict()
        self._lookup_cache: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
        self._clock, self._sleep, self._transport = clock, sleep, transport
        self._last_request = 0.0
        self._cooldown_until = 0.0
        self._lock = asyncio.Lock()

    async def geocode(
        self,
        *,
        name: str,
        address: str | None,
        city: str | None,
        district: str | None,
        state: str | None,
        budget_s: float = 3.0,
    ) -> tuple[float, float, str] | None:
        """Resolve facility coordinates once; later searches reuse the cached result.
        Uses OpenStreetMap/Nominatim via Vercel relay (first) and direct OSM (fallback).
        Redis caches results across server restarts; in-memory cache for this session.
        """
        if not self.enabled:
            return None

        # Cache key: address + state (name alone is too variable)
        cache_key = f"geo:{address or ''}|{state or ''}".casefold()
        
        # Check in-memory cache first
        if cache_key in self._memory_cache:
            self._memory_cache.move_to_end(cache_key)
            return self._memory_cache[cache_key]
        
        # Check Redis cache if available
        if self.redis_client:
            try:
                cached = await asyncio.to_thread(self.redis_client.get, cache_key)
                if cached:
                    try:
                        lat, lon, prec = json.loads(cached)
                        result = (lat, lon, prec)
                        self._memory_cache[cache_key] = result
                        while len(self._memory_cache) > self.max_cache_entries:
                            self._memory_cache.popitem(last=False)
                        return result
                    except (json.JSONDecodeError, ValueError, TypeError):
                        pass
            except Exception as exc:
                log.debug("Redis cache lookup failed: %s", type(exc).__name__)

        hints = area_hints(address, state)
        city = city or hints["city"]
        district = district or hints["district"]
        deadline = self._clock() + max(0.0, budget_s)
        found = await self._geocode_osm(
            name=name,
            address=address,
            city=city,
            district=district,
            state=state,
            deadline=deadline,
        )

        if found is not None:
            # Store in both caches
            self._memory_cache[cache_key] = found
            while len(self._memory_cache) > self.max_cache_entries:
                self._memory_cache.popitem(last=False)
            
            if self.redis_client:
                try:
                    await asyncio.to_thread(
                        self.redis_client.setex,
                        cache_key,
                        86400,  # 24-hour TTL
                        json.dumps(found),
                    )
                except Exception as exc:
                    log.debug("Redis cache store failed: %s", type(exc).__name__)

        return found

    async def _geocode_osm(
        self,
        *,
        name: str,
        address: str | None,
        city: str | None,
        district: str | None,
        state: str | None,
        deadline: float,
    ) -> tuple[float, float, str] | None:
        """Query Nominatim: address-first (primary), then name + area fallbacks."""
        # Address-first queries: full address, then progressively stripped versions
        address_queries = self._address_queries(address, city, district, state)
        
        # Name fallback queries: only if address alone doesn't work
        name_queries = self._name_queries(name, city, district, state)

        locality_match: tuple[float, float, str] | None = None

        # Try address queries first (most likely to match exactly)
        for query in address_queries:
            if self._clock() >= deadline:
                break
            for result in await self._lookup(query, deadline):
                coordinates = self._coordinates(result)
                if coordinates is None:
                    continue
                if (
                    self._is_facility(result)
                    and self._matches_region(result, district, state)
                ):
                    return coordinates[0], coordinates[1], "FACILITY"
                if (
                    locality_match is None
                    and self._is_locality(result)
                    and self._matches_region(result, district, state)
                ):
                    locality_match = coordinates[0], coordinates[1], "LOCALITY"

        # Fall back to name-based queries only if address didn't find anything
        for query in name_queries:
            if self._clock() >= deadline:
                break
            for result in await self._lookup(query, deadline):
                coordinates = self._coordinates(result)
                if coordinates is None:
                    continue
                if (
                    self._is_facility(result)
                    and self._matches_region(result, district, state)
                ):
                    return coordinates[0], coordinates[1], "FACILITY"
                if (
                    locality_match is None
                    and self._is_locality(result)
                    and self._matches_region(result, district, state)
                ):
                    locality_match = coordinates[0], coordinates[1], "LOCALITY"

        return locality_match

    @staticmethod
    def _address_queries(
        address: str | None, city: str | None, district: str | None, state: str | None
    ) -> list[str]:
        """Build address-first queries: full address, then without hospital name, then just locality/district."""
        queries: list[str] = []
        seen: set[str] = set()

        # Full address first
        if address and state:
            q = f"{address}, {state}"
            key = q.casefold()
            if key not in seen:
                queries.append(q)
                seen.add(key)

        # Address without hospital name prefix (remove everything before first comma)
        if address and "," in address and state:
            addr_without_name = ",".join(address.split(",")[1:]).strip()
            if addr_without_name:
                q = f"{addr_without_name}, {state}"
                key = q.casefold()
                if key not in seen:
                    queries.append(q)
                    seen.add(key)

        # City/district + state
        if city and district and state:
            q = f"{city}, {district}, {state}"
            key = q.casefold()
            if key not in seen:
                queries.append(q)
                seen.add(key)

        # Just district + state
        if district and state:
            q = f"{district}, {state}"
            key = q.casefold()
            if key not in seen:
                queries.append(q)
                seen.add(key)

        return queries

    @staticmethod
    def _name_queries(
        name: str | None, city: str | None, district: str | None, state: str | None
    ) -> list[str]:
        """Fallback name-based queries only if address matching failed."""
        queries: list[str] = []
        seen: set[str] = set()

        # Hospital name + district + state
        if name and district and state:
            q = f"{name}, {district}, {state}"
            key = q.casefold()
            if key not in seen:
                queries.append(q)
                seen.add(key)

        # Hospital name + city + state
        if name and city and state:
            q = f"{name}, {city}, {state}"
            key = q.casefold()
            if key not in seen:
                queries.append(q)
                seen.add(key)

        # Hospital name + state only
        if name and state:
            q = f"{name}, {state}"
            key = q.casefold()
            if key not in seen:
                queries.append(q)
                seen.add(key)

        return queries

    @staticmethod
    def _coordinates(result: dict[str, Any]) -> tuple[float, float] | None:
        try:
            latitude, longitude = float(result["lat"]), float(result["lon"])
        except (KeyError, TypeError, ValueError):
            return None
        if (
            not math.isfinite(latitude)
            or not math.isfinite(longitude)
            or not (-90 <= latitude <= 90)
            or not (-180 <= longitude <= 180)
        ):
            return None
        return latitude, longitude

    @staticmethod
    def _is_facility(result: dict[str, Any]) -> bool:
        result_class = str(result.get("class", "")).casefold()
        result_type = str(result.get("type", "")).casefold()
        return result_class in {"amenity", "healthcare", "building"} and (
            result_type in _FACILITY_TYPES or result_class == "building"
        )

    @staticmethod
    def _is_locality(result: dict[str, Any]) -> bool:
        result_class = str(result.get("class", "")).casefold()
        result_type = str(result.get("type", "")).casefold()
        return (
            result_class == "place" and result_type in _LOCALITY_TYPES
        ) or (
            result_class == "boundary" and result_type in {"administrative", "political"}
        )

    @staticmethod
    def _matches_region(result: dict[str, Any], district: str | None, state: str | None) -> bool:
        address = result.get("address")
        if not isinstance(address, dict):
            return not district and not state
        if state and not _matches_area(state, [
            address.get("state"), address.get("state_district"),
        ]):
            return False
        if district and not _matches_area(district, [
            address.get(field) for field in _LOCALITY_ADDRESS_FIELDS
        ]):
            return False
        return True

    async def _lookup(
        self, query: str, deadline: float
    ) -> list[dict[str, Any]]:
        """Query Nominatim via relay (if configured) or direct OSM endpoint."""
        if query in self._lookup_cache:
            self._lookup_cache.move_to_end(query)
            return self._lookup_cache[query]

        async with self._lock:
            if query in self._lookup_cache:
                self._lookup_cache.move_to_end(query)
                return self._lookup_cache[query]

            now = self._clock()
            if now < self._cooldown_until or now >= deadline:
                return []

            wait = max(0.0, 1.0 - (now - self._last_request))
            if now + wait + 0.05 >= deadline:
                return []

            if wait:
                await self._sleep(wait)

            self._last_request = self._clock()
            result: list[dict[str, Any]] = []

            # Try relay first (Vercel IP, less likely to be blocked)
            if self.relay_url and self.relay_secret:
                result = await self._lookup_via_relay(query, deadline)
                if result:
                    self._lookup_cache[query] = result
                    while len(self._lookup_cache) > self.max_cache_entries:
                        self._lookup_cache.popitem(last=False)
                    return result

            # Fall back to direct OSM
            result = await self._lookup_direct(query, deadline)
            self._lookup_cache[query] = result
            while len(self._lookup_cache) > self.max_cache_entries:
                self._lookup_cache.popitem(last=False)
            return result

    async def _lookup_via_relay(
        self, query: str, deadline: float
    ) -> list[dict[str, Any]]:
        """Query Nominatim through Vercel relay."""
        try:
            remaining = max(0.0, deadline - self._clock())
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(min(self.timeout_s, remaining)),
                transport=self._transport,
                follow_redirects=False,
            ) as client:
                params: dict[str, str | int] = {
                    "q": query,
                    "format": "jsonv2",
                    "limit": 5,
                    "addressdetails": 1,
                    "namedetails": 1,
                    "countrycodes": "in",
                }
                if self.contact:
                    params["email"] = self.contact
                response = await client.get(
                    self.relay_url,
                    params=params,
                    headers={
                        "Accept": "application/json",
                        "X-Relay-Secret": self.relay_secret,
                        "User-Agent": f"BloodSignal/1.0 ({self.application_url})",
                    },
                )
            if response.status_code in (403, 429):
                log.warning("Relay returned HTTP %s; geocoding relay failing over to direct", response.status_code)
                return []
            elif response.status_code >= 400:
                return []
            else:
                payload = response.json()
                return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            log.debug("Relay geocoding lookup failed: %s", type(exc).__name__)
            return []

    async def _lookup_direct(
        self, query: str, deadline: float
    ) -> list[dict[str, Any]]:
        """Query Nominatim directly from OSM."""
        try:
            remaining = max(0.0, deadline - self._clock())
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(min(self.timeout_s, remaining)),
                transport=self._transport,
                follow_redirects=False,
            ) as client:
                params: dict[str, str | int] = {
                    "q": query,
                    "format": "jsonv2",
                    "limit": 5,
                    "addressdetails": 1,
                    "namedetails": 1,
                    "countrycodes": "in",
                }
                if self.contact:
                    params["email"] = self.contact
                response = await client.get(
                    self.base_url,
                    params=params,
                    headers={
                        "Accept": "application/json",
                        "User-Agent": (
                            f"BloodSignal/1.0 ({self.application_url}"
                            f"{'; contact: ' + self.contact if self.contact else ''})"
                        ),
                    },
                )
            if response.status_code in (403, 429):
                self._cooldown_until = self._clock() + self.cooldown_s
                log.warning("Nominatim returned HTTP %s; geocoding cooling down", response.status_code)
                return []
            elif response.status_code >= 400:
                return []
            else:
                payload = response.json()
                return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            log.info("Geocoding lookup failed: %s", type(exc).__name__)
            return []
