import { useCallback, useEffect, useMemo, useState } from "react";

/**
 * Inventory row shapes accepted from the legacy UI and normalized API.
 * The supplied repository archive does not contain the original frontend app,
 * so this component intentionally avoids assumptions about a particular API client.
 */
export interface InventoryItem {
  id?: string | number;
  bank_id?: string;
  bank_name?: string;
  facility_name?: string;
  name?: string;
  blood_group?: string;
  bloodGroup?: string;
  component?: string;
  units?: number | string | null;
  available_units?: number | string | null;
  reported_units?: number | string | null;
  quantity?: number | string | null;
  district?: string;
  address?: string;
  phone?: string | null;
  distance_km?: number | null;
}

export interface DeviceLocation {
  latitude: number;
  longitude: number;
  accuracy: number;
}

export interface InventoryTableProps {
  items?: InventoryItem[];
  onLocationUpdate?: (location: DeviceLocation) => void;
  onError?: (message: string) => void;
}

const PAGE_SIZE = 5;

function getReportedUnits(item: InventoryItem): number | null {
  // Prefer the UI's canonical `units` field, then accept supported API variants.
  const raw = item.units ?? item.available_units ?? item.reported_units ?? item.quantity;
  if (raw === null || raw === undefined || raw === "") return null;
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
}

function nameOf(item: InventoryItem): string {
  return item.name || item.bank_name || item.facility_name || "Unnamed facility";
}

function unitsOf(item: InventoryItem): number | string {
  return item.units ?? item.available_units ?? item.reported_units ?? item.quantity ?? "—";
}

function groupOf(item: InventoryItem): string {
  return item.blood_group || item.bloodGroup || "—";
}

function componentOf(item: InventoryItem): string {
  return item.component || "—";
}

function districtOf(item: InventoryItem): string {
  return item.district || "—";
}

function itemKey(item: InventoryItem, index: number): string {
  return String(item.id ?? item.bank_id ?? `${nameOf(item)}-${index}`);
}

