"""Bank discovery. Places results are directory candidates, never stock evidence."""
from __future__ import annotations

import csv
import hashlib
import io
import logging
import math
from typing import Protocol

import httpx

from app.config import Settings
from app.schemas import BloodBank, utcnow
from app.services.demo_data import demo_banks_for

log = logging.getLogger("directory")
_FIELDS = ("places.id,places.displayName,places.formattedAddress,places.nationalPhoneNumber,"
           "places.location,places.regularOpeningHours.weekdayDescriptions")


class DirectoryUnavailable(Exception):
    code = "DIRECTORY_UNAVAILABLE"


class BankDirectory(Protocol):
    async def discover(self, *, district: str | None, state: str | None,
                       origin: tuple[float, float] | None, radius_km: int) -> list[BloodBank]: ...


class DemoDirectory:
    async def discover(self, *, district, state, origin, radius_km) -> list[BloodBank]:
        return demo_banks_for(district, state)



class DataGovDirectory:
    """Government blood-bank directory adapter. Directory fields are never stock evidence."""

    def __init__(self, settings: Settings):
        self._settings = settings

    @staticmethod
    def _normalise_key(value: object) -> str:
        return " ".join(str(value or "").strip().lower().replace("_", " ").split())

    @staticmethod
    def _pick(row: dict, *names: str) -> str | None:
        normalized = {DataGovDirectory._normalise_key(k): v for k, v in row.items()}
        for name in names:
            value = normalized.get(DataGovDirectory._normalise_key(name))
            if value is not None and str(value).strip():
                return str(value).strip()
        return None

    @staticmethod
    def _usable(value: str | None) -> str | None:
        if not value:
            return None
        clean = " ".join(value.strip().split())
        return None if clean.casefold() in {"na", "n/a", "not available", "unknown", "null", "none", "nil", "-"} else clean

    @staticmethod
    def _within_radius(origin: tuple[float, float], latitude: float, longitude: float,
                       radius_km: int) -> bool:
        lat1, lon1 = map(math.radians, origin)
        lat2, lon2 = math.radians(latitude), math.radians(longitude)
        dlat, dlon = lat2 - lat1, lon2 - lon1
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        return 6371.0088 * 2 * math.asin(math.sqrt(a)) <= radius_km

    async def discover(self, *, district, state, origin, radius_km) -> list[BloodBank]:
        key = self._settings.datagov_api_key.get_secret_value()
        if not key:
            raise DirectoryUnavailable("data.gov.in API key not configured")
        url = f"{self._settings.datagov_api_base_url.rstrip('/')}/{self._settings.datagov_resource_id}"
        all_rows: list[dict] = []
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(
                    self._settings.datagov_timeout_s, connect=3.0)) as client:
                for page in range(self._settings.datagov_max_pages):
                    response = await client.get(url, params={
                        "api-key": key, "format": "json",
                        "offset": page * self._settings.datagov_page_size,
                        "limit": self._settings.datagov_page_size,
                    })
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "").lower()
                    if "csv" in content_type or response.text.lstrip().lower().startswith("blood bank name,"):
                        rows = list(csv.DictReader(io.StringIO(response.text)))
                        more = False
                    else:
                        payload = response.json()
                        if isinstance(payload, list):
                            rows, more = payload, False
                        elif isinstance(payload, dict):
                            rows = payload.get("records") or payload.get("data") or []
                            if not isinstance(rows, list):
                                raise ValueError("data.gov.in records field is not a list")
                            total = payload.get("total")
                            more = len(rows) >= self._settings.datagov_page_size and (
                                total is None or (page + 1) * self._settings.datagov_page_size < int(total))
                        else:
                            raise ValueError("unsupported data.gov.in response shape")
                    if not all(isinstance(row, dict) for row in rows):
                        raise ValueError("data.gov.in contains invalid records")
                    all_rows.extend(rows)
                    if not more or not rows:
                        break
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            log.warning("datagov_directory_failed error_class=%s", type(exc).__name__)
            raise DirectoryUnavailable(type(exc).__name__) from exc

        banks: list[BloodBank] = []
        for row in all_rows:
            name = self._usable(self._pick(row, "Blood Bank Name", "blood_bank_name", "name"))
            row_city = self._usable(self._pick(row, "City", "Town", "Village"))
            row_district = self._usable(self._pick(row, "District"))
            row_state = self._usable(self._pick(row, "State"))
            if not name:
                continue
            row_address = self._usable(self._pick(row, "Address", "Postal Address"))
            pincode = self._usable(self._pick(row, "Pincode", "PIN Code", "Pin Code", "Postal Code"))
            if district:
                query_terms = [self._normalise_key(part) for part in district.split(",") if part.strip()]
                record_location = " ".join(self._normalise_key(value) for value in
                                            (row_city, row_district, row_address, pincode) if value)
                if any(term not in record_location for term in query_terms):
                    continue
            if state and (not row_state or row_state.casefold() != state.casefold()):
                continue
            # Only a source-provided address is a facility destination. City/district
            # fields remain useful for display and filtering, but are not precise enough
            # to send someone to a facility via directions.
            address_parts = [row_address, row_city, row_district, pincode, row_state, "India"]
            address = (", ".join(dict.fromkeys(v for v in address_parts if v))
                       if row_address else None)
            phone = self._usable(
                self._pick(row, "Contact No", "Contact No.", "Contact Number", "Phone")
                or self._pick(row, "Mobile") or self._pick(row, "Helpline")
            )
            lat_text, lon_text = self._pick(row, "Latitude", "lat"), self._pick(row, "Longitude", "long", "lon")
            try:
                lat = float(lat_text) if lat_text else None
                lon = float(lon_text) if lon_text else None
                if lat is not None and not -90 <= lat <= 90: lat = None
                if lon is not None and not -180 <= lon <= 180: lon = None
            except (TypeError, ValueError):
                lat = lon = None
            if origin and lat is not None and lon is not None:
                if not self._within_radius(origin, lat, lon, radius_km):
                    continue
            elif origin and not district:
                # Without coordinates there is no safe way to locate an all-India directory
                # row near a device origin. Keep it when a district filter already makes
                # the record geographically relevant; its distance will remain unknown.
                continue
            identity = "|".join([name.casefold(), (row_district or "").casefold(),
                                 (row_state or "").casefold(), address or ""])
            bank_id = "datagov-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
            banks.append(BloodBank(bank_id=bank_id, name=name, address=address, phone=phone,
                                   latitude=lat, longitude=lon, city=row_city, district=row_district,
                                   state=row_state,
                                   source_type="live",
                                   directory_source="data.gov.in Blood Bank Directory",
                                   directory_reference=url, directory_retrieved_at=utcnow()))
        unique: dict[str, BloodBank] = {}
        for bank in banks:
            unique.setdefault(bank.bank_id, bank)
        return list(unique.values())


