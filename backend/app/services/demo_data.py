"""Deterministic demo inventory paired with real, publicly listed facility directory details.

Facility identities, addresses and phones are public listings; every stock record remains
synthetic and must be displayed as demo inventory, never as live availability.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta

from app.schemas import BloodBank, NormalizedStockRecord

DEMO_SOURCE_ID = "demo-fixture-v1"
DEMO_SOURCE_NAME = "Synthetic demo fixtures"
PARSER_VERSION = "demo-parser-1"

# All 28 states and 8 union territories. These names are search anchors only,
# not claims that a real blood bank is located at these places.
DEMO_LOCATIONS: dict[str, tuple[str, ...]] = {
    "Andhra Pradesh": ("Amaravati", "Visakhapatnam", "Vijayawada", "Tirupati"),
    "Arunachal Pradesh": ("Itanagar", "Naharlagun", "Pasighat"),
    "Assam": ("Guwahati", "Dispur", "Dibrugarh", "Silchar"),
    "Bihar": ("Patna", "Gaya", "Muzaffarpur", "Bhagalpur"),
    "Chhattisgarh": ("Raipur", "Bilaspur", "Durg", "Jagdalpur"),
    "Goa": ("Panaji", "Margao", "Vasco da Gama"),
    "Gujarat": ("Gandhinagar", "Ahmedabad", "Surat", "Vadodara", "Rajkot"),
    "Haryana": ("Chandigarh", "Gurugram", "Faridabad", "Hisar"),
    "Himachal Pradesh": ("Shimla", "Dharamshala", "Mandi", "Solan"),
    "Jharkhand": ("Ranchi", "Jamshedpur", "Dhanbad", "Bokaro"),
    "Karnataka": ("Bengaluru", "Mysuru", "Mangaluru", "Hubballi", "Belagavi"),
    "Kerala": ("Thiruvananthapuram", "Kochi", "Kozhikode", "Thrissur", "Kollam"),
    "Madhya Pradesh": ("Bhopal", "Indore", "Jabalpur", "Gwalior", "Ujjain"),
    "Maharashtra": ("Mumbai", "Pune", "Nagpur", "Nashik", "Chhatrapati Sambhajinagar"),
    "Manipur": ("Imphal", "Imphal West", "Thoubal", "Churachandpur"),
    "Meghalaya": ("Shillong", "Tura", "Jowai"),
    "Mizoram": ("Aizawl", "Lunglei", "Champhai"),
    "Nagaland": ("Kohima", "Dimapur", "Mokokchung"),
    "Odisha": ("Bhubaneswar", "Cuttack", "Rourkela", "Sambalpur"),
    "Punjab": ("Chandigarh", "Amritsar", "Ludhiana", "Jalandhar", "Patiala"),
    "Rajasthan": ("Jaipur", "Jodhpur", "Udaipur", "Kota", "Ajmer"),
    "Sikkim": ("Gangtok", "Namchi", "Gyalshing"),
    "Tamil Nadu": ("Chennai", "Coimbatore", "Madurai", "Tiruchirappalli", "Salem"),
    "Telangana": ("Hyderabad", "Warangal", "Nizamabad", "Khammam"),
    "Tripura": ("Agartala", "Udaipur", "Dharmanagar", "Kailashahar"),
    "Uttar Pradesh": ("Lucknow", "Kanpur", "Varanasi", "Agra", "Prayagraj", "Gorakhpur"),
    "Uttarakhand": ("Dehradun", "Haridwar", "Haldwani", "Rishikesh"),
    "West Bengal": ("Kolkata", "Siliguri", "Durgapur", "Asansol", "Bardhaman"),
    "Andaman and Nicobar Islands": ("Sri Vijaya Puram", "Port Blair"),
    "Chandigarh": ("Chandigarh",),
    "Dadra and Nagar Haveli and Daman and Diu": ("Daman", "Silvassa", "Diu"),
    "Delhi": ("New Delhi", "Delhi"),
    "Jammu and Kashmir": ("Srinagar", "Jammu", "Anantnag"),
    "Ladakh": ("Leh", "Kargil"),
    "Lakshadweep": ("Kavaratti",),
    "Puducherry": ("Puducherry", "Karaikal"),
}


def _normal(value: str | None) -> str:
    return " ".join((value or "").strip().casefold().split())


def _bank(slug: str, name: str, district: str, state: str | None = None, *,
          address: str | None = None, phone: str | None = None,
          latitude: float | None = None, longitude: float | None = None,
          directory_source: str | None = None,
          directory_reference: str | None = None) -> BloodBank:
    location = ", ".join(part for part in (district, state) if part)
    is_real_directory = bool(address and phone and latitude is not None and longitude is not None)
    return BloodBank(
        bank_id=f"demo-bank-{slug}",
        provider_place_id=f"public-facility-{slug}" if is_real_directory else f"demo-place-{slug}",
        name=name if is_real_directory else f"[DEMO] {name}",
        address=address or f"Synthetic demo location — {location}",
        phone=phone if is_real_directory else None,
        latitude=latitude,
        longitude=longitude,
        city="Imphal" if is_real_directory else None,
        district=district,
        state=state,
        source_type="live" if is_real_directory else "demo",
        directory_source=directory_source or "Synthetic demo directory",
        directory_reference=directory_reference or f"https://demo.invalid/facilities/{slug}",
    )


def _location_slug(state: str, district: str) -> str:
    fingerprint = hashlib.sha256(f"{state}|{district}".casefold().encode()).hexdigest()[:12]
    return f"location-{fingerprint}"


# Approximate city-centre anchors for deterministic demo maps. These are not
# hospital geocodes; sample facilities are synthetic points near the named city.
_CITY_COORDS: dict[str, tuple[float, float]] = {
    "imphal": (24.8170, 93.9368), "imphal west": (24.8170, 93.9063), "imphal east": (24.7556, 94.0036),
    "thoubal": (24.6388, 94.0106), "agartala": (23.8315, 91.2868), "udaipur": (23.5333, 91.4833),
    "dharmanagar": (24.3667, 92.1667), "guwahati": (26.1445, 91.7362), "dispur": (26.1433, 91.7898),
    "shillong": (25.5788, 91.8933), "aizawl": (23.7271, 92.7176), "kohima": (25.6751, 94.1086),
    "dimapur": (25.9063, 93.7278), "kolkata": (22.5726, 88.3639), "siliguri": (26.7271, 88.3953),
    "patna": (25.5941, 85.1376), "lucknow": (26.8467, 80.9462), "delhi": (28.6139, 77.2090),
    "new delhi": (28.6139, 77.2090), "mumbai": (19.0760, 72.8777), "pune": (18.5204, 73.8567),
    "nagpur": (21.1458, 79.0882), "ahmedabad": (23.0225, 72.5714), "surat": (21.1702, 72.8311),
    "jaipur": (26.9124, 75.7873), "bhopal": (23.2599, 77.4126), "indore": (22.7196, 75.8577),
    "raipur": (21.2514, 81.6296), "ranchi": (23.3441, 85.3096), "bhubaneswar": (20.2961, 85.8245),
    "chennai": (13.0827, 80.2707), "bengaluru": (12.9716, 77.5946), "hyderabad": (17.3850, 78.4867),
    "kochi": (9.9312, 76.2673), "thiruvananthapuram": (8.5241, 76.9366), "panaji": (15.4909, 73.8278),
    "visakhapatnam": (17.6868, 83.2185), "vijayawada": (16.5062, 80.6480), "dehradun": (30.3165, 78.0322),
    "chandigarh": (30.7333, 76.7794), "srinagar": (34.0837, 74.7973), "jammu": (32.7266, 74.8570),
    "gangtok": (27.3389, 88.6065), "itanagar": (27.0844, 93.6053), "amaravati": (16.5131, 80.5165),
}


def _sample_banks(district: str, state: str) -> list[BloodBank]:
    slug = _location_slug(state, district)
    anchor = _CITY_COORDS.get(_normal(district))
    banks = []
    for n in range(1, 4):
        # Keep sample points clustered around the city anchor, with stable offsets.
        latitude = anchor[0] + (n - 2) * 0.008 if anchor else None
        longitude = anchor[1] + (n - 2) * 0.009 if anchor else None
        banks.append(_bank(f"{slug}-sample-{n}", f"Sample Blood Bank {n}", district, state,
                           latitude=latitude, longitude=longitude,
                           address=(f"Synthetic demo point near {district}, {state}" if anchor else None),
                           directory_source="Synthetic demo location anchor" if anchor else "Synthetic demo directory"))
    return banks


_SCENARIO_BANKS = {
    "imphal west": [
        # Public facility directory details are real; all paired stock below is synthetic.
        # Coordinates are approximate map pins for demo distance calculations, not a routing guarantee.
        _bank("alpha", "JNIMS Hospital Blood Bank", "Imphal East", "Manipur",
              address="JNIMS Hospital, Ryang Palli Road, near TV Road, Porompat, Imphal East, Manipur 795005",
              phone="0385-2443142", latitude=24.7556, longitude=94.0036,
              directory_source="GetBlood public blood-bank directory; phone not independently verified",
              directory_reference="https://www.getblood.in/BloodBank/JNIMS-Hospital-Blood-Bank-and-Transfusion-Unit-in-Porompat/"),
        _bank("beta", "RIMS Hospital Blood Bank", "Imphal West", "Manipur",
              address="Regional Institute of Medical Sciences, Lamphelpat, Imphal West, Manipur 795004",
              phone="0385-2414629", latitude=24.8170, longitude=93.9063,
              directory_source="BloodLinks public listing; general hospital contact, ask for blood bank",
              directory_reference="https://bloodlinks.in/details_hospital?id=14716"),
        _bank("gamma", "Shija Blood Bank and Transfusion Services", "Imphal West", "Manipur",
              address="Shija Hospitals and Research Institute, HealthVillage, Langol, Imphal West, Manipur 795004",
              phone="9007900794", latitude=24.8500, longitude=93.9050,
              directory_source="Shija official hospital contact; ask for Blood Bank and Transfusion Services",
              directory_reference="https://old.shijahospitals.com/blood-bank-and-transfusion-services"),
        _bank("delta", "Shija Hospitals and Research Institute", "Imphal West", "Manipur",
              address="HealthVillage, Langol, Imphal West, Manipur 795004",
              phone="9611609611", latitude=24.8500, longitude=93.9050,
              directory_source="Shija official hospital contact; general call centre",
              directory_reference="https://shijahospitals.com/contact-us"),
    ],
    "thoubal": [_bank("epsilon", "Epsilon Blood Bank", "Thoubal", "Manipur")],
    "demo timeout": [_bank("zeta", "Zeta Blood Bank", "Demo Timeout", "Manipur")],
}


def demo_banks_for(district: str | None, state: str | None = None) -> list[BloodBank]:
    district_key, state_key = _normal(district), _normal(state)
    if district_key in _SCENARIO_BANKS:
        return [bank.model_copy() for bank in _SCENARIO_BANKS[district_key]]

    states = [(name, places) for name, places in DEMO_LOCATIONS.items()
              if not state_key or _normal(name) == state_key]
    matches = [(name, place) for name, places in states for place in places
               if district_key and _normal(place) == district_key]
    if matches:
        name, place = matches[0]
        return _sample_banks(place, name)
    if district_key:
        # Allow any manually entered city/district to be demonstrated, visibly synthetic.
        state_name = state.strip() if state else "Unspecified region"
        return _sample_banks(district.strip(), state_name)
    if states:
        # Bound state-only results while covering multiple locations in the selected state.
        return [bank for name, places in states for place in places
                for bank in _sample_banks(place, name)][:36]
    # Broad search returns one sample location per state/UT, never real facility claims.
    return [_sample_banks(places[0], name)[0] for name, places in DEMO_LOCATIONS.items()]


def _rec(bank_slug: str, rid: str, group: str, comp: str, qty: float | None, unit: str | None,
         status: str | None, updated: datetime | None, now: datetime) -> NormalizedStockRecord:
    raw = {"bank": bank_slug, "rid": rid, "group": group, "component": comp, "qty": qty,
           "unit": unit, "status": status, "updated": updated.isoformat() if updated else None}
    return NormalizedStockRecord(
        bank_source_id=f"demo-bank-{bank_slug}", source_id=DEMO_SOURCE_ID,
        blood_group=group, component=comp, quantity=qty, quantity_unit=unit,
        reported_status=status, source_updated_at=updated, retrieved_at=now,
        source_record_id=rid, source_url=f"https://demo.invalid/fixtures/{rid}",
        raw_record_hash=hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest(),
        parser_version=PARSER_VERSION, limitations=["Synthetic demo record; not hospital inventory"])


def demo_stock(now: datetime, bank_ids: list[str] | None = None,
               blood_group: str = "O-", component: str = "whole_blood",
               requested_quantity: int | None = None) -> dict[str, list[NormalizedStockRecord]]:
    h = lambda n: now - timedelta(hours=n)  # noqa: E731
    records = {
        "demo-bank-alpha": [
            _rec("alpha", "alpha-1", "O-", "whole_blood", 3, "units", "available", h(4), now),
            _rec("alpha", "alpha-2", "A+", "whole_blood", 4, "units", "available", h(4), now)],
        "demo-bank-beta": [
            _rec("beta", "beta-1", "O-", "whole_blood", 2, "units", "available", h(72), now)],
        "demo-bank-gamma": [
            _rec("gamma", "gamma-1", "O-", "whole_blood", 1, "units", "available", h(2), now)],
        "demo-bank-delta": [
            _rec("delta", "delta-1", "O-", "whole_blood", 5, "units", "available", None, now)],
    }
    for bank_id in bank_ids or []:
        if bank_id in records or not bank_id.startswith("demo-bank-location-"):
            # Demo inventory can be paired with real directory records during a demo run.
            # The facility itself (name, address, phone, coordinates) remains source data;
            # only its inventory evidence is synthetic and clearly labelled.
            if bank_id.startswith(("datagov-", "place-")):
                fingerprint = hashlib.sha256(bank_id.encode("utf-8")).digest()[0] % 4
                requested = max(1, requested_quantity or 2)
                if fingerprint == 0:
                    quantity, status, updated = requested + 1, "available", h(1)
                elif fingerprint == 1 and requested > 1:
                    quantity, status, updated = requested - 1, "available", h(2)
                elif fingerprint == 2:
                    quantity, status, updated = requested + 1, "available", h(72)
                else:
                    quantity, status, updated = 0, "unavailable", h(1)
                slug = hashlib.sha256(bank_id.encode("utf-8")).hexdigest()[:16]
                record = _rec(slug, f"demo-{slug}-{blood_group}-{component}", blood_group,
                              component, quantity, "units", status, updated, now)
                records[bank_id] = [record.model_copy(update={"bank_source_id": bank_id})]
            continue
        if bank_id.endswith("-sample-1"):
            quantity, status, updated = 3, "available", h(3)
        elif bank_id.endswith("-sample-2"):
            quantity, status, updated = 1, "available", h(2)
        else:
            quantity, status, updated = None, "unavailable", h(5)
        slug = bank_id.removeprefix("demo-bank-")
        records[bank_id] = [_rec(
            slug, f"{slug}-sample-stock", blood_group, component, quantity,
            "units" if quantity is not None else None, status, updated, now)]
    return records
