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


def test_area_hints_read_city_and_district_from_address_tail():
    from app.services.geocoding import area_hints
    hints = area_hints("AGMC & GBP Hospital, GB Bazar, P.O Kunjavan, Agartala, West Tripura, Tripura", "Tripura")
    assert hints == {"city": "Agartala", "district": "West Tripura"}
    # Not trusted when the address does not end in the requested state.
    assert area_hints("Some Road, Imphal, Manipur", "Tripura") == {"city": None, "district": None}


def test_resolved_facility_is_cached_between_searches():
    calls = []

    def handler(request):
        calls.append(request.url.params["q"])
        return httpx.Response(200, json=[place_result()])

    geocoder = make_geocoder(handler)
    kwargs = dict(name="Unknown Blood Centre", address="Main Road, Agartala, West Tripura, Tripura",
                  city=None, district=None, state="Tripura")
    first = asyncio.run(geocoder.geocode(**kwargs))
    count = len(calls)
    second = asyncio.run(geocoder.geocode(**kwargs))
    assert first is not None and first == second
    assert first[2] == "LOCALITY"
    assert len(calls) == count  # second search made no network requests



def test_matches_indian_hospital_abbreviations_like_agmc_igm():
    from app.services.geocoding import NominatimGeocoder
    geocoder = NominatimGeocoder(enabled=True)
    # AGMC listed in OSM; matches request that includes AGMC
    assert geocoder._matches_facility_name(
        {"name": "AGMC and GBP Hospital", "display_name": "AGMC and GBP Hospital, Agartala"},
        "Dept of Transfusion Medicine AGMC and GBP Hospital"
    )
    # Indira Gandhi Memorial in OSM result; matches request with same words
    assert geocoder._matches_facility_name(
        {"name": "Indira Gandhi Memorial Hospital", "display_name": "Indira Gandhi Memorial Hospital"},
        "Indira Gandhi Memorial (IGM) Hospital"
    )
    # Short 4-letter distinctive word like "Jnims" matches single long token
    assert geocoder._matches_facility_name(
        {"name": "JNIMS Hospital", "display_name": "JNIMS Hospital, Imphal"},
        "JNIMS"
    )


def test_area_hints_handles_missing_or_short_addresses():
    from app.services.geocoding import area_hints
    # Too short, no hints
    assert area_hints("Main Road, Tripura", "Tripura") == {"city": None, "district": None}
    # State mismatch
    assert area_hints("Road, Imphal, Manipur, Tripura", "Manipur") == {"city": None, "district": None}
