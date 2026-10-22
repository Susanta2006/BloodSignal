import { describe, expect, it } from "vitest";

import { eraktkoshStates } from "@/features/blood-search/eraktkosh-catalog";
import { districtsByState, nearbyDistrictSuggestions } from "@/features/blood-search/district-catalog";

describe("state and district selector catalogs", () => {
  it("provides a district selector entry for every e-RaktKosh state", () => {
    for (const state of eraktkoshStates) {
      expect(districtsByState[state.displayName]?.length, state.displayName).toBeGreaterThan(0);
    }
  });

  it("offers nearby district suggestions without claiming availability", () => {
    expect(nearbyDistrictSuggestions("Imphal West")).toContain("Imphal East");
    expect(nearbyDistrictSuggestions("not in catalog")).toEqual([]);
  });
});
