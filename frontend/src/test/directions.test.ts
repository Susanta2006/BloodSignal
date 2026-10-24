import { describe, expect, it } from "vitest";

import {
  buildGoogleMapsEmbedUrl,
  buildDirectionsUrl,
  hasReliableFacilityDestination,
  travelModes,
} from "@/features/blood-search/directions";

describe("Google Maps destination URLs", () => {
  it("embeds a route from the shared device origin to the selected facility", () => {
    const url = buildGoogleMapsEmbedUrl(
      { latitude: 24.81, longitude: 93.91 },
      { latitude: 24.8, longitude: 93.9 },
    );
    const parsed = new URL(url!);

    expect(parsed.origin + parsed.pathname).toBe(
      "https://maps.google.com/maps",
    );
    expect(parsed.searchParams.get("saddr")).toBe("24.8,93.9");
    expect(parsed.searchParams.get("daddr")).toBe("24.81,93.91");
    expect(parsed.searchParams.get("dirflg")).toBe("d");
  });

  it("shows only a facility pin when no device origin was shared", () => {
    const url = buildGoogleMapsEmbedUrl({ latitude: 24.81, longitude: 93.91 });
    const parsed = new URL(url!);

    expect(parsed.searchParams.get("q")).toBe("24.81,93.91");
    expect(parsed.searchParams.has("saddr")).toBe(false);
  });

  it("embeds a route from a manually selected district when device coordinates are unavailable", () => {
    const url = buildGoogleMapsEmbedUrl(
      { latitude: 24.81, longitude: 93.91 },
      null,
      "Imphal West, Manipur, India",
    );
    const parsed = new URL(url!);

    expect(parsed.searchParams.get("saddr")).toBe(
      "Imphal West, Manipur, India",
    );
    expect(parsed.searchParams.get("daddr")).toBe("24.81,93.91");
    expect(parsed.searchParams.get("dirflg")).toBe("d");
  });

  it("uses the selected travel mode in an embedded route", () => {
    const url = buildGoogleMapsEmbedUrl(
      { latitude: 24.81, longitude: 93.91 },
      { latitude: 24.8, longitude: 93.9 },
      null,
      "walking",
    );

    expect(new URL(url!).searchParams.get("dirflg")).toBe("w");
  });

  it("uses validated facility coordinates before its address and leaves origin unspecified", () => {
    const url = buildDirectionsUrl(
      {
        latitude: 24.8,
        longitude: 93.94,
        address: "Hospital Road, Imphal, Manipur, India",
      },
      "driving",
    );

    expect(url).not.toBeNull();
    const parsed = new URL(url!);
    expect(parsed.origin + parsed.pathname).toBe(
      "https://www.google.com/maps/dir/",
    );

    expect(parsed.searchParams.get("destination")).toBe("24.8,93.94");

    expect(parsed.searchParams.has("origin")).toBe(false);
  });

  it("uses Google Maps search rather than a broken route when facility coordinates are missing", () => {
    const url = buildDirectionsUrl(
      {
        name: "AGMC & GBP Hospital",
        address: "A very long messy source address, landmark and extra text",
        city: "Agartala",
        district: "West Tripura",
        state: "Tripura",
      },
      "driving",
      "West Tripura, Tripura, India",
    );
    const parsed = new URL(url!);
    expect(parsed.origin + parsed.pathname).toBe(
      "https://www.google.com/maps/search/",
    );
    expect(parsed.searchParams.get("query")).toBe(
      "AGMC & GBP Hospital, Agartala, West Tripura, Tripura",
    );
    expect(parsed.searchParams.get("query")).not.toContain(
      "messy source address",
    );
    expect(parsed.searchParams.has("destination")).toBe(false);
  });

  it("URL-encodes address search when valid facility coordinates are absent", () => {
    const address = "Blood Centre, MG Road & Market, Imphal, Manipur";
    const url = buildDirectionsUrl(
      { name: "Blood Centre", city: "Imphal", state: "Manipur" },
      "transit",
    );

    expect(url).not.toBeNull();
    const parsed = new URL(url!);
    expect(parsed.searchParams.get("query")).toBe(
      "Blood Centre, Imphal, Manipur",
    );
    expect(parsed.search).not.toContain("%26");
  });

  it("keeps destination links available for existing travel-mode choices", () => {
    for (const travelMode of travelModes) {
      const url = buildDirectionsUrl(
        {
          name: "District Blood Centre",
          city: "Imphal",
          district: "Imphal West",
          state: "Manipur",
        },
        travelMode,
      );
      expect(url && new URL(url).searchParams.get("query")).toBe(
        "District Blood Centre, Imphal, Imphal West, Manipur",
      );
    }
  });

  it("disables directions when a facility has no usable destination", () => {
    expect(hasReliableFacilityDestination({})).toBe(false);
    expect(hasReliableFacilityDestination({ address: "Unavailable" })).toBe(
      false,
    );
    expect(buildDirectionsUrl({}, "driving")).toBeNull();
  });
});
