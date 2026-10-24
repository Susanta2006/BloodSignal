/**
 * Nominatim proxy: forwards geocoding requests to OSM to bypass Render IP blocks.
 * Requires X-Relay-Secret header matching RELAY_SECRET env var.
 */

export default async function handler(req, res) {
  const secret = process.env.RELAY_SECRET || "";
  const headerSecret = req.headers["x-relay-secret"] || "";

  if (!secret || headerSecret !== secret) {
    return res.status(403).json({ error: "Unauthorized" });
  }

  if (req.method !== "GET") {
    return res.status(405).json({ error: "Method not allowed" });
  }

  const params = req.query;
  const nominatimUrl = new URL("https://nominatim.openstreetmap.org/search");
  
  Object.entries(params).forEach(([key, value]) => {
    nominatimUrl.searchParams.append(key, value);
  });

  try {
    const response = await fetch(nominatimUrl.toString(), {
      headers: {
        "Accept": "application/json",
        "User-Agent": "BloodSignal/1.0 (relay via Vercel)",
      },
    });

    const data = await response.json();
    res.setHeader("Cache-Control", "public, max-age=86400"); // 24h cache
    res.status(200).json(data);
  } catch (error) {
    console.error("Nominatim proxy error:", error);
    res.status(502).json({ error: "Upstream error" });
  }
}
