import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { BloodBankMap, type FacilityMapItem } from "@/features/blood-search/BloodBankMap";

const facilities: FacilityMapItem[] = [
  { id: "bank-a", name: "Bank A", latitude: 24.8, longitude: 93.9 },
  { id: "bank-b", name: "Bank B", latitude: 24.81, longitude: 93.91 },
];

describe("blood bank map selection", () => {
  it("updates the route immediately when the selected facility changes", () => {
    render(
      <BloodBankMap
        facilities={facilities}
        origin={{ latitude: 24.79, longitude: 93.89 }}
      />,
    );

    const map = screen.getByTitle("Google Maps route to Bank A") as HTMLIFrameElement;
    expect(new URL(map.src).searchParams.get("daddr")).toBe("24.8,93.9");

    fireEvent.change(screen.getByLabelText("Show facility and route on Google Maps"), {
      target: { value: "bank-b" },
    });

    expect(screen.getByTitle("Google Maps route to Bank B")).toBeInTheDocument();
    const nextMap = screen.getByTitle("Google Maps route to Bank B") as HTMLIFrameElement;
    expect(new URL(nextMap.src).searchParams.get("daddr")).toBe("24.81,93.91");
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
