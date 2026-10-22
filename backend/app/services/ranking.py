"""Evidence-first candidate distance and ranking rules. No network or LLM calls."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Iterable

from app.schemas import Candidate, EvidenceState, MatchState, Section

EARTH_RADIUS_KM = 6371.0088
RANKING_VERSION = "availability-nearest-first-2.0.0"


def valid_coordinate_pair(latitude: object, longitude: object) -> bool:
    return (
        isinstance(latitude, (int, float))
        and not isinstance(latitude, bool)
        and isinstance(longitude, (int, float))
        and not isinstance(longitude, bool)
        and math.isfinite(latitude)
        and math.isfinite(longitude)
        and -90 <= latitude <= 90
        and -180 <= longitude <= 180
    )


def haversine_distance_km(
    origin: tuple[float, float] | None,
    destination: tuple[float, float] | None,
) -> float | None:
    """Return great-circle kilometres for two validated coordinate pairs."""
    if origin is None or destination is None:
        return None
    if not valid_coordinate_pair(*origin) or not valid_coordinate_pair(*destination):
        return None

    lat1, lon1, lat2, lon2 = map(math.radians, (*origin, *destination))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(min(1.0, a)))


def add_distance_metadata(
    candidates: Iterable[Candidate],
    *,
    origin: tuple[float, float] | None,
    location_mode: str,
) -> None:
    for candidate in candidates:
        bank = candidate.bank
        destination = (
            (bank.latitude, bank.longitude)
            if valid_coordinate_pair(bank.latitude, bank.longitude)
            else None
        )
        distance = haversine_distance_km(origin, destination)
        if distance is not None:
            candidate.distance_km = round(distance, 3)
            candidate.distance_type = "straight_line"
            # Haversine is a spherical approximation, even with exact coordinates.
            candidate.distance_is_approximate = True
            candidate.distance_unavailable_reason = None
        else:
            candidate.distance_km = None
            candidate.distance_type = None
            candidate.distance_is_approximate = False
            if origin is None or not valid_coordinate_pair(*origin):
                reason = (
                    "Exact distance is unavailable because the search uses a district or manual location."
                    if location_mode == "manual"
                    else "Exact distance is unavailable because device coordinates were not shared."
                )
            else:
                reason = "Exact distance is unavailable because this facility has no valid coordinates."
            candidate.distance_unavailable_reason = reason


def _tier(candidate: Candidate) -> tuple[int, str]:
    if candidate.source_type == "demo":
        return 4, "Synthetic inventory is demo-only and is not ranked as real stock."
    if candidate.section == Section.A_ELIGIBLE and candidate.match_state == MatchState.MATCHED_REPORTED:
        return 1, "Fresh live inventory evidence confirms the requested component and quantity."
    if (
        candidate.section == Section.C_INSUFFICIENT
        and candidate.match_state == MatchState.INSUFFICIENT
        and candidate.evidence_state == EvidenceState.FRESH
    ):
        return 2, "Fresh live inventory evidence reports partial quantity below the request."
    if candidate.section == Section.D_MISMATCH:
        return 5, "The source record does not match the requested blood group or component."
    return 3, "Inventory is unknown, stale, conflicting, or unavailable; availability is unconfirmed."


def _freshness_score(candidate: Candidate, now: datetime, freshness_hours: int) -> float:
    updated = candidate.source_updated_at
    if updated is None:
        return 0.0
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    age_hours = (now - updated).total_seconds() / 3600
    if age_hours < 0:
        return 0.0
    return 1.0 / (1.0 + age_hours / max(freshness_hours, 1))


def _quantity_score(candidate: Candidate, requested_quantity: int) -> float:
    quantity = candidate.quantity_reported
    if quantity is None or quantity < 0 or requested_quantity < 1:
        return 0.0
    return min(quantity / requested_quantity, 1.0)


def rank_candidates(
    candidates: list[Candidate],
    *,
    now: datetime,
    requested_quantity: int,
    freshness_hours: int,
    distance_scale_km: float,
    freshness_weight: float,
    quantity_weight: float,
    distance_weight: float,
) -> None:
    """Rank by strict evidence tier, then configurable normalized evidence/distance score.

    Within each tier the score is the weighted sum of freshness, requested-quantity
    coverage, and proximity (1 / (1 + km / distance_scale_km)). Missing values score 0.
    Ties use fresher timestamp, quantity, distance, case-folded facility name, then ID.
    """
    total_weight = freshness_weight + quantity_weight + distance_weight
    if total_weight <= 0:
        raise ValueError("At least one ranking weight must be greater than zero")
    weights = (freshness_weight / total_weight, quantity_weight / total_weight,
               distance_weight / total_weight)
    prepared = []
    for candidate in candidates:
        tier, tier_reason = _tier(candidate)
        fresh = _freshness_score(candidate, now, freshness_hours)
        quantity = _quantity_score(candidate, requested_quantity)
        distance = (
            1.0 / (1.0 + candidate.distance_km / distance_scale_km)
            if candidate.distance_km is not None
            else 0.0
        )
        score = weights[0] * fresh + weights[1] * quantity + weights[2] * distance
        candidate.ranking_tier = tier
        candidate.ranking_score = round(score, 6)
        candidate.ranking_version = RANKING_VERSION
        distance_reason = (
            f"straight-line distance {candidate.distance_km:.3f} km was included"
            if candidate.distance_km is not None
            else "distance was unavailable and did not affect the score"
        )
        candidate.ranking_explanation = (
            f"Tier {tier}. {tier_reason} Within this tier, the weighted score uses source freshness, "
            f"reported quantity, and {distance_reason}."
        )
        prepared.append((
            (tier, -score, -fresh, -quantity,
             candidate.distance_km if candidate.distance_km is not None else math.inf,
             candidate.bank.name.casefold(), candidate.bank.bank_id),
            candidate,
        ))

    for rank, (_, candidate) in enumerate(sorted(prepared, key=lambda row: row[0]), start=1):
        candidate.rank = rank
