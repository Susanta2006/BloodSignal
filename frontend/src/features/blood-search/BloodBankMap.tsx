import { useMemo, useState } from "react";

export type FacilityMapItem = {
  id: string;
  name: string;
  address?: string;
  latitude: number;
  longitude: number;
  location_precision?: "FACILITY" | "LOCALITY" | null;
  location_note?: string | null;
};

type Props = { facilities: FacilityMapItem[] };

function validCoordinates(latitude: number, longitude: number): boolean {
  return Number.isFinite(latitude) && Number.isFinite(longitude) &&
    Math.abs(latitude) <= 90 && Math.abs(longitude) <= 180;
}

/** Google Maps web embed (not the Maps JavaScript API); only validated source coordinates are mapped. */
export function BloodBankMap({ facilities }: Props) {
  const validFacilities = useMemo(() => {
    const seen = new Set<string>();
    return facilities.filter((facility) => {
      if (!validCoordinates(facility.latitude, facility.longitude)) return false;
      const coordinateKey = `${facility.latitude.toFixed(5)},${facility.longitude.toFixed(5)}`;
      if (seen.has(coordinateKey)) return false;
      seen.add(coordinateKey);
      return true;
    });
  }, [facilities]);
  const [selectedId, setSelectedId] = useState<string>(validFacilities[0]?.id ?? "");
  const selected = validFacilities.find((facility) => facility.id === selectedId) ?? validFacilities[0];

  if (validFacilities.length === 0 || !selected) {
    return <p className="border-y py-6 text-sm text-muted-foreground">No facilities in these results have validated coordinates. The facility list remains available; no location has been guessed.</p>;
  }

  const lat = selected.latitude;
  const lon = selected.longitude;
  const embedUrl = `https://maps.google.com/maps?q=${encodeURIComponent(`${lat},${lon}`)}&z=15&output=embed`;
  const googleMapsUrl = `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(`${lat},${lon}`)}`;

  return (
    <div className="space-y-3">
      <label htmlFor="google-facility" className="block text-sm font-medium">Show facility on Google Maps</label>
      <select id="google-facility" className="h-11 w-full rounded-sm border bg-background px-3 text-sm" value={selected.id} onChange={(event) => setSelectedId(event.target.value)}>
        {validFacilities.map((facility) => <option key={facility.id} value={facility.id}>{facility.name}</option>)}
      </select>
      <div className="h-[360px] overflow-hidden rounded-sm border sm:h-[440px]">
        <iframe title={`Google Maps location for ${selected.name}`} src={embedUrl} className="h-full w-full border-0" loading="lazy" referrerPolicy="no-referrer-when-downgrade" />
      </div>
      <p className="text-sm"><strong>{selected.name}</strong>{selected.address ? ` — ${selected.address}` : ""}</p>
      {selected.location_precision === "LOCALITY" ? <p className="text-sm font-medium text-amber-700">Approximate location: town/district centre, not the facility.</p> : null}
      {selected.location_note ? <p className="text-xs leading-5 text-muted-foreground">{selected.location_note}</p> : null}
      <p className="text-xs leading-5 text-muted-foreground">{selected.location_precision === "LOCALITY" ? "This is a locality-level pin and must not be treated as the facility entrance or road distance." : "Map marker shows the available coordinates only."} A marker does not confirm current stock, component availability, or opening status. Map imagery and labels are provided by Google Maps. The marker uses the source coordinates shown below.</p>
      <a className="inline-flex min-h-11 items-center underline underline-offset-4" href={googleMapsUrl} target="_blank" rel="noreferrer">Open this location in Google Maps</a>
    </div>
  );
}