class FallbackDirectory:
    """Opt-in directory fallback; demo rows remain marked as demo by DemoDirectory."""

    def __init__(self, primary: BankDirectory, fallback: BankDirectory):
        self.primary, self.fallback = primary, fallback

    async def discover(self, *, district, state, origin, radius_km):
        try:
            banks = await self.primary.discover(district=district, state=state, origin=origin,
                                                radius_km=radius_km)
            if banks:
                return banks
            log.warning("directory_fallback_used source=demo reason=no_public_matches")
            return await self.fallback.discover(district=district, state=state, origin=origin,
                                                radius_km=radius_km)
        except DirectoryUnavailable:
            log.warning("directory_fallback_used source=demo reason=provider_unavailable")
            return await self.fallback.discover(district=district, state=state, origin=origin,
                                                radius_km=radius_km)


class GooglePlacesDirectory:
    """Places API (New) Text Search with a minimal field mask (controls SKU/cost)."""

    def __init__(self, api_key: str, timeout_s: float = 8.0):
        self._key, self._timeout = api_key, timeout_s

    async def discover(self, *, district, state, origin, radius_km) -> list[BloodBank]:
        body: dict = {"textQuery": f"blood bank in {district or ''} {state or ''}".strip()
                      if district or state else "blood bank",
                      "maxResultCount": 10}
        if origin:
            body["locationBias"] = {"circle": {"center": {"latitude": origin[0], "longitude": origin[1]},
                                               "radius": float(min(radius_km, 50) * 1000)}}
        headers = {"X-Goog-Api-Key": self._key, "X-Goog-FieldMask": _FIELDS}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(self._timeout, connect=3.0)) as c:
                resp = await c.post("https://places.googleapis.com/v1/places:searchText",
                                    json=body, headers=headers)
            resp.raise_for_status()
            places = resp.json().get("places", [])
        except (httpx.HTTPError, ValueError) as e:
            log.warning("places_failed", extra={"error_class": type(e).__name__})  # no key/body logged
            raise DirectoryUnavailable(type(e).__name__) from e

        banks: list[BloodBank] = []
        for p in places if isinstance(places, list) else []:
            pid = p.get("id")
            name = (p.get("displayName") or {}).get("text")
            if not pid or not name:
                continue
            loc = p.get("location") or {}
            banks.append(BloodBank(
                bank_id=f"place-{pid}", provider_place_id=pid, name=name,
                address=p.get("formattedAddress"), phone=p.get("nationalPhoneNumber"),
                latitude=loc.get("latitude"), longitude=loc.get("longitude"),
                opening_hours=(p.get("regularOpeningHours") or {}).get("weekdayDescriptions"),
                directory_source="Google Places", directory_reference=f"https://maps.google.com/?place_id={pid}",
                directory_retrieved_at=utcnow(),
                district=district, source_type="live"))
        return banks


class UnconfiguredDirectory:
    """Live mode with no Places key. Raises instead of falling back to demo banks."""

    async def discover(self, *, district, state, origin, radius_km) -> list[BloodBank]:
        raise DirectoryUnavailable("Places API key not configured")
