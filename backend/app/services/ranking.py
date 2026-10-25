"""Evidence-first candidate distance and ranking rules. No network or LLM calls."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Iterable

from app.schemas import Candidate, EvidenceState, MatchState, Section

EARTH_RADIUS_KM = 6371.0088
RANKING_VERSION = "emergency-sufficient-freshness-distance-3.0.0"


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
    """
    FIXED: Add distance metadata with proper coordinate validation.
    Distinguishes between missing user coordinates vs missing facility coordinates.
    """
    import logging
    log = logging.getLogger("geocoding")
    
    candidate_list = list(candidates)

    for candidate in candidate_list:
        bank = candidate.bank
        
        # Validate origin coordinates
        origin_valid = origin is not None and valid_coordinate_pair(*origin)
        
        # Prefer a distance already supplied by the source (nearest-bloodbank API),
        # available even when facility coordinates are absent.
        source_distance = getattr(bank, "distance_km", None)
        if isinstance(source_distance, (int, float)) and not isinstance(source_distance, bool) and math.isfinite(source_distance) and source_distance >= 0:
            candidate.distance_km = round(float(source_distance), 3)
            candidate.distance_type = "straight_line"
            candidate.distance_is_approximate = True
            candidate.distance_unavailable_reason = None
            continue

        # Validate facility coordinates  
        destination_valid = valid_coordinate_pair(bank.latitude, bank.longitude)
        destination = (bank.latitude, bank.longitude) if destination_valid else None
        
        # Calculate distance with validated coordinates
        distance = haversine_distance_km(origin, destination)
        unavailable_reason = None

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
            
            # Determine specific reason
            if not origin_valid:
                # User location problem
                if origin is None:
                    reason = (
                        "Exact distance is unavailable because device coordinates were not shared."
                        if location_mode == "exact"
                        else "Exact distance is unavailable because the search uses a district or manual location."
                    )
                else:
                    reason = (
                        f"Exact distance is unavailable: invalid user coordinates "
                        f"(lat={origin[0]}, lon={origin[1]})."
                    )
            elif not destination_valid:
                # Facility location problem
                if bank.location_precision is None:
                    reason = (
                        "Exact distance is unavailable: this facility has no published "
                        "coordinates in the source data."
                    )
                    log.debug(
                        "No published coordinates for %s (bank_id=%s); distance left unavailable.",
                        bank.name, bank.bank_id,
                    )
                else:
                    reason = (
                        f"Exact distance is unavailable: this facility has no valid coordinates "
                        f"(lat={bank.latitude}, lon={bank.longitude}). "
                        f"Facility may need re-geocoding."
                    )
                    log.warning(
                        f"Invalid facility coordinates for {bank.name}: "
                        f"lat={bank.latitude}, lon={bank.longitude}"
                    )
            else:
                # Should not happen
                reason = "Exact distance is unavailable for unknown reasons."
                log.error(
                    f"Unexpected distance calculation failure for {bank.name}: "
                    f"origin={origin}, destination={destination}"
                )
            
            candidate.distance_unavailable_reason = reason



def _is_fresh(candidate: Candidate, now: datetime, freshness_hours: int) -> bool:
    updated = candidate.source_updated_at
    state = getattr(candidate.evidence_state, "value", candidate.evidence_state)
    if updated is None or state in (
        EvidenceState.UNKNOWN_FRESHNESS.value,
        EvidenceState.CONFLICTING_REPORTS.value,
        EvidenceState.SOURCE_UNAVAILABLE.value,
    ):
        return False
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    age_hours = (now - updated).total_seconds() / 3600
    return 0 <= age_hours <= freshness_hours


def _is_sufficient(candidate: Candidate, requested_quantity: int) -> bool:
    return (
        candidate.match_state == MatchState.MATCHED_REPORTED
        and candidate.quantity_reported is not None
        and candidate.quantity_reported >= requested_quantity
        and requested_quantity > 0
        and candidate.section == Section.A_ELIGIBLE
    )


def _tier(candidate: Candidate, now: datetime, freshness_hours: int, requested_quantity: int) -> tuple[int, str]:
    """Emergency ordering: sufficient+fresh, sufficient+stale, then partial evidence."""
    demo = candidate.source_type == "demo"
    if candidate.section == Section.D_MISMATCH:
        return (9 if demo else 8), "The source record does not match the requested blood group or component."
    sufficient = _is_sufficient(candidate, requested_quantity)
    fresh = _is_fresh(candidate, now, freshness_hours)
    if sufficient and fresh:
        return (7 if demo else 1), "Reported quantity is sufficient and the source report is fresh."
    if sufficient:
        return (8 if demo else 2), "Reported quantity is sufficient, but the source report is stale or freshness is unknown. Reconfirm by phone."
    if candidate.match_state == MatchState.INSUFFICIENT and candidate.quantity_reported is not None and candidate.quantity_reported > 0:
        if fresh:
            return (6 if demo else 5), "Fresh source data reports positive stock below the requested quantity."
        return (5 if demo else 3), "Positive stock is insufficient and the source report is stale or freshness is unknown."
    return (6 if demo else 4), "Stock is unknown, unavailable, conflicting, or not positively reported; availability is unconfirmed."


def _freshness_score(candidate: Candidate, now: datetime, freshness_hours: int) -> float:
    updated = candidate.source_updated_at
    if updated is None:
        return 0.0
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    age_hours = (now - updated).total_seconds() / 3600
    if age_hours < 0:
        return 0.0
    return round(1.0 / (1.0 + age_hours / max(freshness_hours, 1)), 6)


def _quantity_score(candidate: Candidate, requested_quantity: int) -> float:
    quantity = candidate.quantity_reported
    if quantity is None or quantity < 0 or requested_quantity < 1:
        return 0.0
    return min(quantity / requested_quantity, 1.0)


def _is_positive_exact_match(candidate: Candidate) -> bool:
    quantity = candidate.quantity_reported
    return (
        candidate.match_state in (MatchState.MATCHED_REPORTED, MatchState.INSUFFICIENT)
        and quantity is not None
        and quantity > 0
    )


def _freshness_order(candidate: Candidate, now: datetime, freshness_hours: int) -> tuple[int, float]:
    """Fresh reports first, newest source timestamp first, then stale and untimed evidence."""
    state = getattr(candidate.evidence_state, "value", candidate.evidence_state)
    updated = candidate.source_updated_at
    if updated is None or state in (
        EvidenceState.UNKNOWN_FRESHNESS.value,
        EvidenceState.CONFLICTING_REPORTS.value,
        EvidenceState.SOURCE_UNAVAILABLE.value,
    ):
        return 2, math.inf
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    age_hours = (now - updated).total_seconds() / 3600
    if age_hours < 0:
        return 2, math.inf
    freshness_bucket = 0 if age_hours <= freshness_hours else 1
    return freshness_bucket, -updated.timestamp()


def _availability_order(candidate: Candidate, requested_quantity: int) -> tuple[int, float]:
    """Positive exact-scope stock: surplus, exact quantity, partial quantity, then unknown."""
    quantity = candidate.quantity_reported
    if not _is_positive_exact_match(candidate) or quantity is None:
        return 3, 0.0
    if quantity > requested_quantity:
        return 0, -quantity
    if quantity == requested_quantity:
        return 1, -quantity
    return 2, -quantity


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
    """Rank exact positive live stock by freshness, quantity class, then proximity.

    Source/evidence quality remains fail-closed: exact-scope positive live reports precede
    unconfirmed live records, and synthetic demo records remain last. Within those groups,
    freshness is ordered before availability (surplus, exact, partial), then validated
    straight-line distance. The normalized score is supporting metadata, not the sort key.
    """
    total_weight = freshness_weight + quantity_weight + distance_weight
    if total_weight <= 0:
        raise ValueError("At least one ranking weight must be greater than zero")
    weights = (freshness_weight / total_weight, quantity_weight / total_weight,
               distance_weight / total_weight)
    prepared = []
    for candidate in candidates:
        tier, tier_reason = _tier(candidate, now, freshness_hours, requested_quantity)
        fresh = _freshness_score(candidate, now, freshness_hours)
        quantity = _quantity_score(candidate, requested_quantity)
        freshness_bucket, timestamp_key = _freshness_order(candidate, now, freshness_hours)
        availability_bucket, quantity_key = _availability_order(candidate, requested_quantity)
        distance = (
            1.0 / (1.0 + candidate.distance_km / distance_scale_km)
            if candidate.distance_km is not None and distance_scale_km > 0
            else 0.0
        )
        score = weights[0] * fresh + weights[1] * quantity + weights[2] * distance
        candidate.ranking_tier = tier
        candidate.ranking_score = round(score, 6)
        candidate.ranking_version = RANKING_VERSION
        distance_reason = (
            f"straight-line distance {candidate.distance_km:.3f} km was included"
            if candidate.distance_km is not None
            else "distance was unavailable and did not affect ordering"
        )
        candidate.ranking_explanation = (
            f"Priority tier {tier}. {tier_reason} Within the same priority tier, shortest validated "
            f"straight-line distance ranks first; {distance_reason}."
        )
        sufficient = _is_sufficient(candidate, requested_quantity)
        fresh_flag = _is_fresh(candidate, now, freshness_hours)
        demo_flag = candidate.source_type == "demo"
        # Priority classes follow emergency policy; distance is the primary tie-breaker.
        if sufficient and fresh_flag:
            priority = 7 if demo_flag else 0
        elif sufficient:
            priority = 8 if demo_flag else 1
        elif candidate.match_state == MatchState.INSUFFICIENT and candidate.quantity_reported is not None and candidate.quantity_reported > 0 and not fresh_flag:
            priority = 9 if demo_flag else 2
        elif candidate.match_state == MatchState.INSUFFICIENT and candidate.quantity_reported is not None and candidate.quantity_reported > 0 and fresh_flag:
            priority = 10 if demo_flag else 3
        elif candidate.section == Section.D_MISMATCH:
            priority = 12 if demo_flag else 5
        else:
            priority = 11 if demo_flag else 4
        prepared.append((
            (priority,
             candidate.distance_km if candidate.distance_km is not None else math.inf,
             freshness_bucket, timestamp_key, availability_bucket, quantity_key,
             candidate.bank.name.casefold(), candidate.bank.bank_id),
            candidate,
        ))

    for rank, (_, candidate) in enumerate(sorted(prepared, key=lambda row: row[0]), start=1):
        candidate.rank = rank
