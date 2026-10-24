import { validLatLon, type LatLon } from "@/features/blood-search/distance";

/**
 * Device-native location service bridge.
 *
 * A web page can only reach the operating system's location service through the
 * browser. When BloodSignal runs inside a native shell (Capacitor), the shell's
 * Geolocation plugin talks to Android/iOS directly, so it still works when the
 * embedded browser refuses or fails to share a location. In a normal browser tab
 * none of this exists and every function here is a harmless no-op, so the
 * browser Geolocation flow in the page is unchanged.
 *
 * No npm dependency is added: the bridge is read from `window.Capacitor`, which
 * the native shell injects. (Cordova's geolocation plugin replaces
 * `navigator.geolocation` itself, so it is already covered by the browser flow.)
 */

type NativePosition = { coords?: { latitude?: number; longitude?: number } };

type CapacitorBridge = {
  isNativePlatform?: () => boolean;
  Plugins?: Record<string, unknown>;
  PluginHeaders?: Array<{ name?: string }>;
  nativePromise?: (
    plugin: string,
    method: string,
    options?: Record<string, unknown>,
  ) => Promise<unknown>;
};

type GeolocationPlugin = {
  checkPermissions?: () => Promise<Record<string, string>>;
  requestPermissions?: (options?: {
    permissions?: string[];
  }) => Promise<Record<string, string>>;
  getCurrentPosition: (
    options?: Record<string, unknown>,
  ) => Promise<NativePosition>;
};

function capacitor(): CapacitorBridge | null {
  if (typeof window === "undefined") return null;
  const bridge = (window as unknown as { Capacitor?: CapacitorBridge })
    .Capacitor;
  if (!bridge || typeof bridge.isNativePlatform !== "function") return null;
  try {
    return bridge.isNativePlatform() ? bridge : null;
  } catch {
    return null;
  }
}

function geolocationPlugin(): GeolocationPlugin | null {
  const bridge = capacitor();
  if (!bridge) return null;

  const registered = bridge.Plugins?.["Geolocation"] as
    GeolocationPlugin | undefined;
  if (registered && typeof registered.getCurrentPosition === "function") {
    return registered;
  }

  // The native shell lists installed plugins even when the web bundle never
  // imported them; call them through the bridge directly.
  const installed = bridge.PluginHeaders?.some(
    (header) => header?.name === "Geolocation",
  );
  const call = bridge.nativePromise;
  if (installed && typeof call === "function") {
    const invoke = (method: string, options?: Record<string, unknown>) =>
      call.call(bridge, "Geolocation", method, options);
    return {
      checkPermissions: () =>
        invoke("checkPermissions") as Promise<Record<string, string>>,
      requestPermissions: (options) =>
        invoke("requestPermissions", options) as Promise<
          Record<string, string>
        >,
      getCurrentPosition: (options) =>
        invoke("getCurrentPosition", options) as Promise<NativePosition>,
    };
  }
  return null;
}

/** True only inside a native shell that has its Geolocation plugin installed. */
export function isNativeLocationAvailable(): boolean {
  return geolocationPlugin() !== null;
}

function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T | null> {
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(null), ms);
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      () => {
        clearTimeout(timer);
        resolve(null);
      },
    );
  });
}

/**
 * Asks the device's native location service for the current position. Resolves
 * to validated coordinates, or null when the service is unavailable, permission
 * is refused, the request times out, or the coordinates are invalid. Never
 * throws.
 */
export async function getNativeLocation(
  timeoutMs = 25000,
): Promise<LatLon | null> {
  const plugin = geolocationPlugin();
  if (!plugin) return null;

  try {
    if (plugin.checkPermissions && plugin.requestPermissions) {
      const status = await withTimeout(plugin.checkPermissions(), 5000);
      const granted = (value?: string) => value === "granted";
      if (
        status &&
        !granted(status["location"]) &&
        !granted(status["coarseLocation"])
      ) {
        // The OS permission prompt waits for the user, so give it more time.
        const requested = await withTimeout(
          plugin.requestPermissions({ permissions: ["location"] }),
          timeoutMs,
        );
        if (
          !requested ||
          (!granted(requested["location"]) &&
            !granted(requested["coarseLocation"]))
        ) {
          return null;
        }
      }
    }

    const position = await withTimeout(
      plugin.getCurrentPosition({
        enableHighAccuracy: true,
        timeout: Math.max(5000, timeoutMs - 3000),
        maximumAge: 60000,
      }),
      timeoutMs,
    );
    const coordinates = {
      latitude: Number(position?.coords?.latitude),
      longitude: Number(position?.coords?.longitude),
    };
    return validLatLon(coordinates) ? coordinates : null;
  } catch {
    return null;
  }
}
