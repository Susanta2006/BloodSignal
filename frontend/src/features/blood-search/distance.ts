/** Coordinate validation and great-circle distance helpers. These are not road distances. */
export type LatLon = { latitude: number; longitude: number };

export function validLatLon(point: LatLon | null | undefined): point is LatLon {
  return Boolean(point && Number.isFinite(point.latitude) && Number.isFinite(point.longitude) &&
    point.latitude >= -90 && point.latitude <= 90 &&
    point.longitude >= -180 && point.longitude <= 180);
}

/** Haversine distance in kilometres using the mean Earth radius (6371.0088 km). */
export function haversineDistanceKm(origin: LatLon | null | undefined, destination: LatLon | null | undefined): number | null {
  if (!validLatLon(origin) || !validLatLon(destination)) return null;
  const radians = (degrees: number) => degrees * Math.PI / 180;
  const lat1 = radians(origin.latitude);
  const lat2 = radians(destination.latitude);
  const dLat = lat2 - lat1;
  const dLon = radians(destination.longitude - origin.longitude);
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
  const boundedA = Math.min(1, Math.max(0, a));
  return 2 * 6371.0088 * Math.asin(Math.sqrt(boundedA));
}
