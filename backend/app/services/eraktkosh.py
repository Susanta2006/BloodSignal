from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from app.schemas import BloodBank, NormalizedStockRecord, utcnow
from app.services.geocoding import NominatimGeocoder
from app.services.stock_source import (
    AccessNotPermitted,
    SourceMalformed,
    SourceRateLimited,
    SourceRequestRejected,
    SourceResult,
    SourceStatusInfo,
    SourceUnavailable,
)

log = logging.getLogger("eraktkosh")
ERAKTKOSH_SOURCE_ID = "eraktkosh"
ERAKTKOSH_LABEL = "e-RaktKosh"
OFFICIAL_URL = "https://eraktkosh.mohfw.gov.in/eraktkoshPortal/eraktkosh/blood-availability"
PARSER_VERSION = "eraktkosh-bulk-json-1"

# Source names/IDs are preserved exactly as supplied in the supplied catalog.
STATE_CODES: dict[str, str] = {
    "Tripura": "16", "Andaman and Nicobar Islands": "35", "Andhra Pradesh": "28",
    "Arunachal Pradesh": "12", "Assam": "18", "Bihar": "10", "Chandigarh": "94",
    "Chattisgarh": "22", "Dadra And Nagar Haveli And Daman And Diu": "25", "Delhi": "97",
    "Goa": "30", "Gujarat": "24", "Haryana": "96", "Himachal Pradesh": "92",
    "Jammu and Kashmir": "91", "Jharkhand": "20", "Karnataka": "29", "Kerala": "32",
    "Ladakh": "37", "Lakshadweep": "31", "Madhya Pradesh": "23", "Maharastra": "27",
    "Manipur": "14", "Meghalaya": "17", "Mizoram": "15", "Nagaland": "13",
    "Odisha": "21", "Puducherry": "34", "Punjab": "93", "Rajasthan": "98",
    "Sikkim": "11", "Tamil Nadu": "33", "Telengana": "36", "Uttarakhand": "95",
    "Uttar Pradesh": "99", "West Bengal": "19",
}
COMPONENT_CODES: dict[str, str] = {
    "Cryo Poor Plasma": "21", "Cryoprecipitate": "17", "Fresh frozen Plasma": "13",
    "Irradiated RBC": "29", "Leukoreduced Rbc": "30", "Packed Red Blood Cells": "12",
    "Plasma": "19", "Platelet Concentrate": "20", "Platelate Rich Plasma": "16",
    "Random donor platelets": "23", "Sagm Packed Red Blood Cells": "28",
    "Single donor Plasma": "18", "Single Donor Platelets": "14", "Whole Blood": "11",
}

# Existing request contract remains stable. These are exact source component selections, not
# clinical compatibility substitutions. The UI should label the selected source component clearly.
INTERNAL_COMPONENT_TO_SOURCE = {
    "cryo_poor_plasma": "Cryo Poor Plasma",
    "cryoprecipitate": "Cryoprecipitate",
    "fresh_frozen_plasma": "Fresh frozen Plasma",
    "irradiated_rbc": "Irradiated RBC",
    "leukoreduced_rbc": "Leukoreduced Rbc",
    "prbc": "Packed Red Blood Cells",
    "plasma": "Plasma",
    "platelet_concentrate": "Platelet Concentrate",
    "platelet_rich_plasma": "Platelate Rich Plasma",
    "random_donor_platelets": "Random donor platelets",
    "sagm_packed_red_blood_cells": "Sagm Packed Red Blood Cells",
    "single_donor_plasma": "Single donor Plasma",
    "single_donor_platelets": "Single Donor Platelets",
    "whole_blood": "Whole Blood",
    # Legacy broad values are retained for old clients but map to one exact source component only.
    "platelets": "Platelet Concentrate",
}
_GROUPS = {
    "o-ve": "O-", "o+ve": "O+", "a-ve": "A-", "a+ve": "A+",
    "b-ve": "B-", "b+ve": "B+", "ab-ve": "AB-", "ab+ve": "AB+",
    "o-": "O-", "o+": "O+", "a-": "A-", "a+": "A+",
    "b-": "B-", "b+": "B+", "ab-": "AB-", "ab+": "AB+",
}
_KNOWN_UNSUPPORTED_GROUPS = {"oh-ve", "oh+ve"}


