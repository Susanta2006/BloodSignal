"""Rate-limited Nominatim fallback geocoding for e-RaktKosh facilities.

Facility coordinates are accepted only when the result's name, feature type, district,
and state agree with the requested hospital. Locality results remain explicitly marked
LOCALITY and are never used to calculate facility distance.
"""
from __future__ import annotations

import asyncio
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
_GENERIC_NAME_TOKENS = {
    "and", "the", "of", "at", "for", "in", "a", "an", "dept", "department",
    "hospital", "hospitals", "clinic", "clinics", "blood", "bank", "centre",
    "center", "medical", "health", "services", "service", "transfusion",
    "medicine", "unit", "government", "govt",
}


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


class NominatimGeocoder:
    def __init__(self, *, contact: str = "", base_url: str = "https://nominatim.openstreetmap.org/search",
                 application_url: str = "https://bloodsignal.vercel.app", enabled: bool = False,
                 timeout_s: float = 2.0, max_cache_entries: int = 1000,
                 cooldown_s: float = 900.0, clock=time.monotonic,
                 sleep=asyncio.sleep, transport: httpx.AsyncBaseTransport | None = None):
        self.enabled = enabled
        self.contact = contact.strip()
        self.base_url = base_url.rstrip("?")
        self.application_url = application_url.strip().rstrip("/") or "https://bloodsignal.vercel.app"
        self.timeout_s = timeout_s
        self.max_cache_entries = max_cache_entries
        self.cooldown_s = cooldown_s
        self._clock, self._sleep, self._transport = clock, sleep, transport
        self._last_request = 0.0
        self._cooldown_until = 0.0
        self._lock = asyncio.Lock()
        self._cache: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()

    async def geocode(self, *, name: str, address: str | None, city: str | None,
                      district: str | None, state: str | None, budget_s: float = 3.0
                      ) -> tuple[float, float, str] | None:
        if not self.enabled:
            return None
        deadline = self._clock() + max(0.0, budget_s)
        facility_queries = self._queries(
            (name, address, city, district, state, "India"),
            (name, district, state, "India"),
            (name, city, state, "India"),
        )
        locality_queries = self._queries(
            (city, district, state, "India"),
            (district, state, "India"),
        )
        locality_match: tuple[float, float, str] | None = None

        # Keep looking after a city result. A low-precision first hit must not prevent
        # the more specific name + district + state query from finding the hospital.
        for query in facility_queries:
            if self._clock() >= deadline:
                break
            for result in await self._lookup(query, deadline):
                coordinates = self._coordinates(result)
                if coordinates is None:
                    continue
                if (
                    self._is_facility(result)
                    and self._matches_facility_name(result, name)
                    and self._matches_region(result, district, state)
                ):
                    return coordinates[0], coordinates[1], "FACILITY"
                if (
                    locality_match is None
                    and self._is_locality(result)
                    and self._matches_region(result, district, state)
                ):
                    locality_match = coordinates[0], coordinates[1], "LOCALITY"

        # Only return a locality after all facility-focused searches have been tried.
        for query in locality_queries:
            if self._clock() >= deadline:
                break
            for result in await self._lookup(query, deadline):
                coordinates = self._coordinates(result)
                if (
                    coordinates is not None
                    and self._is_locality(result)
                    and self._matches_region(result, district, state)
                ):
                    return coordinates[0], coordinates[1], "LOCALITY"
        return locality_match

    @staticmethod
    def _queries(*parts_list: tuple[str | None, ...]) -> list[str]:
        queries: list[str] = []
        seen: set[str] = set()
        for parts in parts_list:
            query = ", ".join(dict.fromkeys(
                part.strip() for part in parts if isinstance(part, str) and part.strip()
            ))
            key = query.casefold()
            if query and key not in seen:
                seen.add(key)
                queries.append(query)
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

    @staticmethod
    def _matches_facility_name(result: dict[str, Any], requested_name: str) -> bool:
        requested = _tokens(requested_name) - _GENERIC_NAME_TOKENS
        if not requested:
            return False
        returned_names: list[str] = []
        if isinstance(result.get("name"), str):
            returned_names.append(result["name"])
        namedetails = result.get("namedetails")
        if isinstance(namedetails, dict):
            returned_names.extend(value for value in namedetails.values() if isinstance(value, str))
        display_name = result.get("display_name")
        if isinstance(display_name, str):
            returned_names.append(display_name.split(",", 1)[0])
        returned: set[str] = set()
        for value in returned_names:
            returned.update(_tokens(value))
        matched = requested & returned
        # Two distinctive words catch compound facility names; a single long name token
        # also supports short official names such as JNIMS or Shija.
        return len(matched) >= 2 or any(len(token) >= 5 for token in matched)

    async def _lookup(self, query: str, deadline: float) -> list[dict[str, Any]]:
        if query in self._cache:
            self._cache.move_to_end(query)
            return self._cache[query]
        async with self._lock:
            if query in self._cache:
                self._cache.move_to_end(query)
                return self._cache[query]
            now = self._clock()
            if now < self._cooldown_until or now >= deadline:
                return []
            wait = max(0.0, 1.0 - (now - self._last_request))
            if now + wait + 0.05 >= deadline:
                return []
            if wait:
                await self._sleep(wait)
            self._last_request = self._clock()
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(min(self.timeout_s, max(0.2, deadline-self._clock()))),
                                             transport=self._transport, follow_redirects=False) as client:
                    params: dict[str, str | int] = {
                        "q": query, "format": "jsonv2", "limit": 5,
                        "addressdetails": 1, "namedetails": 1, "countrycodes": "in",
                    }
                    if self.contact:
                        params["email"] = self.contact
                    response = await client.get(self.base_url, params=params,
                                                headers={"Accept": "application/json",
                                                         "User-Agent": (
                                                             f"BloodSignal/1.0 ({self.application_url}"
                                                             f"{'; contact: ' + self.contact if self.contact else ''})"
                                                         )})
                if response.status_code in (403, 429):
                    self._cooldown_until = self._clock() + self.cooldown_s
                    log.warning("Nominatim returned HTTP %s; geocoding cooling down", response.status_code)
                    result = []
                elif response.status_code >= 400:
                    result = []
                else:
                    payload = response.json()
                    result = [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                log.info("Geocoding lookup failed non-fatally: %s", type(exc).__name__)
                result = []
            self._cache[query] = result
            while len(self._cache) > self.max_cache_entries:
                self._cache.popitem(last=False)
            return result
