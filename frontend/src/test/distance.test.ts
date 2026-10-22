import { describe, expect, it } from "vitest";
import { haversineDistanceKm, validLatLon } from "@/features/blood-search/distance";

describe("straight-line distance calculations", () => {
  it("calculates a known short distance in kilometres", () => {
    expect(haversineDistanceKm(
      { latitude: 24.800, longitude: 93.900 },
      { latitude: 24.810, longitude: 93.900 },
    )).toBeCloseTo(1.112, 2);
  });

  it("returns zero for identical coordinates", () => {
    expect(haversineDistanceKm({ latitude: 0, longitude: 0 }, { latitude: 0, longitude: 0 })).toBe(0);
  });

  it("handles antimeridian coordinates and clamps the haversine term", () => {
    expect(haversineDistanceKm({ latitude: 0, longitude: 179.9 }, { latitude: 0, longitude: -179.9 })).toBeCloseTo(22.239, 2);
  });

  it("rejects missing, non-finite, and out-of-range coordinates", () => {
    expect(validLatLon({ latitude: 91, longitude: 0 })).toBe(false);
    expect(haversineDistanceKm(null, { latitude: 0, longitude: 0 })).toBeNull();
    expect(haversineDistanceKm({ latitude: 0, longitude: Number.NaN }, { latitude: 0, longitude: 0 })).toBeNull();
  });
});
