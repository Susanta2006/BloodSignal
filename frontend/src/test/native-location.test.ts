import { afterEach, describe, expect, it, vi } from "vitest";

import {
  getNativeLocation,
  isNativeLocationAvailable,
} from "@/features/blood-search/native-location";

type TestWindow = { Capacitor?: unknown };
const w = window as unknown as TestWindow;

afterEach(() => {
  delete w.Capacitor;
});

describe("native device location bridge", () => {
  it("is unavailable and returns null in a normal browser", async () => {
    expect(isNativeLocationAvailable()).toBe(false);
    expect(await getNativeLocation()).toBeNull();
  });

  it("is unavailable when Capacitor runs as a web app", () => {
    w.Capacitor = {
      isNativePlatform: () => false,
      Plugins: { Geolocation: { getCurrentPosition: vi.fn() } },
    };
    expect(isNativeLocationAvailable()).toBe(false);
  });

  it("reads a position from the registered native Geolocation plugin", async () => {
    w.Capacitor = {
      isNativePlatform: () => true,
      Plugins: {
        Geolocation: {
          checkPermissions: async () => ({ location: "granted" }),
          requestPermissions: vi.fn(),
          getCurrentPosition: async () => ({
            coords: { latitude: 24.8, longitude: 93.94 },
          }),
        },
      },
    };
    expect(isNativeLocationAvailable()).toBe(true);
    expect(await getNativeLocation()).toEqual({
      latitude: 24.8,
      longitude: 93.94,
    });
  });

  it("requests OS permission and returns null when it is refused", async () => {
    const getCurrentPosition = vi.fn();
    w.Capacitor = {
      isNativePlatform: () => true,
      Plugins: {
        Geolocation: {
          checkPermissions: async () => ({ location: "prompt" }),
          requestPermissions: async () => ({ location: "denied" }),
          getCurrentPosition,
        },
      },
    };
    expect(await getNativeLocation()).toBeNull();
    expect(getCurrentPosition).not.toHaveBeenCalled();
  });

  it("falls back to the native bridge when the plugin was not imported by the web bundle", async () => {
    const nativePromise = vi.fn(async (_plugin: string, method: string) =>
      method === "checkPermissions"
        ? { location: "granted" }
        : { coords: { latitude: 24.81, longitude: 93.91 } },
    );
    w.Capacitor = {
      isNativePlatform: () => true,
      PluginHeaders: [{ name: "Geolocation" }],
      nativePromise,
    };
    expect(isNativeLocationAvailable()).toBe(true);
    expect(await getNativeLocation()).toEqual({
      latitude: 24.81,
      longitude: 93.91,
    });
  });

  it("rejects invalid native coordinates and swallows plugin errors", async () => {
    w.Capacitor = {
      isNativePlatform: () => true,
      Plugins: {
        Geolocation: {
          getCurrentPosition: async () => ({
            coords: { latitude: 999, longitude: 0 },
          }),
        },
      },
    };
    expect(await getNativeLocation()).toBeNull();
    w.Capacitor = {
      isNativePlatform: () => true,
      Plugins: {
        Geolocation: {
          getCurrentPosition: async () => {
            throw new Error("boom");
          },
        },
      },
    };
    expect(await getNativeLocation()).toBeNull();
  });
});
