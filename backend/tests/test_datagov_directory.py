from __future__ import annotations

import pytest
from pydantic import SecretStr

from app.config import Settings
from app.services.directory import DataGovDirectory, DirectoryUnavailable


def test_datagov_header_normalization_and_optional_values():
    row = {
        " Blood Bank Name ": "District Blood Centre",
        "STATE": "Manipur",
        "District": "Imphal West",
        "Contact No": "12345",
        "Latitude": "24.8",
        "Longitude": "93.9",
    }
    assert DataGovDirectory._pick(row, "Blood Bank Name") == "District Blood Centre"
    assert DataGovDirectory._pick(row, "state") == "Manipur"
    assert DataGovDirectory._pick(row, "Mobile") is None


def test_datagov_location_distance_is_bounded_and_uses_haversine():
    origin = (24.8, 93.9)
    assert DataGovDirectory._within_radius(origin, 24.8, 93.9, 1)
    assert not DataGovDirectory._within_radius(origin, 25.5, 93.9, 10)


@pytest.mark.asyncio
async def test_datagov_parsing_filters_device_radius_deduplicates_and_keeps_provenance(monkeypatch):
    rows = [
        {"Blood Bank Name": "Near Centre", "State": "Manipur", "District": "Imphal West",
         "Address": "Road 1", "Contact No.": "+91 12345 67890", "PIN Code": "795001",
         "Latitude": "24.8", "Longitude": "93.9"},
        {"Blood Bank Name": "Near Centre", "State": "Manipur", "District": "Imphal West",
         "Address": "Road 1", "Contact No.": "+91 12345 67890", "PIN Code": "795001",
         "Latitude": "24.8", "Longitude": "93.9"},
        {"Blood Bank Name": "Far Centre", "State": "Manipur", "District": "Imphal West",
         "Address": "Road 2", "Latitude": "25.5", "Longitude": "93.9"},
        {"Blood Bank Name": "No Coordinates", "State": "Manipur", "District": "Imphal West"},
        {"Blood Bank Name": "Invalid Coordinates", "State": "Manipur", "District": "Imphal West",
         "Latitude": "120", "Longitude": "93.9"},
    ]

    class Response:
        headers = {"content-type": "application/json"}
        text = ""
        def raise_for_status(self): pass
        def json(self): return {"records": rows, "total": len(rows)}

    class Client:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, *args, **kwargs): return Response()

    monkeypatch.setattr("app.services.directory.httpx.AsyncClient", Client)
    settings = Settings(_env_file=None, datagov_api_key=SecretStr("test-key"), datagov_page_size=10)
    banks = await DataGovDirectory(settings).discover(
        district=None, state=None, origin=(24.8, 93.9), radius_km=10
    )

    assert len(banks) == 1
    assert banks[0].name == "Near Centre"
    assert banks[0].phone == "+91 12345 67890"
    assert banks[0].directory_source == "data.gov.in Blood Bank Directory"
    assert banks[0].directory_reference.endswith(settings.datagov_resource_id)
    assert banks[0].directory_retrieved_at is not None

    district_banks = await DataGovDirectory(settings).discover(
        district="Imphal West", state="Manipur", origin=(24.8, 93.9), radius_km=10
    )
    by_name = {bank.name: bank for bank in district_banks}
    assert set(by_name) == {"Near Centre", "No Coordinates", "Invalid Coordinates"}
    assert by_name["No Coordinates"].latitude is None
    assert by_name["Invalid Coordinates"].latitude is None


@pytest.mark.asyncio
async def test_datagov_missing_key_fails_closed():
    settings = Settings(_env_file=None, datagov_api_key=SecretStr(""))
    with pytest.raises(DirectoryUnavailable, match="API key not configured"):
        await DataGovDirectory(settings).discover(
            district="Imphal West", state="Manipur", origin=None, radius_km=25
        )
