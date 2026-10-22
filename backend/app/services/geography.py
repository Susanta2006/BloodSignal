"""Nearby-district expansion policy. SUGGESTS areas; never claims stock exists there.

STARTER DATA: verify adjacency with a domain owner before relying on it.
"""
from __future__ import annotations

GEO_POLICY_VERSION = "geo-policy-0.1-unverified"

_NEARBY = {
    "imphal west": ["Imphal East", "Thoubal", "Bishnupur"],
    "imphal east": ["Imphal West", "Thoubal", "Bishnupur"],
    "thoubal": ["Imphal East", "Imphal West", "Bishnupur"],
    "bishnupur": ["Imphal West", "Imphal East", "Thoubal"],
    "agartala": ["Sepahijala", "Gomati", "Khowai"],
    "west tripura": ["Sepahijala", "Khowai", "Gomati"],
    "sepahijala": ["West Tripura", "Gomati", "Khowai"],
    "gomati": ["West Tripura", "Sepahijala", "South Tripura"],
    "khowai": ["West Tripura", "Dhalai", "Sepahijala"],
    "guwahati": ["Kamrup", "Kamrup Metropolitan", "Nalbari"],
    "kolkata": ["Howrah", "North 24 Parganas", "South 24 Parganas"],
    "pune": ["Satara", "Ahmednagar", "Solapur"],
    "mumbai": ["Thane", "Palghar", "Raigad"],
    "delhi": ["Gurugram", "Faridabad", "Ghaziabad"],
    "new delhi": ["Gurugram", "Faridabad", "Ghaziabad"],
    "bengaluru": ["Ramanagara", "Tumakuru", "Kolar"],
    "hyderabad": ["Medchal-Malkajgiri", "Rangareddy", "Sangareddy"],
    "chennai": ["Tiruvallur", "Kanchipuram", "Chengalpattu"],
}


def nearby_districts(district: str | None) -> list[str]:
    return list(_NEARBY.get((district or "").strip().lower(), []))
