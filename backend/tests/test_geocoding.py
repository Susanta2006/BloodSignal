from __future__ import annotations

import asyncio

import httpx

from app.services.geocoding import NominatimGeocoder


def make_geocoder(handler):
    now = [0.0]

    async def sleep(seconds):
        now[0] += seconds

    return NominatimGeocoder(
        contact="",
        application_url="https://bloodsignal.vercel.app",
        enabled=True,
        clock=lambda: now[0],
        sleep=sleep,
        transport=httpx.MockTransport(handler),
    )


def place_result():
    return {
        "lat": "23.8315",
        "lon": "91.2868",
        "class": "place",
        "type": "city",
        "name": "Agartala",
        "address": {
            "city": "Agartala",
            "county": "West Tripura",
            "state": "Tripura",
            "country": "India",
        },
    }


def hospital_result():
    return {
        "lat": "23.8466",
        "lon": "91.2868",
        "class": "amenity",
        "type": "hospital",
        "name": "AGMC & GBP Hospital",
        "namedetails": {"name:en": "AGMC & GBP Hospital"},
        "address": {
            "city": "Agartala",
            "county": "West Tripura",
            "state": "Tripura",
            "country": "India",
        },
    }


def test_facility_search_continues_after_town_result_and_accepts_matching_hospital():
    requests = []

    def handler(request):
        requests.append(request)
        result = place_result() if len(requests) == 1 else hospital_result()
        return httpx.Response(200, json=[result])

    geocoder = make_geocoder(handler)
    found = asyncio.run(geocoder.geocode(
        name="Dept of Transfusion Medicine and Blood Centre AGMC and GBP Hospital",
        address="GB Bazar, P.O Kunjavan",
        city="Agartala",
        district="Agartala",
        state="Tripura",
        budget_s=8,
    ))

    assert found == (23.8466, 91.2868, "FACILITY")
    assert len(requests) >= 2
    assert requests[0].url.params["countrycodes"] == "in"
    assert requests[0].url.params["addressdetails"] == "1"
    assert "bloodsignal.vercel.app" in requests[0].headers["user-agent"]


def test_facility_result_from_another_district_or_state_is_rejected():
    wrong_area = hospital_result()
    wrong_area["address"] = {
        "city": "Guwahati",
        "county": "Kamrup Metropolitan",
        "state": "Assam",
        "country": "India",
    }

    def handler(_request):
        return httpx.Response(200, json=[wrong_area])

    geocoder = make_geocoder(handler)
    found = asyncio.run(geocoder.geocode(
        name="AGMC & GBP Hospital",
        address=None,
        city="Agartala",
        district="Agartala",
        state="Tripura",
        budget_s=8,
    ))

    assert found is None


def test_locality_is_an_explicit_fallback_only_when_no_hospital_match_exists():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=[place_result()])

    geocoder = make_geocoder(handler)
    found = asyncio.run(geocoder.geocode(
        name="IGM Hospital",
        address="Akhaura Road",
        city="Agartala",
        district="Agartala",
        state="Tripura",
        budget_s=8,
    ))

    assert found == (23.8315, 91.2868, "LOCALITY")
    assert len(requests) > 1


def test_disabled_geocoder_makes_no_requests():
    calls = []
    geocoder = NominatimGeocoder(
        contact="",
        enabled=False,
        transport=httpx.MockTransport(
            lambda request: calls.append(request) or httpx.Response(200, json=[])
        ),
    )

    found = asyncio.run(geocoder.geocode(
        name="Test Hospital",
        address=None,
        city=None,
        district="Test District",
        state="Test State",
    ))

    assert found is None
    assert calls == []
