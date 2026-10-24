from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx
import pytest

from app.services.eraktkosh import (
    COMPONENT_CODES,
    OFFICIAL_URL,
    STATE_CODES,
    ERaktKoshAdapter,
    _map_group,
    _parse_qty_entries,
)
from app.services.stock_source import AccessNotPermitted, SourceMalformed, SourceRateLimited, SourceRequestRejected

FIXTURE = [{
    "hospitalname": "Indira Gandhi Memorial (Igm) Hospital ",
    "hospitaladd": "Akhaura Road, , Opposite of TB hall, Agartala, West Tripura, Tripura",
    "hospitalcontact": "Phone: 7005448827 ,Fax: 1234567890, Email: igmtripurabloodbank@gmail.com",
    "hospitalCode": "25002", "hospitalType": "Govt.", "entrydate": "2026-10-21 12:31:06",
    "offline": "1", "moderateStock": "0", "type": "Blood Bank", "compId": None,
    "count": None, "available_WithQty": None,
    "components": {"Packed Red Blood Cells": {
        "not_available_WithQty": "Oh+VE : 0, O-Ve : 0, B-Ve : 0, Oh-VE : 0",
        "available_WithQty": "O+Ve : 38, A+Ve : 2, AB+Ve : 11, B+Ve : 5, A-Ve : 2, AB-Ve : 1",
    }},
}]
NOW = datetime(2026, 10, 22, 6, 0, tzinfo=timezone.utc)


def adapter(handler, **kwargs):
    return ERaktKoshAdapter(
        timeout_s=2, clock=lambda: NOW, sleep=lambda _: asyncio.sleep(0),
        transport=httpx.MockTransport(handler), **kwargs,
    )


def test_catalog_ids_are_strings_and_source_names_preserved():
    assert STATE_CODES["Tripura"] == "16"
    assert STATE_CODES["Manipur"] == "14"
    assert COMPONENT_CODES["Packed Red Blood Cells"] == "12"
    assert COMPONENT_CODES["Whole Blood"] == "11"
    assert all(isinstance(v, str) for v in STATE_CODES.values())
    assert all(isinstance(v, str) for v in COMPONENT_CODES.values())


def test_parse_group_spacing_and_bombay_group_not_mapped_to_o():
    assert _map_group("O +Ve") == ("O+", "ok")
    assert _map_group("O-Ve") == ("O-", "ok")
    assert _map_group("AB+VE") == ("AB+", "ok")
    assert _map_group("Oh-VE") == (None, "unsupported")
    assert _parse_qty_entries("O+Ve : 38, A-Ve : 2") == [
        ("O+Ve", "O+Ve", 38), ("A-Ve", "A-Ve", 2)]


def test_fixture_maps_quantities_contacts_and_source_time():
    a = adapter(lambda request: httpx.Response(200, json=FIXTURE))
    result = a._parse(FIXTURE, "16", "12", "Packed Red Blood Cells")
    assert len(result.banks) == 1
    bank = result.banks[0]
    assert bank.bank_id == "eraktkosh-25002"
    assert bank.name.strip() == "Indira Gandhi Memorial (Igm) Hospital"
    assert bank.phone == "7005448827"  # fax is not mistaken for phone
    assert bank.address.startswith("Akhaura Road")
    by_group = {r.blood_group: r for r in result.records if r.blood_group}
    assert by_group["O+"].quantity == 38
    assert by_group["AB+"].quantity == 11
    assert by_group["O-"].quantity == 0
    assert by_group["O-"].reported_status == "unavailable"
    assert by_group["O+"].source_updated_at == datetime(2026, 10, 21, 7, 1, 6, tzinfo=timezone.utc)
    assert by_group["O+"].retrieved_at == NOW
    assert all(r.component == "prbc" for r in result.records if r.blood_group)
    assert all(r.source_url == OFFICIAL_URL for r in result.records)


def test_empty_source_timezone_still_parses_timestamp_as_india_time():
    # An empty ERAKTKOSH_SOURCE_TIMEZONE environment value must not silently disable parsing.
    a = adapter(lambda request: httpx.Response(200, json=FIXTURE), source_timezone="")
    result = a._parse(FIXTURE, "16", "12", "Packed Red Blood Cells")
    record = next(r for r in result.records if r.blood_group == "AB+")
    assert record.source_updated_at == datetime(2026, 10, 21, 7, 1, 6, tzinfo=timezone.utc)
    assert record.source_updated_at_raw == "2026-10-21 12:31:06"


def test_bulk_request_uses_only_state_and_component_ids_and_no_credentials():
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=FIXTURE)
    a = adapter(handler)
    result = asyncio.run(a.fetch_stock([], "O-", "prbc", "Agartala", state="Tripura", quantity=2))
    assert len(seen) == 1
    request = seen[0]
    assert request.url == httpx.URL(OFFICIAL_URL).copy_merge_params({"stateCode": "16", "componentId": "12"})
    assert request.headers["accept"] == "application/json"
    assert request.headers["user-agent"].startswith("BloodSignal/")
    assert "cookie" not in request.headers
    assert "authorization" not in request.headers
    assert "api_key" not in request.url.params
    assert all(r.blood_group == "O-" for r in result.records)


