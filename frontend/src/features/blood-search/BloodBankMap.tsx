import { useEffect, useMemo, useState } from "react";
import { facilityDistanceKm, validLatLon, type LatLon } from "@/features/blood-search/distance";
import { buildDirectionsUrl, buildGoogleMapsEmbedUrl } from "@/features/blood-search/directions";

export type FacilityMapItem = {
  id: string;
  name: string;
  address?: string;
  latitude: number;
  longitude: number;
  location_precision?: "FACILITY" | "LOCALITY" | null;
  location_note?: string | null;
};

type Props = { facilities: FacilityMapItem[]; origin?: LatLon | null };

function validCoordinates(latitude: number, longitude: number): boolean {
  return Number.isFinite(latitude) && Number.isFinite(longitude) &&
    Math.abs(latitude) <= 90 && Math.abs(longitude) <= 180;
}

/** Google Maps web embed (not the Maps JavaScript API); only validated source coordinates are mapped. */
export function BloodBankMap({ facilities, origin = null }: Props) {
  const validFacilities = useMemo(() => {
    return facilities.filter((facility) => {
      return Boolean(
        facility.id &&
          facility.name.trim() &&
          validCoordinates(facility.latitude, facility.longitude),
      );
    });
  }, [facilities]);
  const [selectedId, setSelectedId] = useState<string>(validFacilities[0]?.id ?? "");
  useEffect(() => {
    setSelectedId(validFacilities[0]?.id ?? "");
  }, [validFacilities]);
  const selected = validFacilities.find((facility) => facility.id === selectedId) ?? validFacilities[0];

  if (validFacilities.length === 0 || !selected) {
    return <p className="border-y py-6 text-sm text-muted-foreground">No facilities in these results have validated coordinates. The facility list remains available; no location has been guessed.</p>;
  }

  const lat = selected.latitude;
  const lon = selected.longitude;
  const destination = { ...selected, latitude: lat, longitude: lon };
  const embedUrl = buildGoogleMapsEmbedUrl(destination, origin);
  const googleMapsUrl = buildDirectionsUrl(
    destination,
    "driving",
    validLatLon(origin) ? `${origin.latitude},${origin.longitude}` : undefined,
  );
  const straightLineKm = facilityDistanceKm(
    origin,
    { latitude: lat, longitude: lon },
    selected.location_precision,
  );

  return (
    <div className="space-y-3">
      <label htmlFor="google-facility" className="block text-sm font-medium">Show facility and route on Google Maps</label>
      <select id="google-facility" className="h-11 w-full rounded-sm border bg-background px-3 text-sm" value={selected.id} onChange={(event) => setSelectedId(event.target.value)}>
        {validFacilities.map((facility) => <option key={facility.id} value={facility.id}>{facility.name}</option>)}
      </select>
      <div className="h-[360px] overflow-hidden rounded-sm border sm:h-[440px]">
        {embedUrl ? (
          <iframe title={`Google Maps route to ${selected.name}`} src={embedUrl} className="h-full w-full border-0" loading="lazy" referrerPolicy="no-referrer-when-downgrade" />
        ) : null}
      </div>
      <p className="text-sm"><strong>{selected.name}</strong>{selected.address ? ` — ${selected.address}` : ""}</p>
      {selected.location_precision === "LOCALITY" ? <p className="text-sm font-medium text-amber-700">Approximate location: town/district centre, not the facility.</p> : null}
      {selected.location_note ? <p className="text-xs leading-5 text-muted-foreground">{selected.location_note}</p> : null}
      <p className="text-xs leading-5 text-muted-foreground">
        {validLatLon(origin)
          ? "The map shows your shared device location and the selected facility, with Google Maps directions between them."
          : "The map shows the facility at its source coordinates. Share device location to show a route from your current location."}
        {" "}Map imagery and directions are provided by Google Maps; straight-line distance is not road distance or travel time.
      </p>
      <dl className="grid gap-2 text-xs text-muted-foreground sm:grid-cols-2">
        <div><dt className="font-medium text-foreground">Facility coordinates</dt><dd>{lat.toFixed(6)}, {lon.toFixed(6)}</dd></div>
        {validLatLon(origin) ? <div><dt className="font-medium text-foreground">Your shared location</dt><dd>{origin.latitude.toFixed(6)}, {origin.longitude.toFixed(6)}</dd></div> : null}
        <div><dt className="font-medium text-foreground">Straight-line distance</dt><dd>{straightLineKm === null ? "Unavailable from locality or ambiguous coordinates" : `${straightLineKm.toFixed(2)} km`}</dd></div>
      </dl>
      {googleMapsUrl ? <a className="inline-flex min-h-11 items-center underline underline-offset-4" href={googleMapsUrl} target="_blank" rel="noreferrer">Open directions in Google Maps</a> : null}
    </div>
  );
}
