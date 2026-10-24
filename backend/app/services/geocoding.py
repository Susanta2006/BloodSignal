"""Opt-in, policy-conscious Nominatim geocoding for e-RaktKosh facilities.

Coordinates are explicitly classified as FACILITY (facility-level result) or LOCALITY
(town/district centre). A lookup failure is non-fatal and never changes stock evidence.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from typing import Any

import httpx

log = logging.getLogger("geocoding")


class NominatimGeocoder:
    def __init__(self, *, contact: str, base_url: str = "https://nominatim.openstreetmap.org/search",
                 enabled: bool = False, timeout_s: float = 2.0, max_cache_entries: int = 1000,
                 cooldown_s: float = 900.0, clock=time.monotonic,
                 sleep=asyncio.sleep, transport: httpx.AsyncBaseTransport | None = None):
        self.enabled = enabled
        self.contact = contact.strip()
        self.base_url = base_url.rstrip("?")
        self.timeout_s = timeout_s
        self.max_cache_entries = max_cache_entries
        self.cooldown_s = cooldown_s
        self._clock, self._sleep, self._transport = clock, sleep, transport
        self._last_request = 0.0
        self._cooldown_until = 0.0
        self._lock = asyncio.Lock()
        self._cache: OrderedDict[str, dict[str, Any] | None] = OrderedDict()

    async def geocode(self, *, name: str, address: str | None, city: str | None,
                      district: str | None, state: str | None, budget_s: float = 3.0
                      ) -> tuple[float, float, str] | None:
        if not self.enabled:
            return None
        deadline = self._clock() + max(0.0, budget_s)
        facility_query = ", ".join(x.strip() for x in (name, address, city, district, state, "India") if x and x.strip())
        locality_query = ", ".join(x.strip() for x in (city, district, state, "India") if x and x.strip())
        for query, precision in ((facility_query, "FACILITY"), (locality_query, "LOCALITY")):
            if not query or self._clock() >= deadline:
                break
            result = await self._lookup(query, deadline)
            if result:
                try:
                    lat, lon = float(result["lat"]), float(result["lon"])
                    if -90 <= lat <= 90 and -180 <= lon <= 180:
                        # Do not call a city/town result a facility just because the query
                        # included a hospital name. Trust the returned feature classification.
                        result_class = str(result.get("class", "")).casefold()
                        result_type = str(result.get("type", "")).casefold()
                        facility_types = {"hospital", "clinic", "healthcare", "doctors", "blood_bank", "building"}
                        is_facility = result_class in {"amenity", "healthcare", "building"} and (
                            result_type in facility_types or result_class == "building")
                        actual_precision = "FACILITY" if precision == "FACILITY" and is_facility else "LOCALITY"
                        return lat, lon, actual_precision
                except (KeyError, TypeError, ValueError):
                    continue
        return None

    async def _lookup(self, query: str, deadline: float) -> dict[str, Any] | None:
        if query in self._cache:
            self._cache.move_to_end(query)
            return self._cache[query]
        async with self._lock:
            if query in self._cache:
                self._cache.move_to_end(query)
                return self._cache[query]
            now = self._clock()
            if now < self._cooldown_until or now >= deadline:
                return None
            wait = max(0.0, 1.0 - (now - self._last_request))
            if now + wait + 0.05 >= deadline:
                return None
            if wait:
                await self._sleep(wait)
            self._last_request = self._clock()
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(min(self.timeout_s, max(0.2, deadline-self._clock()))),
                                             transport=self._transport, follow_redirects=False) as client:
                    response = await client.get(self.base_url, params={"q": query, "format": "jsonv2", "limit": 1,
                                                                         "addressdetails": 0, "email": self.contact},
                                                headers={"Accept": "application/json",
                                                         "User-Agent": (f"BloodSignal/1.0 (contact: {self.contact})"
                                                                        if self.contact else "BloodSignal/1.0 (hospital directory geocoding)" )})
                if response.status_code in (403, 429):
                    self._cooldown_until = self._clock() + self.cooldown_s
                    log.warning("Nominatim returned HTTP %s; geocoding cooling down", response.status_code)
                    result = None
                elif response.status_code >= 400:
                    result = None
                else:
                    payload = response.json()
                    result = payload[0] if isinstance(payload, list) and payload and isinstance(payload[0], dict) else None
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                log.info("Geocoding lookup failed non-fatally: %s", type(exc).__name__)
                result = None
            self._cache[query] = result
            while len(self._cache) > self.max_cache_entries:
                self._cache.popitem(last=False)
            return result
