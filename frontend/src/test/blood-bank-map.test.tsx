import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  BloodBankMap,
  type FacilityMapItem,
} from "@/features/blood-search/BloodBankMap";

const bankA: FacilityMapItem = {
  id: "bank-a",
  name: "Bank A",
  latitude: 24.8,
  longitude: 93.9,
};
const bankB: FacilityMapItem = {
  id: "bank-b",
  name: "Bank B",
  latitude: 24.81,
  longitude: 93.91,
};
const facilities: FacilityMapItem[] = [bankA, bankB];

describe("blood bank map selection", () => {
  it("updates the route immediately when the selected facility changes", () => {
    render(
      <BloodBankMap
        facilities={facilities}
        origin={{ latitude: 24.79, longitude: 93.89 }}
      />,
    );

    const map = screen.getByTitle(
      "Google Maps route to Bank A",
    ) as HTMLIFrameElement;
    expect(new URL(map.src).searchParams.get("daddr")).toBe("24.8,93.9");

    fireEvent.change(
      screen.getByLabelText("Select a facility to preview on Google Maps"),
      {
        target: { value: "bank-b" },
      },
    );

    expect(
      screen.getByTitle("Google Maps route to Bank B"),
    ).toBeInTheDocument();
    const nextMap = screen.getByTitle(
      "Google Maps route to Bank B",
    ) as HTMLIFrameElement;
    expect(new URL(nextMap.src).searchParams.get("daddr")).toBe("24.81,93.91");
  });

  it("refreshes the selected facility's coordinates without switching back to the first bank", () => {
    const { rerender } = render(
      <BloodBankMap
        facilities={facilities}
        origin={{ latitude: 24.79, longitude: 93.89 }}
      />,
    );
    fireEvent.change(
      screen.getByLabelText("Select a facility to preview on Google Maps"),
      {
        target: { value: "bank-b" },
      },
    );

    rerender(
      <BloodBankMap
        facilities={[
          bankA,
          { ...bankB, latitude: 24.82, longitude: 93.92 },
        ]}
        origin={{ latitude: 24.79, longitude: 93.89 }}
      />,
    );

    expect(
      screen.getByTitle("Google Maps route to Bank B"),
    ).toBeInTheDocument();
    const updatedMap = screen.getByTitle(
      "Google Maps route to Bank B",
    ) as HTMLIFrameElement;
    expect(new URL(updatedMap.src).searchParams.get("daddr")).toBe(
      "24.82,93.92",
    );
    expect(screen.getByText("24.820000, 93.920000")).toBeInTheDocument();
  });

  it("routes from the manually selected district when device GPS is unavailable", () => {
    render(
      <BloodBankMap
        facilities={facilities}
        originLabel="Imphal West, Manipur, India"
      />,
    );

    const map = screen.getByTitle(
      "Google Maps route to Bank A",
    ) as HTMLIFrameElement;
    const params = new URL(map.src).searchParams;
    expect(params.get("saddr")).toBe("Imphal West, Manipur, India");
    expect(params.get("daddr")).toBe("24.8,93.9");
    expect(screen.getByText("Imphal West, Manipur, India")).toBeInTheDocument();
  });

  it("keeps separate facilities in the selector when their coordinates match", () => {
    render(
      <BloodBankMap
        facilities={[
          { id: "bank-a", name: "Bank A", latitude: 24.8, longitude: 93.9 },
          { id: "bank-b", name: "Bank B", latitude: 24.8, longitude: 93.9 },
        ]}
      />,
    );

    expect(screen.getByRole("option", { name: "Bank A" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Bank B" })).toBeInTheDocument();
  });

  it("shows a facility search preview when coordinates and a starting point are both unavailable", () => {
    render(
      <BloodBankMap
        facilities={[
          {
            id: "unlocated-bank",
            name: "AGMC & GBP Hospital",
            address: "GB Bazar, P.O Kunjavan",
            city: "Agartala",
            district: "West Tripura",
            state: "Tripura",
          },
        ]}
      />,
    );

    const map = screen.getByTitle(
      "Google Maps search for AGMC & GBP Hospital",
    ) as HTMLIFrameElement;
    const params = new URL(map.src).searchParams;
    expect(params.get("q")).toContain("AGMC & GBP Hospital");
    expect(params.get("q")).toContain("GB Bazar, P.O Kunjavan");
    expect(params.get("q")).toContain("West Tripura");
    expect(screen.getByText(/not a confirmed pin/)).toBeInTheDocument();
    expect(
      screen.getByText("Unavailable—coordinates missing or invalid"),
    ).toBeInTheDocument();
    // Directions without an origin start from the device's own current location.
    const link = screen.getByRole("link", {
      name: "Open driving directions in Google Maps",
    });
    const href = new URL(link.getAttribute("href")!);
    expect(href.pathname).toBe("/maps/dir/");
    expect(href.searchParams.get("destination")).toContain("AGMC & GBP Hospital");
    expect(href.searchParams.has("origin")).toBe(false);
  });

  it("routes from the shared device location to a facility known only by name and address", () => {
    render(
      <BloodBankMap
        facilities={[
          {
            id: "unlocated-bank",
            name: "AGMC & GBP Hospital",
            address: "GB Bazar, P.O Kunjavan",
            city: "Agartala",
            district: "West Tripura",
            state: "Tripura",
          },
        ]}
        origin={{ latitude: 23.8357, longitude: 91.27 }}
      />,
    );

    const map = screen.getByTitle(
      "Google Maps route to AGMC & GBP Hospital",
    ) as HTMLIFrameElement;
    const params = new URL(map.src).searchParams;
    expect(params.get("saddr")).toBe("23.8357,91.27");
    expect(params.get("daddr")).toBe(
      "AGMC & GBP Hospital, GB Bazar, P.O Kunjavan, Agartala, West Tripura, Tripura, India",
    );
    expect(params.get("dirflg")).toBe("d");
    expect(screen.getByText("23.835700, 91.270000")).toBeInTheDocument();
    expect(
      screen.getByText(/Facility coordinates are not on file, so the route goes/),
    ).toBeInTheDocument();
    const href = new URL(
      screen
        .getByRole("link", { name: "Open driving directions in Google Maps" })
        .getAttribute("href")!,
    );
    expect(href.searchParams.get("origin")).toBe("23.8357,91.27");
  });

  it("routes from the selected district to a facility known only by name and address", () => {
    render(
      <BloodBankMap
        facilities={[
          { id: "b", name: "Blood Centre", address: "MG Road", city: "Imphal", state: "Manipur" },
        ]}
        originLabel="Imphal West, Manipur, India"
      />,
    );
    const params = new URL(
      (screen.getByTitle("Google Maps route to Blood Centre") as HTMLIFrameElement).src,
    ).searchParams;
    expect(params.get("saddr")).toBe("Imphal West, Manipur, India");
    expect(params.get("daddr")).toBe("Blood Centre, MG Road, Imphal, Manipur, India");
  });

  it("does not route to a town/district-centre coordinate; it uses the facility name and address", () => {
    render(
      <BloodBankMap
        facilities={[
          {
            id: "locality-bank",
            name: "District Blood Centre",
            address: "Hospital Road",
            city: "Imphal",
            state: "Manipur",
            latitude: 24.8,
            longitude: 93.94,
            location_precision: "LOCALITY",
          },
        ]}
        origin={{ latitude: 24.79, longitude: 93.89 }}
      />,
    );
    const params = new URL(
      (screen.getByTitle("Google Maps route to District Blood Centre") as HTMLIFrameElement).src,
    ).searchParams;
    expect(params.get("daddr")).toBe(
      "District Blood Centre, Hospital Road, Imphal, Manipur, India",
    );
    expect(params.get("daddr")).not.toBe("24.8,93.94");
    expect(screen.getByText(/only a town\/district centre/)).toBeInTheDocument();
  });
});
