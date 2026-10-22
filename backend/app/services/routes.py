"""Driving distance/time via Routes API. Failure => None => ROUTE_UNKNOWN. Never estimated."""
from __future__ import annotations

import logging
from typing import Protocol

import httpx

from app.schemas import RouteResult, utcnow

log = logging.getLogger("routes")


class RoutesClient(Protocol):
    async def driving(self, origin: tuple[float, float],
                      dest: tuple[float, float]) -> RouteResult | None: ...


class NullRoutes:
    async def driving(self, origin, dest) -> RouteResult | None:
        return None


class GoogleRoutes:
    def __init__(self, api_key: str, timeout_s: float = 6.0):
        self._key, self._timeout = api_key, timeout_s

    async def driving(self, origin, dest) -> RouteResult | None:
        pt = lambda p: {"location": {"latLng": {"latitude": p[0], "longitude": p[1]}}}  # noqa: E731
        body = {"origin": pt(origin), "destination": pt(dest), "travelMode": "DRIVE"}
        headers = {"X-Goog-Api-Key": self._key,
                   "X-Goog-FieldMask": "routes.distanceMeters,routes.duration"}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(self._timeout, connect=3.0)) as c:
                r = await c.post("https://routes.googleapis.com/directions/v2:computeRoutes",
                                 json=body, headers=headers)
            r.raise_for_status()
            route = r.json()["routes"][0]
            return RouteResult(meters=int(route["distanceMeters"]),
                               seconds=int(float(str(route["duration"]).rstrip("s"))),
                               retrieved_at=utcnow())
        except (httpx.HTTPError, KeyError, IndexError, ValueError, TypeError) as e:
            log.warning("route_failed", extra={"error_class": type(e).__name__})
            return None