export default function InventoryTable({
  items = [],
  onLocationUpdate,
  onError,
}: InventoryTableProps) {
  const [page, setPage] = useState<number>(1);
  const [locationStatus, setLocationStatus] = useState<string>("");

  // Safeguard: unknown, zero, negative, and malformed quantities are not matches.
  // Filter before slicing so every page contains up to exactly five valid records.
  const positiveItems = useMemo(
    () => (Array.isArray(items) ? items : []).filter((item) => {
      const units = getReportedUnits(item);
      return units !== null && units > 0;
    }),
    [items],
  );

  const pageCount = Math.max(1, Math.ceil(positiveItems.length / PAGE_SIZE));
  useEffect(() => {
    setPage((current) => Math.min(current, pageCount));
  }, [pageCount]);

  const visibleItems = positiveItems.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  const startEntry = positiveItems.length > 0 ? (page - 1) * PAGE_SIZE + 1 : 0;
  const endEntry = Math.min(page * PAGE_SIZE, positiveItems.length);

  const handleDeviceLocation = useCallback((): void => {
    if (!navigator.geolocation) {
      const message = "This browser does not support device geolocation.";
      setLocationStatus(message);
      onError?.(message);
      return;
    }

    const isLocalhost = ["localhost", "127.0.0.1", "[::1]"].includes(window.location.hostname);
    if (!window.isSecureContext && !isLocalhost) {
      const message = "Device location requires HTTPS or localhost. Open this page in a secure context.";
      setLocationStatus(message);
      onError?.(message);
      return;
    }

    setLocationStatus("Requesting location permission…");
    navigator.geolocation.getCurrentPosition(
      (position: GeolocationPosition) => {
        const location: DeviceLocation = {
          latitude: position.coords.latitude,
          longitude: position.coords.longitude,
          accuracy: position.coords.accuracy,
        };
        setLocationStatus("Location updated.");
        onLocationUpdate?.(location);
        // Keep the event contract requested by the application. Coordinates are
        // passed through onLocationUpdate rather than attached to a generic Event.
        window.dispatchEvent(new Event("location_updated"));
      },
      (error: GeolocationPositionError) => {
        const messages: Record<number, string> = {
          [error.PERMISSION_DENIED]: "Location permission was denied. Allow location access in browser settings and retry.",
          [error.POSITION_UNAVAILABLE]: "Device location is currently unavailable. Check GPS/location services and retry.",
          [error.TIMEOUT]: "The location request timed out. Please retry.",
        };
        const message = messages[error.code] || "Unable to obtain device location.";
        setLocationStatus(message);
        onError?.(message);
      },
      { enableHighAccuracy: true, timeout: 12_000, maximumAge: 30_000 },
    );
  }, [onLocationUpdate, onError]);

  return (
    <section className="w-full space-y-4">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <h2 className="text-lg font-semibold">Blood inventory</h2>
        <button
          type="button"
          onClick={handleDeviceLocation}
          className="rounded-md border px-3 py-2 text-sm font-medium hover:bg-gray-50"
        >
          Use my location
        </button>
      </div>

      {locationStatus && (
        <p role="status" aria-live="polite" className="text-sm text-gray-600">
          {locationStatus}
        </p>
      )}

      {/* Desktop/tablet layout */}
      <div className="hidden overflow-x-auto rounded-lg border md:block">
        <table className="w-full border-collapse text-left text-sm">
          <thead className="bg-gray-50">
            <tr>
              {["Facility", "Blood group", "Component", "Units", "District", "Contact"].map((heading) => (
                <th key={heading} scope="col" className="px-4 py-3 font-semibold">{heading}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visibleItems.map((item, index) => (
              <tr key={itemKey(item, index)} className="border-t">
                <td className="px-4 py-3">{nameOf(item)}</td>
                <td className="px-4 py-3">{groupOf(item)}</td>
                <td className="px-4 py-3">{componentOf(item)}</td>
                <td className="px-4 py-3 font-medium">{unitsOf(item)}</td>
                <td className="px-4 py-3">{districtOf(item)}</td>
                <td className="px-4 py-3">
                  {item.phone ? <a className="underline" href={`tel:${item.phone}`}>{item.phone}</a> : "Not provided"}
                </td>
              </tr>
            ))}
            {visibleItems.length === 0 && (
              <tr><td colSpan={6} className="px-4 py-8 text-center text-gray-500">No positive-stock records found.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Mobile layout */}
      <div className="block space-y-3 md:hidden">
        {visibleItems.map((item, index) => (
          <article key={itemKey(item, index)} className="rounded-lg border p-4 shadow-sm">
            <div className="flex items-start justify-between gap-3">
              <h3 className="font-semibold">{nameOf(item)}</h3>
              <span className="shrink-0 rounded-full bg-green-50 px-2 py-1 text-sm font-semibold text-green-800">
                {unitsOf(item)} units
              </span>
            </div>
            <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 text-sm">
              <div><dt className="text-gray-500">Blood group</dt><dd>{groupOf(item)}</dd></div>
              <div><dt className="text-gray-500">Component</dt><dd>{componentOf(item)}</dd></div>
              <div><dt className="text-gray-500">District</dt><dd>{districtOf(item)}</dd></div>
              <div>
                <dt className="text-gray-500">Contact</dt>
                <dd>{item.phone ? <a className="underline" href={`tel:${item.phone}`}>{item.phone}</a> : "Not provided"}</dd>
              </div>
            </dl>
          </article>
        ))}
        {visibleItems.length === 0 && (
          <p className="rounded-lg border p-6 text-center text-sm text-gray-500">No positive-stock records found.</p>
        )}
      </div>

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-sm text-gray-600" aria-live="polite">
          Showing {startEntry}–{endEntry} of {positiveItems.length} entries
        </p>
        <nav aria-label="Inventory pagination" className="flex flex-wrap items-center gap-1">
          <button
            type="button"
            onClick={() => setPage((current) => Math.max(1, current - 1))}
            disabled={page <= 1}
            className="rounded border px-3 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-40"
          >Previous</button>
          {Array.from({ length: pageCount }, (_, index) => index + 1).map((number) => (
            <button
              type="button"
              key={number}
              onClick={() => setPage(number)}
              aria-current={page === number ? "page" : undefined}
              className={`min-w-9 rounded border px-3 py-2 text-sm ${page === number ? "bg-gray-900 text-white" : "hover:bg-gray-50"}`}
            >{number}</button>
          ))}
          <button
            type="button"
            onClick={() => setPage((current) => Math.min(pageCount, current + 1))}
            disabled={page >= pageCount}
            className="rounded border px-3 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-40"
          >Next</button>
        </nav>
      </div>
    </section>
  );
}
