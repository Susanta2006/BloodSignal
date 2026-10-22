export const travelModes = ["driving", "walking", "transit", "two-wheeler"] as const;

export type TravelMode = (typeof travelModes)[number];

export type FacilityDestination = {
  name?: string | null;
  address?: string | null;
  city?: string | null;
  district?: string | null;
  state?: string | null;
  latitude?: number | null;
  longitude?: number | null;
};

function validCoordinates(
  latitude: number | null | undefined,
  longitude: number | null | undefined,
): latitude is number {
  return typeof latitude === "number" && Number.isFinite(latitude) && latitude >= -90 && latitude <= 90 &&
    typeof longitude === "number" && Number.isFinite(longitude) && longitude >= -180 && longitude <= 180;
}

export function hasReliableFacilityDestination(facility: FacilityDestination): boolean {
  return validCoordinates(facility.latitude, facility.longitude) ||
    (typeof facility.address === "string" && facility.address.trim().length > 0 &&
      !["unknown", "unavailable", "not provided", "n/a"].includes(facility.address.trim().toLowerCase())) ||
    (typeof facility.name === "string" && facility.name.trim().length > 0);
}

export function hasFacilityCoordinates(facility: FacilityDestination): boolean {
  return validCoordinates(facility.latitude, facility.longitude);
}

/**
 * Use Google Maps URLs (no Maps API key required) and prefer validated source coordinates.
 * When coordinates are absent, open a structured name/locality search rather than guessing a pin.
 */
export function buildDirectionsUrl(
  facility: FacilityDestination,
  _travelMode: TravelMode,
  origin?: string,
): string | null {
  if (validCoordinates(facility.latitude, facility.longitude)) {
    const destination = `${facility.latitude},${facility.longitude}`;
    const googleParams = new URLSearchParams({ api: "1", destination, travelmode: _travelMode === "walking" ? "walking" : _travelMode === "transit" ? "transit" : "driving" });
    if (origin?.trim()) googleParams.set("origin", origin.trim());
    return `https://www.google.com/maps/dir/?${googleParams.toString()}`;
  }
  // Avoid the often malformed/overlong source address. A facility name plus structured
  // administrative locality is a more reliable Nominatim search query.
  const structured = [facility.name, facility.city, facility.district, facility.state]
    .filter((v) => typeof v === "string" && v.trim())
    .map((v) => v!.trim()).filter((v, i, all) => all.indexOf(v) === i);
  const query = (structured.length ? structured : [facility.address?.trim()].filter(Boolean)).join(", ");
  if (!query) return null;
  return `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(query)}`;
}
