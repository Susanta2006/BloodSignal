import { readFile, unlink, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { loadEnv } from "vite";

const configured = (
  process.env.VITE_SITE_URL || loadEnv("production", process.cwd(), "").VITE_SITE_URL
)?.trim();
const output = resolve(
  process.env.NITRO_PRESET === "vercel" ? ".vercel/output/static" : ".output/public",
);
try {
  const headersPath = resolve(output, "_headers");
  let headers = await readFile(headersPath, "utf8");
  if (!/^\/\*$/m.test(headers)) {
    headers =
      `${headers.trimEnd()}\n\n/*\n` +
      `  X-Content-Type-Options: nosniff\n` +
      `  X-Frame-Options: DENY\n` +
      `  Referrer-Policy: strict-origin-when-cross-origin\n` +
      `  Permissions-Policy: camera=(), microphone=(self), geolocation=(self)\n` +
      `  Cross-Origin-Opener-Policy: same-origin\n` +
      `  Strict-Transport-Security: max-age=31536000; includeSubDomains\n`;
    await writeFile(headersPath, headers, "utf8");
  }
} catch (error) {
  if (error.code !== "ENOENT") throw error;
}
if (!configured) {
  try {
    await unlink(resolve(output, "sitemap.xml"));
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
  try {
    const robotsPath = resolve(output, "robots.txt");
    const robots = await readFile(robotsPath, "utf8");
    await writeFile(robotsPath, robots.replace(/^Sitemap: .*\n?/gm, ""), "utf8");
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
  console.info(
    "VITE_SITE_URL is empty; skipping sitemap generation. Set it to the deployed HTTPS origin before production builds.",
  );
  process.exit(0);
}

const site = new URL(configured);
if (site.protocol !== "https:" || site.pathname !== "/" || site.search || site.hash) {
  throw new Error(
    "VITE_SITE_URL must be the HTTPS site origin, for example https://bloodsignal.example",
  );
}

const paths = ["/", "/about", "/find", "/developers", "/privacy", "/terms", "/cookies"];
const xml =
  `<?xml version="1.0" encoding="UTF-8"?>\n` +
  `<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n` +
  paths.map((path) => `  <url><loc>${site.origin}${path}</loc></url>`).join("\n") +
  `\n</urlset>\n`;
await writeFile(resolve(output, "sitemap.xml"), xml, "utf8");
const robotsPath = resolve(output, "robots.txt");
const robots = await readFile(robotsPath, "utf8");
const sitemapLine = `Sitemap: ${site.origin}/sitemap.xml`;
const updatedRobots = robots.match(/^Sitemap: .*$/m)
  ? robots.replace(/^Sitemap: .*$/m, sitemapLine)
  : `${robots.trimEnd()}\n\n${sitemapLine}\n`;
await writeFile(robotsPath, updatedRobots, "utf8");
console.info(`Generated sitemap for ${site.origin}`);
