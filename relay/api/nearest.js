// Secured e-RaktKosh nearest-bloodbank relay. Fixed upstream URL; never an open proxy.
const { timingSafeEqual } = require("node:crypto");
const UPSTREAM = "https://eraktkosh.mohfw.gov.in/eraktkoshPortal/eraktkosh/bloodbank/nearest";

function safeEqual(a, b) {
  const x = Buffer.from(String(a || ""));
  const y = Buffer.from(String(b || ""));
  return x.length === y.length && x.length > 0 && timingSafeEqual(x, y);
}
function send(res, status, body, extra = {}) {
  res.statusCode = status;
  res.setHeader("Content-Type", "application/json");
  res.setHeader("Cache-Control", "no-store");
  res.setHeader("X-Relay-Region", process.env.VERCEL_REGION || "unknown");
  for (const [k, v] of Object.entries(extra)) res.setHeader(k, v);
  res.end(typeof body === "string" ? body : JSON.stringify(body));
}
module.exports = async function handler(req, res, fetchImpl = fetch) {
  if (req.method !== "GET") return send(res, 405, { error: "method_not_allowed" });
  const secret = process.env.RELAY_SECRET || "";
  if (secret.length < 16) return send(res, 500, { error: "relay_not_configured" });
  if (!safeEqual(req.headers["x-relay-secret"], secret)) return send(res, 401, { error: "unauthorized" });

  const url = new URL(req.url, "http://relay.local");
  const latitude = url.searchParams.get("latitude") || "";
  const longitude = url.searchParams.get("longitude") || "";
  const radius = url.searchParams.get("radius") || "";
  const lat = Number(latitude), lon = Number(longitude), r = Number(radius);
  if (!latitude || !longitude || !radius || !Number.isFinite(lat) || !Number.isFinite(lon) ||
      !Number.isFinite(r) || lat < -90 || lat > 90 || lon < -180 || lon > 180 ||
      !Number.isInteger(r) || r < 1 || r > 2000000) {
    return send(res, 400, { error: "invalid_params" });
  }
  const target = new URL(UPSTREAM);
  target.searchParams.set("latitude", String(lat));
  target.searchParams.set("longitude", String(lon));
  target.searchParams.set("radius", String(r));
  try {
    const upstream = await fetchImpl(target, {
      method: "GET", redirect: "manual",
      headers: { Accept: "application/json", "User-Agent": "BloodSignal/1.0 (read-only hackathon availability search)" },
      signal: AbortSignal.timeout(20000),
    });
    const text = await upstream.text();
    const retryAfter = upstream.headers.get("retry-after");
    return send(res, upstream.status, text, retryAfter ? { "Retry-After": retryAfter } : {});
  } catch (err) {
    const timeout = err && (err.name === "TimeoutError" || err.name === "AbortError");
    return send(res, 504, { error: timeout ? "upstream_timeout" : "upstream_unreachable" });
  }
};
