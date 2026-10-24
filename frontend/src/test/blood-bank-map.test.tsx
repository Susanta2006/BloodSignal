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
      screen.getByLabelText("Show facility and route on Google Maps"),
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
      screen.getByLabelText("Show facility and route on Google Maps"),
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
});
