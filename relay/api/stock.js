// BloodSignal e-RaktKosh relay — runs as a Vercel Function pinned to Mumbai (bom1).
// Makes the SAME fixed request the backend would make, from an Indian region.
// Not an open proxy: fixed host/path, numeric params only, shared-secret required.
const { timingSafeEqual } = require("node:crypto");

const UPSTREAM = "https://eraktkosh.mohfw.gov.in/eraktkoshPortal/eraktkosh/blood-availability";

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
  const stateCode = url.searchParams.get("stateCode") || "";
  const componentId = url.searchParams.get("componentId") || "";
  if (!/^\d{1,3}$/.test(stateCode) || !/^\d{1,3}$/.test(componentId))
    return send(res, 400, { error: "invalid_params" });

  const target = new URL(UPSTREAM);
  target.searchParams.set("stateCode", stateCode);
  target.searchParams.set("componentId", componentId);
  try {
    const upstream = await fetchImpl(target, {
      method: "GET",
      redirect: "manual",
      headers: {
        Accept: "application/json",
        "User-Agent": "BloodSignal/1.0 (read-only hackathon availability search)",
      },
      signal: AbortSignal.timeout(20_000),
    });
    const text = await upstream.text();
    const retryAfter = upstream.headers.get("retry-after");
    // Pass the upstream status through so the backend classifies 401/403/429/5xx exactly as before.
    return send(res, upstream.status, text, retryAfter ? { "Retry-After": retryAfter } : {});
  } catch (err) {
    const timeout = err && (err.name === "TimeoutError" || err.name === "AbortError");
    return send(res, 504, { error: timeout ? "upstream_timeout" : "upstream_unreachable" });
  }
};
