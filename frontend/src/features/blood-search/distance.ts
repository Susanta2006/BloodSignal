/** Coordinate validation and great-circle distance helpers. These are not road distances. */
export type LatLon = { latitude: number; longitude: number };
type RecordValue = Record<string, unknown>;

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

function asRecord(value: unknown): RecordValue | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as RecordValue
    : null;
}

function numericCoordinate(value: unknown): number | null {
  if (typeof value !== "number" && typeof value !== "string") return null;
  if (typeof value === "string" && !value.trim()) return null;
  const number = typeof value === "number" ? value : Number(value);
  return Number.isFinite(number) ? number : null;
}

/** Handles the flattened frontend candidate as well as the backend's nested `bank` object. */
export function getFacilityLocationPrecision(candidate: RecordValue): "FACILITY" | "LOCALITY" | null {
  const bank = asRecord(candidate["bank"]);
  const raw = candidate["location_precision"] ?? bank?.["location_precision"];
  return raw === "FACILITY" || raw === "LOCALITY" ? raw : null;
}

export function getFacilityLocationNote(candidate: RecordValue): string | null {
  const bank = asRecord(candidate["bank"]);
  const note = candidate["location_note"] ?? bank?.["location_note"];
  return typeof note === "string" && note.trim() ? note.trim() : null;
}

export function getFacilityCoordinates(candidate: RecordValue): LatLon | null {
  const location = asRecord(candidate["location"]);
  const bank = asRecord(candidate["bank"]);
  const coordinates = asRecord(candidate["coordinates"]);

  const latitude = numericCoordinate(
    candidate["latitude"] ?? bank?.["latitude"] ?? candidate["lat"] ?? candidate["location_latitude"] ??
    location?.["latitude"] ?? location?.["lat"] ?? coordinates?.["latitude"] ?? coordinates?.["lat"],
  );
  const longitude = numericCoordinate(
    candidate["longitude"] ?? bank?.["longitude"] ?? candidate["lng"] ?? candidate["lon"] ??
    candidate["location_longitude"] ?? location?.["longitude"] ?? location?.["lng"] ?? location?.["lon"] ??
    coordinates?.["longitude"] ?? coordinates?.["lng"] ?? coordinates?.["lon"],
  );

  const result = latitude === null || longitude === null ? null : { latitude, longitude };
  return validLatLon(result) ? result : null;
}

/**
 * A locality/town/district centroid is not a facility destination. Ignore both a backend
 * distance computed from that centroid and any client-side fallback calculation, so every
 * facility in the same district is not misleadingly shown at the same distance.
 */
export function candidateDistanceKm(
  candidate: RecordValue,
  origin: LatLon | null,
): number | null {
  if (getFacilityLocationPrecision(candidate) === "LOCALITY") return null;

  const reported = candidate["distance_km"];
  if (typeof reported === "number" && Number.isFinite(reported) && reported >= 0) return reported;
  if (!origin) return null;
  return haversineDistanceKm(origin, getFacilityCoordinates(candidate));
}
