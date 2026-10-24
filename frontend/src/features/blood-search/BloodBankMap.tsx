import { useEffect, useMemo, useState } from "react";
import {
  facilityDistanceKm,
  validLatLon,
  type LatLon,
} from "@/features/blood-search/distance";
import {
  buildDirectionsUrl,
  buildGoogleMapsEmbedUrl,
  buildGoogleMapsSearchEmbedUrl,
  buildGoogleMapsTextRouteEmbedUrl,
  buildTextDirectionsUrl,
  type TravelMode,
} from "@/features/blood-search/directions";

export type FacilityMapItem = {
  id: string;
  name: string;
  address?: string;
  city?: string;
  district?: string;
  state?: string;
  latitude?: number | null;
  longitude?: number | null;
  location_precision?: "FACILITY" | "LOCALITY" | null;
  location_note?: string | null;
};

type Props = {
  facilities: FacilityMapItem[];
  origin?: LatLon | null;
  originLabel?: string | null;
  travelMode?: TravelMode;
};

function validCoordinates(latitude: number, longitude: number): boolean {
  return (
    Number.isFinite(latitude) &&
    Number.isFinite(longitude) &&
    Math.abs(latitude) <= 90 &&
    Math.abs(longitude) <= 180
  );
}

/** Google Maps web embed (not the Maps JavaScript API); only validated source coordinates are mapped. */
export function BloodBankMap({
  facilities,
  origin = null,
  originLabel = null,
  travelMode = "driving",
}: Props) {
  const validFacilities = useMemo(() => {
    // Keep named results selectable even when the backend has no verified pin yet.
    // Those entries get a Google Maps name/address search preview, never a guessed pin.
    return facilities.filter((facility) => Boolean(facility.id && facility.name.trim()));
  }, [facilities]);
  const [selectedId, setSelectedId] = useState<string>(
    validFacilities[0]?.id ?? "",
  );
  useEffect(() => {
    setSelectedId((current) =>
      validFacilities.some((facility) => facility.id === current)
        ? current
        : (validFacilities[0]?.id ?? ""),
    );
  }, [validFacilities]);
  const selected =
    validFacilities.find((facility) => facility.id === selectedId) ??
    validFacilities[0];

  if (validFacilities.length === 0 || !selected) {
    return (
      <p className="border-y py-6 text-sm text-muted-foreground">
        No named facilities are available to preview on the map. The facility list
        remains available; no location has been guessed.
      </p>
    );
  }

  const hasCoordinates = validCoordinates(selected.latitude ?? NaN, selected.longitude ?? NaN);
  // A town/district-centre coordinate is not the facility. Route to it by name and
  // address instead, exactly like a facility that has no coordinates at all.
  const facilityLevelPin =
    hasCoordinates && selected.location_precision !== "LOCALITY";
  const destination = { ...selected };
  const textRouteUrl = facilityLevelPin
    ? null
    : buildGoogleMapsTextRouteEmbedUrl(destination, origin, originLabel, travelMode);
  const showsTextRoute = textRouteUrl !== null;
  const embedUrl = facilityLevelPin
    ? buildGoogleMapsEmbedUrl(destination, origin, originLabel, travelMode)
    : (textRouteUrl ?? buildGoogleMapsSearchEmbedUrl(destination));
  const originParam = validLatLon(origin)
    ? `${origin.latitude},${origin.longitude}`
    : (originLabel ?? undefined);
  const googleMapsUrl = facilityLevelPin
    ? buildDirectionsUrl(destination, travelMode, originParam)
    : buildTextDirectionsUrl(destination, travelMode, originParam);
  const hasRoute =
    showsTextRoute ||
    (facilityLevelPin && (validLatLon(origin) || Boolean(originLabel)));
  const straightLineKm = hasCoordinates
    ? facilityDistanceKm(
        origin,
        { latitude: selected.latitude!, longitude: selected.longitude! },
        selected.location_precision,
      )
    : null;

  return (
    <div className="space-y-3">
      <label htmlFor="google-facility" className="block text-sm font-medium">
        Select a facility to preview on Google Maps
      </label>
      <select
        id="google-facility"
        className="h-11 w-full rounded-sm border bg-background px-3 text-sm"
        value={selected.id}
        onChange={(event) => setSelectedId(event.target.value)}
      >
        {validFacilities.map((facility) => (
          <option key={facility.id} value={facility.id}>
            {facility.name}
          </option>
        ))}
      </select>
      <div className="h-[360px] overflow-hidden rounded-sm border sm:h-[440px]">
        {embedUrl ? (
          <iframe
            key={embedUrl}
            title={
              facilityLevelPin || showsTextRoute
                ? `Google Maps route to ${selected.name}`
                : `Google Maps search for ${selected.name}`
            }
            src={embedUrl}
            className="h-full w-full border-0"
            loading="lazy"
            referrerPolicy="no-referrer-when-downgrade"
          />
        ) : null}
      </div>
      <p className="text-sm">
        <strong>{selected.name}</strong>
        {selected.address ? ` — ${selected.address}` : ""}
      </p>
      {selected.location_precision === "LOCALITY" ? (
        <p className="text-sm font-medium text-amber-700">
          Coordinates on file are only a town/district centre, not the facility. The
          straight-line distance shown is from your location to that centre, not to
          the facility itself. Google Maps routing uses the facility name and address.
        </p>
      ) : null}
      {selected.location_note ? (
        <p className="text-xs leading-5 text-muted-foreground">
          {selected.location_note}
        </p>
      ) : null}
      {!hasCoordinates && showsTextRoute ? (
        <p className="text-sm font-medium text-amber-700">
          Facility coordinates are not on file, so the route goes to the facility
          by its name and address as Google Maps resolves them. Check the
          destination before travelling; straight-line distance is unavailable.
        </p>
      ) : null}
      {!hasCoordinates && !showsTextRoute ? (
        <p className="text-sm font-medium text-amber-700">
          Facility coordinates are not verified. This is a name-and-address search
          preview, not a confirmed pin. Share device location or choose a district
          to show a route from your starting point.
        </p>
      ) : null}
      <p className="text-xs leading-5 text-muted-foreground">
        {!facilityLevelPin
          ? showsTextRoute
            ? validLatLon(origin)
              ? "Google Maps is routing from your shared device location to the facility using its name and address."
              : `Google Maps is routing from ${originLabel} to the facility using its name and address. This is a district-level starting area, not your precise location.`
            : "Google Maps is searching by facility name and address. Check the result before travelling; this preview does not confirm the exact facility location."
          : validLatLon(origin)
            ? "The map shows your shared device location and the selected facility. Open directions for Google Maps' current route options."
            : originLabel
              ? `Google Maps directions start from ${originLabel}. This is a district-level starting area, not your precise location.`
              : "The map shows the facility at its source coordinates. Share device location to show a route from your current location."}{" "}
        {hasRoute
          ? "Where Google Maps offers alternative routes, compare their distances and choose the shortest. "
          : "Opening directions in Google Maps starts from your device's own current location. "}
        {facilityLevelPin
          ? "Map imagery and routing are provided by Google Maps."
          : "Map search results and routing are provided by Google Maps."}{" "}
        Straight-line distance is not road distance or travel time.
      </p>
      <dl className="grid gap-2 text-xs text-muted-foreground sm:grid-cols-2">
        <div>
          <dt className="font-medium text-foreground">Facility coordinates</dt>
          <dd>
            {hasCoordinates
              ? `${selected.latitude!.toFixed(6)}, ${selected.longitude!.toFixed(6)}`
              : "Not available"}
          </dd>
        </div>
        {hasRoute && validLatLon(origin) ? (
          <div>
            <dt className="font-medium text-foreground">
              Your shared location
            </dt>
            <dd>
              {origin.latitude.toFixed(6)}, {origin.longitude.toFixed(6)}
            </dd>
          </div>
        ) : null}
        {hasRoute && !validLatLon(origin) && originLabel ? (
          <div>
            <dt className="font-medium text-foreground">Starting area</dt>
            <dd>{originLabel}</dd>
          </div>
        ) : null}
        <div>
          <dt className="font-medium text-foreground">
            Straight-line distance
          </dt>
          <dd>
            {straightLineKm === null
              ? "Unavailable—coordinates missing or invalid"
              : `${straightLineKm.toFixed(2)} km (straight-line, not road distance)`}
          </dd>
        </div>
      </dl>
      {googleMapsUrl ? (
        <a
          className="inline-flex min-h-11 items-center underline underline-offset-4"
          href={googleMapsUrl}
          target="_blank"
          rel="noreferrer"
        >
          {`Open ${travelMode} directions in Google Maps`}
        </a>
      ) : null}
    </div>
  );
}