def _key(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def _clean(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (str, int, float)):
        text = str(value).strip()
        return text or None
    return None


def _map_group(raw: str) -> tuple[str | None, str]:
    # Normalize verified spacing/case variants, but keep Oh (Bombay phenotype) distinct from O.
    k = re.sub(r"\s+", "", raw.casefold()).replace("+ve", "+ve").replace("-ve", "-ve")
    if k in _GROUPS:
        return _GROUPS[k], "ok"
    if k in _KNOWN_UNSUPPORTED_GROUPS:
        return None, "unsupported"
    return None, "invalid"


def _parse_qty_entries(raw: Any) -> list[tuple[str, str | None, int | None]]:
    """Parse `O+Ve : 38, A-Ve : 2`; None/malformed is unknown, never zero."""
    text = _clean(raw)
    if not text:
        return []
    out: list[tuple[str, str | None, int | None]] = []
    for item in text.split(","):
        if ":" not in item:
            out.append((item.strip(), None, None))
            continue
        label, qty_text = item.split(":", 1)
        label = label.strip()
        qty_text = qty_text.strip()
        qty: int | None
        if re.fullmatch(r"\d+", qty_text):
            qty = int(qty_text)
        else:
            qty = None
        out.append((label, label or None, qty))
    return out


def _contacts(raw: Any) -> tuple[str | None, str | None, list[str], list[str]]:
    """Extract labelled phone/email/fax values; never mistake fax for phone."""
    text = _clean(raw)
    if not text or text == "-":
        return None, None, [], []
    phone_values: list[str] = []
    emails = re.findall(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", text, re.I)
    faxes: list[str] = []
    for match in re.finditer(r"\b(phone|mobile|contact|fax)\s*:\s*([^,;]+)", text, re.I):
        kind, value = match.group(1).casefold(), match.group(2).strip()
        if kind == "fax":
            faxes.append(value)
        elif value and value not in {"-", "N/A", "NA"}:
            # Validate syntax/digit count; don't invent country codes or digits.
            digits = re.sub(r"\D", "", value)
            if re.fullmatch(r"\+?[0-9().\s-]+", value) and 7 <= len(digits) <= 15:
                phone_values.append(" ".join(value.split()))
    return (phone_values[0] if phone_values else None,
            emails[0] if emails else None, phone_values, faxes)


def _source_time(raw: Any, timezone_name: str | None) -> datetime | None:
    value = _clean(raw)
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        if not timezone_name:
            return None
        try:
            dt = dt.replace(tzinfo=ZoneInfo(timezone_name))
        except (ZoneInfoNotFoundError, ValueError):
            # India has no DST. Keep naive e-RaktKosh timestamps parseable on minimal
            # Windows/Linux images that lack an OS tz database, without masking bad zones.
            if timezone_name == "Asia/Kolkata":
                dt = dt.replace(tzinfo=timezone(timedelta(hours=5, minutes=30)))
            else:
                log.warning("Unable to resolve source timezone %r; timestamp remains unknown", timezone_name)
                return None
    return dt.astimezone(timezone.utc)


def _component_entries(hospital: dict[str, Any], wanted_component: str) -> tuple[str, dict[str, Any]] | None:
    components = hospital.get("components")
    if not isinstance(components, dict):
        return None
    wanted = _key(wanted_component)
    for label, value in components.items():
        if _key(str(label)) == wanted and isinstance(value, dict):
            return str(label), value
    return None


class ERaktKoshAdapter:
    source_id = ERAKTKOSH_SOURCE_ID
    source_display_name = "e-RaktKosh (Ministry of Health & Family Welfare / C-DAC)"
    source_type = "live"
    source_label = ERAKTKOSH_LABEL
    official_url = OFFICIAL_URL
    provides_directory = True
    no_store = True

    def __init__(self, *, base_url: str = OFFICIAL_URL, api_key: str = "", contract_file: str = "",
                 access_verified: bool = True, timeout_s: float = 30.0, probe_ttl_s: int = 60,
                 source_timezone: str | None = "Asia/Kolkata",
                 clock: Callable[[], datetime] = utcnow,
                 transport: httpx.AsyncBaseTransport | None = None,
                 sleep: Callable[[float], Any] = asyncio.sleep,
                 geocoder: NominatimGeocoder | None = None, geocoder_candidate_limit: int = 10,
                 geocoder_time_budget_s: float = 3.0,
                 relay_url: str = "", relay_secret: str = ""):
        # Keep the endpoint fixed to the verified host/path. Do not allow arbitrary proxy URLs.
        self._base = OFFICIAL_URL
        # Optional operator-owned relay (fixed at startup from settings, never from user input).
        # It must run the same fixed request from an allowed network region.
        self._relay_url = relay_url.strip()
        self._relay_secret = relay_secret
        self._timeout = timeout_s
        self._clock = clock
        self._transport = transport
        self._sleep = sleep
        # e-RaktKosh timestamps are timezone-naive and the portal is India-specific.
        # Empty environment variables must not disable parsing and turn every record UNKNOWN.
        self._timezone = source_timezone or "Asia/Kolkata"
        self._semaphore = asyncio.Semaphore(2)
        self._inflight: dict[tuple[str, str], asyncio.Task[SourceResult]] = {}
        self._inflight_lock = asyncio.Lock()
        self._retry_after_until = 0.0
        self._retry_after_clock = time.monotonic
        self._geocoder = geocoder
        self._geocoder_candidate_limit = geocoder_candidate_limit
        self._geocoder_time_budget_s = geocoder_time_budget_s

    def configured(self) -> tuple[bool, str]:
        return True, "fixed read-only e-RaktKosh bulk endpoint configured; source access is verified per search"

    async def status(self) -> SourceStatusInfo:
        # Never fetch all state/component combinations at startup just for a health probe.
        return SourceStatusInfo("READY", "e-RaktKosh endpoint configured; live access is checked on each search")

    async def fetch_stock(self, bank_ids, blood_group, component, district, *, state=None,
                          quantity=None) -> SourceResult:
        state_code = _lookup_code(STATE_CODES, state)
        source_component = INTERNAL_COMPONENT_TO_SOURCE.get(component)
        component_id = _lookup_code(COMPONENT_CODES, source_component)
        if not state_code:
            raise SourceRequestRejected("Select a state from the supported e-RaktKosh state catalog")
        if not component_id:
            raise SourceRequestRejected("Selected component has no verified e-RaktKosh component ID")
        key = (state_code, component_id)
        async with self._inflight_lock:
            task = self._inflight.get(key)
            if task is None:
                task = asyncio.create_task(self._fetch_parse(state_code, component_id, source_component or ""))
                self._inflight[key] = task
        try:
            result = await task
            # Apply selected blood group locally after the bulk source response is parsed.
            selected = [r.model_copy(update={"component": component}) for r in result.records
                        if r.blood_group == blood_group]
            # The endpoint omits district IDs. Apply only a transparent address-text filter; do
            # not assign/infer a district ID from an address. If the address does not mention the
            # requested area, exclude it rather than implying that it is in the requested district.
            if district:
                needle = _key(district)
                matching_banks = {b.bank_id for b in result.banks
                                  if needle in _key(b.address or "")}
                selected = [r for r in selected if r.bank_source_id in matching_banks]
                banks = [b for b in result.banks if b.bank_id in matching_banks]
            else:
                banks = result.banks
            # Geocode only facilities represented by matching stock records, bounded by both
            # candidate count and wall-clock budget. Failures are intentionally non-fatal.
            if self._geocoder and self._geocoder.enabled and banks:
                import time as _time
                deadline = _time.monotonic() + self._geocoder_time_budget_s
                relevant_ids = {r.bank_source_id for r in selected}
                updated_banks = []
                count = 0
                for bank in banks:
                    valid_coordinate_pair = (
                        isinstance(bank.latitude, (int, float))
                        and not isinstance(bank.latitude, bool)
                        and isinstance(bank.longitude, (int, float))
                        and not isinstance(bank.longitude, bool)
                        and -90 <= bank.latitude <= 90
                        and -180 <= bank.longitude <= 180
                    )
                    needs_geocoding = (
                        not valid_coordinate_pair or bank.location_precision == "LOCALITY"
                    )
                    if (bank.bank_id in relevant_ids and needs_geocoding and
                            count < self._geocoder_candidate_limit and _time.monotonic() < deadline):
                        count += 1
                        try:
                            found = await self._geocoder.geocode(
                                name=bank.name, address=bank.address, city=bank.city,
                                district=district or bank.district, state=state,
                                budget_s=max(0.0, deadline - _time.monotonic()))
                            if found:
                                lat, lon, precision = found
                                bank = bank.model_copy(update={
                                    "latitude": lat, "longitude": lon,
                                    "location_precision": precision,
                                     "location_note": ("Approximate OpenStreetMap/Nominatim town/district centre; not the facility location. © OpenStreetMap contributors. Distance is approximate, straight-line, not road distance."
                                                      if precision == "LOCALITY" else
                                                       "Facility match from OpenStreetMap/Nominatim. © OpenStreetMap contributors. Distance is approximate, straight-line, not road distance.")})
                        except Exception as exc:  # geocoding must never fail the stock search
                            log.info("Non-fatal geocoding error: %s", type(exc).__name__)
                    updated_banks.append(bank)
                banks = updated_banks
            return result.model_copy(update={"records": selected, "banks": banks,
                                             "checked_bank_ids": [b.bank_id for b in banks]})
        finally:
            async with self._inflight_lock:
                if self._inflight.get(key) is task and task.done():
                    self._inflight.pop(key, None)

    async def _fetch_parse(self, state_code: str, component_id: str, component_name: str) -> SourceResult:
        params = {"stateCode": state_code, "componentId": component_id}
        headers = {"Accept": "application/json", "User-Agent": "BloodSignal/1.0 (read-only hackathon availability search)"}
        target = self._base
        if self._relay_url:
            target = self._relay_url
            headers["X-Relay-Secret"] = self._relay_secret
        remaining = self._retry_after_until - self._retry_after_clock()
        if remaining > 0:
            raise SourceRateLimited(f"e-RaktKosh retry window active; wait about {int(remaining) + 1}s")
        try:
            async with self._semaphore:
                async with httpx.AsyncClient(timeout=httpx.Timeout(self._timeout, connect=15.0),
                                             transport=self._transport, follow_redirects=False) as client:
                    response = await client.get(target, params=params, headers=headers)
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
            log.warning(
                "e-RaktKosh request failed: error_type=%s state_code=%s component_id=%s",
                type(exc).__name__,
                state_code,
                component_id,
            )
            raise SourceUnavailable(type(exc).__name__) from exc
        
        except httpx.HTTPError as exc:
            log.warning(
                "e-RaktKosh HTTP client error: error_type=%s state_code=%s component_id=%s",
                type(exc).__name__,
                state_code,
                component_id,
            )
            raise SourceUnavailable(type(exc).__name__) from exc

        status = response.status_code
        if status in (401, 403) or 300 <= status < 400:
            # Do not retry access restrictions or follow authentication redirects.
            raise AccessNotPermitted(f"e-RaktKosh returned HTTP {status}; automated access stopped")
        if status == 429:
            retry_after = response.headers.get("Retry-After", "60")
            try:
                delay = max(1.0, min(900.0, float(retry_after)))
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(retry_after)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=timezone.utc)
                    delay = max(1.0, min(900.0, (retry_at - datetime.now(timezone.utc)).total_seconds()))
                except (TypeError, ValueError, OverflowError):
                    delay = 60.0
            self._retry_after_until = self._retry_after_clock() + delay
            raise SourceRateLimited(f"e-RaktKosh rate limited the request; retry after {int(delay)}s")
        if status >= 500:
            raise SourceUnavailable(f"HTTP {status}")
        if status >= 400:
            raise SourceRequestRejected(f"e-RaktKosh returned HTTP {status}")
        content_type = response.headers.get("content-type", "").casefold()
        body_start = response.text.lstrip()[:40].casefold()
        if "json" not in content_type and not body_start.startswith(("[", "{")):
            raise SourceMalformed("e-RaktKosh returned non-JSON content (possibly an access/interstitial page)")
        try:
            payload = response.json()
        except ValueError as exc:
            raise SourceMalformed("e-RaktKosh response was not valid JSON") from exc
        return self._parse(payload, state_code, component_id, component_name)

    def _parse(self, payload: Any, state_code: str, component_id: str, component_name: str) -> SourceResult:
        if not isinstance(payload, list):
            raise SourceMalformed("e-RaktKosh JSON schema changed: expected a list of hospital records")
        now = self._clock()
        banks: dict[str, BloodBank] = {}
        records: list[NormalizedStockRecord] = []
        invalid_rows = 0
        seen: dict[tuple[str, str, str], tuple[int | None, str, int]] = {}
        for hospital in payload:
            if not isinstance(hospital, dict):
                invalid_rows += 1
                continue
            name = _clean(hospital.get("hospitalname"))
            if not name:
                invalid_rows += 1
                continue
            address = _clean(hospital.get("hospitaladd"))
            source_id = _clean(hospital.get("hospitalCode"))
            if not source_id:
                # Do not merge similarly named hospitals; records without the source identity are
                # skipped rather than assigned a fabricated identity.
                invalid_rows += 1
                continue
            bank_id = f"eraktkosh-{source_id}"
            phone, email, phones, faxes = _contacts(hospital.get("hospitalcontact"))
            # Preserve source-provided identity and address. District/coordinates are not inferred.
            banks[bank_id] = BloodBank(bank_id=bank_id, name=name, address=address, phone=phone,
                                       phones=phones, emails=[email] if email else [], faxes=faxes,
                                       contact_raw=_clean(hospital.get("hospitalcontact")),
                                       facility_type=_clean(hospital.get("hospitalType")) or _clean(hospital.get("type")),
                                       source_type="live", district=None)
            found = _component_entries(hospital, component_name)
            if found is None:
                # A valid hospital without the selected component is not evidence of zero stock.
                continue
            source_component_label, component_data = found
            # e-RaktKosh deployments have exposed entrydate at the hospital row and,
            # in some payload variants, beside the component stock object. Preserve the
            # source's literal timestamp and parse it using the configured India timezone.
            raw_time = _clean(hospital.get("entrydate")) or _clean(component_data.get("entrydate")) or _clean(component_data.get("updated_at")) or _clean(component_data.get("lastUpdated"))
            updated = _source_time(raw_time, self._timezone)
            for field, status in (("available_WithQty", "available"), ("not_available_WithQty", "unavailable")):
                raw_list = component_data.get(field)
                if raw_list is None:
                    continue
                if not isinstance(raw_list, str):
                    invalid_rows += 1
                    continue
                for raw_label, _, qty in _parse_qty_entries(raw_list):
                    group, group_state = _map_group(raw_label)
                    if group_state == "unsupported":
                        continue
                    if group_state != "ok":
                        # Keep the row as unknown group for audit but it cannot be matched.
                        group = None
                    normalized_group_key = group or re.sub(r"\s+", "", raw_label.casefold())
                    record_key = (bank_id, source_component_label, normalized_group_key)
                    raw_row = {"hospitalCode": source_id, "component": source_component_label,
                               "group_label": raw_label, "quantity": qty, "status": status,
                               "entrydate": raw_time}
                    row_hash = hashlib.sha256(json.dumps(raw_row, sort_keys=True).encode()).hexdigest()
                    old = seen.get(record_key)
                    if old is not None:
                        if old[0] != qty or old[1] != status:
                            invalid_rows += 1
                            index = old[2]
                            prior = records[index]
                            records[index] = prior.model_copy(update={
                                "quantity": None, "reported_status": None,
                                "limitations": prior.limitations + [
                                    "Conflicting duplicate source entries; quantity/status withheld"],
                                "raw_record_hash": hashlib.sha256(
                                    (prior.raw_record_hash + row_hash).encode()).hexdigest(),
                            })
                            seen[record_key] = (None, "conflicting", index)
                        continue
                    limitations: list[str] = []
                    if qty is None:
                        limitations.append("Quantity missing or malformed; availability is unknown")
                    if updated is None:
                        limitations.append("Source update timestamp missing/unparseable; freshness unknown")
                    if group_state != "ok":
                        limitations.append(f"Unrecognized source blood-group label: {raw_label}")
                    if not raw_time:
                        limitations.append("Source entrydate not provided")
                    seen[record_key] = (qty, status, len(records))
                    records.append(NormalizedStockRecord(
                        bank_source_id=bank_id, source_id=self.source_id, blood_group=group,
                        component=_internal_component(source_component_label), quantity=float(qty) if qty is not None else None,
                        quantity_unit="units", reported_status=status, source_updated_at=updated,
                        retrieved_at=now, source_record_id=f"{source_id}:{source_component_label}:{raw_label}",
                        source_url=self.official_url, raw_record_hash=row_hash, parser_version=PARSER_VERSION,
                        limitations=limitations, source_updated_at_raw=raw_time,
                        requested_state_code=state_code, requested_component_id=component_id,
                        source_blood_group_label=raw_label,
                        source_component_label=source_component_label))
        return SourceResult(source_id=self.source_id, source_display_name=self.source_display_name,
                            source_type="live", source_label=self.source_label, official_url=self.official_url,
                            records=records, banks=list(banks.values()), checked_bank_ids=list(banks),
                            invalid_rows=invalid_rows)


def _lookup_code(catalog: dict[str, str], value: str | None) -> str | None:
    if not value:
        return None
    folded = _key(value)
    for label, code in catalog.items():
        if _key(label) == folded:
            return code
    # Corrected display spellings are aliases only; source labels/IDs remain unchanged in catalogs.
    if catalog is STATE_CODES:
        aliases = {
            "maharashtra": "Maharastra", "chhattisgarh": "Chattisgarh",
            "telangana": "Telengana", "dadra and nagar haveli and daman and diu":
                "Dadra And Nagar Haveli And Daman And Diu",
        }
        canonical = aliases.get(folded)
        if canonical:
            return catalog.get(canonical)
    return None


def _internal_component(source_label: str) -> str | None:
    # Canonical values prefer the exact source-specific option; fetch_stock replaces this with
    # the requested value when serving a legacy alias, without changing the source label.
    reverse = {
        "cryo poor plasma": "cryo_poor_plasma", "cryoprecipitate": "cryoprecipitate",
        "fresh frozen plasma": "fresh_frozen_plasma", "irradiated rbc": "irradiated_rbc",
        "leukoreduced rbc": "leukoreduced_rbc", "packed red blood cells": "prbc",
        "plasma": "plasma", "platelet concentrate": "platelet_concentrate",
        "platelate rich plasma": "platelet_rich_plasma",
        "random donor platelets": "random_donor_platelets",
        "sagm packed red blood cells": "sagm_packed_red_blood_cells",
        "single donor plasma": "single_donor_plasma",
        "single donor platelets": "single_donor_platelets", "whole blood": "whole_blood",
    }
    return reverse.get(_key(source_label))
