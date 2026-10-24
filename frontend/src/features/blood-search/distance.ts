/** Coordinate validation and great-circle distance helpers. These are not road distances. */
export type LatLon = { latitude: number; longitude: number };
export type FacilityLocationPrecision = "FACILITY" | "LOCALITY" | null | undefined;

export function validLatLon(point: LatLon | null | undefined): point is LatLon {
  return Boolean(
    point &&
      typeof point.latitude === "number" &&
      Number.isFinite(point.latitude) &&
      point.latitude >= -90 &&
      point.latitude <= 90 &&
      typeof point.longitude === "number" &&
      Number.isFinite(point.longitude) &&
      point.longitude >= -180 &&
      point.longitude <= 180,
  );
}

/** Haversine distance in kilometres using the mean Earth radius (6371.0088 km). */
export function haversineDistanceKm(
  origin: LatLon | null | undefined,
  destination: LatLon | null | undefined,
): number | null {
  if (!validLatLon(origin) || !validLatLon(destination)) return null;
  const radians = (degrees: number) => degrees * Math.PI / 180;
  const lat1 = radians(origin.latitude);
  const lat2 = radians(destination.latitude);
  const dLat = lat2 - lat1;
  // Normalize across the antimeridian to keep the shortest angular difference.
  const longitudeDifference =
    ((destination.longitude - origin.longitude + 540) % 360) - 180;
  const dLon = radians(longitudeDifference);
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
  const boundedA = Math.min(1, Math.max(0, a));
  return 2 * 6371.0088 * Math.asin(Math.sqrt(boundedA));
}

/**
 * Do not calculate facility distance from a town/district-centre geocode.
 * Its coordinate is useful for a clearly labelled map pin, not for ranking.
 */
export function facilityDistanceKm(
  origin: LatLon | null | undefined,
  destination: LatLon | null | undefined,
  precision?: FacilityLocationPrecision,
): number | null {
  if (precision === "LOCALITY") return null;
  return haversineDistanceKm(origin, destination);
}
