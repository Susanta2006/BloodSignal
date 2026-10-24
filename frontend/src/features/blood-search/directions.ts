import { validLatLon, type LatLon } from "@/features/blood-search/distance";

export const travelModes = [
  "driving",
  "walking",
  "transit",
  "two-wheeler",
] as const;

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

/** A no-key Google Maps embed for a selected destination, optionally showing a route from GPS. */
export function buildGoogleMapsEmbedUrl(
  facility: FacilityDestination,
  origin?: LatLon | null,
  originText?: string | null,
  travelMode: TravelMode = "driving",
): string | null {
  if (!hasFacilityCoordinates(facility)) return null;

  const destination = `${facility.latitude},${facility.longitude}`;
  const directionsFlag =
    travelMode === "walking" ? "w" : travelMode === "transit" ? "r" : "d";
  const params = new URLSearchParams({ output: "embed" });
  if (validLatLon(origin)) {
    params.set("saddr", `${origin.latitude},${origin.longitude}`);
    params.set("daddr", destination);
    params.set("dirflg", directionsFlag);
  } else if (usableText(originText)) {
    params.set("saddr", originText.trim());
    params.set("daddr", destination);
    params.set("dirflg", directionsFlag);
  } else {
    params.set("q", destination);
    params.set("z", "15");
  }
  return `https://maps.google.com/maps?${params.toString()}`;
}

/** Search fallback for a named facility without verified coordinates; it is not a pin or route. */
export function buildGoogleMapsSearchEmbedUrl(
  facility: FacilityDestination,
): string | null {
  const parts = [
    facility.name,
    facility.address,
    facility.city,
    facility.district,
    facility.state,
    "India",
  ]
    .filter(usableText)
    .map((value) => value.trim())
    .filter((value, index, all) => all.indexOf(value) === index);

  if (parts.length === 0) return null;
  const params = new URLSearchParams({ q: parts.join(", "), output: "embed" });
  return `https://maps.google.com/maps?${params.toString()}`;
}

/**
 * Name + address + locality as one Google Maps place query, e.g.
 * "AGMC & GBP Hospital, GB Bazar, Agartala, West Tripura, Tripura, India".
 * Shared by the search preview and the text-destination route so both resolve
 * the same place.
 */
export function buildFacilityQueryText(
  facility: FacilityDestination,
): string | null {
  const parts = [
    facility.name,
    facility.address,
    facility.city,
    facility.district,
    facility.state,
    "India",
  ]
    .filter(usableText)
    .map((value) => value.trim())
    .filter((value, index, all) => all.indexOf(value) === index);
  return parts.length === 0 ? null : parts.join(", ");
}

/**
 * Embedded Google Maps route whose destination is the facility's name and
 * address (not coordinates). Used when no facility-level coordinates exist, or
 * when the only coordinates are a town/district centre. Returns null when there
 * is no usable starting point or no usable destination text.
 */
export function buildGoogleMapsTextRouteEmbedUrl(
  facility: FacilityDestination,
  origin?: LatLon | null,
  originText?: string | null,
  travelMode: TravelMode = "driving",
): string | null {
  const destination = buildFacilityQueryText(facility);
  if (!destination) return null;
  const start = validLatLon(origin)
    ? `${origin.latitude},${origin.longitude}`
    : usableText(originText)
      ? originText.trim()
      : null;
  if (!start) return null;

  const directionsFlag =
    travelMode === "walking" ? "w" : travelMode === "transit" ? "r" : "d";
  const params = new URLSearchParams({ output: "embed" });
  params.set("saddr", start);
  params.set("daddr", destination);
  params.set("dirflg", directionsFlag);
  return `https://maps.google.com/maps?${params.toString()}`;
}

/**
 * Google Maps directions link to the facility's name and address. When no
 * origin is given the link omits it, so Google Maps (the native app on phones)
 * starts from the device's own current location.
 */
export function buildTextDirectionsUrl(
  facility: FacilityDestination,
  travelMode: TravelMode,
  origin?: string,
): string | null {
  const destination = buildFacilityQueryText(facility);
  if (!destination) return null;
  const googleMode =
    travelMode === "walking"
      ? "walking"
      : travelMode === "transit"
        ? "transit"
        : "driving";
  const params = new URLSearchParams({ api: "1" });
  params.set("destination", destination);
  params.set("travelmode", googleMode);
  if (usableText(origin)) params.set("origin", origin.trim());
  return `https://www.google.com/maps/dir/?${params.toString()}`;
}

const INVALID_VALUES = new Set([
  "unknown",
  "unavailable",
  "not provided",
  "n/a",
  "none",
  "null",
]);

function usableText(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.trim().length > 0 &&
    !INVALID_VALUES.has(value.trim().toLowerCase())
  );
}

function validCoordinates(
  latitude: number | null | undefined,
  longitude: number | null | undefined,
): boolean {
  return (
    typeof latitude === "number" &&
    Number.isFinite(latitude) &&
    latitude >= -90 &&
    latitude <= 90 &&
    typeof longitude === "number" &&
    Number.isFinite(longitude) &&
    longitude >= -180 &&
    longitude <= 180
  );
}

export function hasFacilityCoordinates(facility: FacilityDestination): boolean {
  return validCoordinates(facility.latitude, facility.longitude);
}

export function hasReliableFacilityDestination(
  facility: FacilityDestination,
): boolean {
  if (hasFacilityCoordinates(facility)) return true;

  const locality = [facility.city, facility.district, facility.state].some(
    usableText,
  );

  return (
    (usableText(facility.name) && locality) ||
    (usableText(facility.address) && locality)
  );
}

/**
 * Opens Google Maps directions when coordinates are available.
 * Otherwise, opens a structured facility search.
 * Google Maps links do not require a Maps API key.
 */
export function buildDirectionsUrl(
  facility: FacilityDestination,
  travelMode: TravelMode,
  origin?: string,
): string | null {
  const params = new URLSearchParams({ api: "1" });

  if (hasFacilityCoordinates(facility)) {
    params.set("destination", `${facility.latitude},${facility.longitude}`);

    const googleMode =
      travelMode === "walking"
        ? "walking"
        : travelMode === "transit"
          ? "transit"
          : "driving";

    params.set("travelmode", googleMode);

    if (usableText(origin)) {
      params.set("origin", origin.trim());
    }

    return `https://www.google.com/maps/dir/?${params.toString()}`;
  }

  const locality = [facility.city, facility.district, facility.state]
    .filter(usableText)
    .map((value) => value.trim())
    .filter((value, index, all) => all.indexOf(value) === index);
  const facilityIdentity = usableText(facility.name)
    ? facility.name.trim()
    : usableText(facility.address)
      ? facility.address.trim()
      : null;
  const structured = [facilityIdentity, ...locality].filter(
    (value): value is string => value !== null,
  );

  if (structured.length === 0) return null;

  params.set("query", structured.join(", "));

  return `https://www.google.com/maps/search/?${params.toString()}`;
}