def test_facility_geocoding_fallback_attaches_verified_coordinates_to_stock_result():
    class FixedGeocoder:
        enabled = True

        async def geocode(self, **kwargs):
            assert kwargs["name"] == "Indira Gandhi Memorial (Igm) Hospital"
            assert kwargs["district"] == "Agartala"
            assert kwargs["state"] == "Tripura"
            return 23.8466, 91.2868, "FACILITY"

    result = asyncio.run(adapter(
        lambda request: httpx.Response(200, json=FIXTURE),
        geocoder=FixedGeocoder(),
    ).fetch_stock([], "O-", "prbc", "Agartala", state="Tripura"))

    bank = result.banks[0]
    assert (bank.latitude, bank.longitude) == (23.8466, 91.2868)
    assert bank.location_precision == "FACILITY"
    assert "geocoded from the facility address" in (bank.location_note or "")


def test_empty_array_is_valid_empty_result():
    result = asyncio.run(adapter(lambda request: httpx.Response(200, json=[])).fetch_stock(
        [], "O-", "prbc", "Agartala", state="Tripura"))
    assert result.records == []
    assert result.banks == []


@pytest.mark.parametrize("response,exception", [
    (httpx.Response(401), AccessNotPermitted),
    (httpx.Response(403), AccessNotPermitted),
    (httpx.Response(302, headers={"Location": "https://example.com/login"}), AccessNotPermitted),
    (httpx.Response(429, headers={"Retry-After": "30"}), SourceRateLimited),
    (httpx.Response(200, text="<html>please enable JavaScript</html>", headers={"content-type": "text/html"}), SourceMalformed),
    (httpx.Response(200, json={"data": []}), SourceMalformed),
    (httpx.Response(400), SourceRequestRejected),
])
def test_provider_errors_are_not_treated_as_zero_stock(response, exception):
    with pytest.raises(exception):
        asyncio.run(adapter(lambda request: response).fetch_stock(
            [], "O-", "prbc", "Agartala", state="Tripura"))


def test_missing_hospital_code_is_not_given_a_fabricated_identity():
    row = dict(FIXTURE[0])
    row.pop("hospitalCode")
    result = adapter(lambda request: httpx.Response(200, json=[row]))._parse(
        [row], "16", "12", "Packed Red Blood Cells")
    assert result.banks == []
    assert result.records == []
    assert result.invalid_rows == 1


def test_invalid_state_or_component_fails_before_network_request():
    calls = []
    a = adapter(lambda request: calls.append(request) or httpx.Response(200, json=[]))
    with pytest.raises(SourceRequestRejected):
        asyncio.run(a.fetch_stock([], "O-", "prbc", "Agartala", state="Unknown State"))
    assert calls == []


def test_conflicting_duplicate_group_entries_are_withheld():
    row = dict(FIXTURE[0])
    row["components"] = {"Packed Red Blood Cells": {
        "available_WithQty": "O-Ve : 3",
        "not_available_WithQty": "O-Ve : 0",
    }}
    result = adapter(lambda request: httpx.Response(200, json=[row]))._parse(
        [row], "16", "12", "Packed Red Blood Cells")
    o_negative = next(r for r in result.records if r.blood_group == "O-")
    assert o_negative.quantity is None
    assert o_negative.reported_status is None
    assert any("Conflicting duplicate" in note for note in o_negative.limitations)
    assert result.invalid_rows == 1


def test_same_state_component_requests_are_deduplicated_in_flight():
    seen = []
    # Use a delayed transport handler to ensure both calls overlap.
    class AsyncTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            seen.append(request)
            await asyncio.sleep(0.01)
            return httpx.Response(200, json=FIXTURE)
    a = ERaktKoshAdapter(transport=AsyncTransport())
    async def run_both():
        await asyncio.gather(
            a.fetch_stock([], "O-", "prbc", "Agartala", state="Tripura"),
            a.fetch_stock([], "O-", "prbc", "Agartala", state="Tripura"),
        )
    asyncio.run(run_both())
    assert len(seen) == 1


def test_parser_accepts_entrydate_next_to_component_stock():
    payload = [{
        "hospitalname": "Test Hospital", "hospitaladd": "Agartala, Tripura",
        "hospitalCode": "99999", "components": {
            "Packed Red Blood Cells": {
                "entrydate": "2026-10-22 10:35:16",
                "available_WithQty": "O+Ve : 5",
            }
        },
    }]
    result = adapter(lambda request: httpx.Response(200, json=payload))._parse(
        payload, "16", "12", "Packed Red Blood Cells"
    )
    assert result.records[0].source_updated_at is not None
    assert result.records[0].source_updated_at_raw == "2026-10-22 10:35:16"
    assert "Source update timestamp missing/unparseable; freshness unknown" not in result.records[0].limitations


def test_india_timestamp_parses_when_zoneinfo_database_is_missing(monkeypatch):
    import app.services.eraktkosh as module
    def no_tz_database(_name):
        raise module.ZoneInfoNotFoundError("tz database unavailable")
    monkeypatch.setattr(module, "ZoneInfo", no_tz_database)
    parsed = module._source_time("2026-10-21 12:31:06", "Asia/Kolkata")
    assert parsed == datetime(2026, 10, 21, 7, 1, 6, tzinfo=timezone.utc)


def test_unknown_timezone_does_not_fall_back_to_india(monkeypatch):
    import app.services.eraktkosh as module
    def no_tz_database(_name):
        raise module.ZoneInfoNotFoundError("unknown zone")
    monkeypatch.setattr(module, "ZoneInfo", no_tz_database)
    assert module._source_time("2026-10-21 12:31:06", "Mars/Olympus") is None
