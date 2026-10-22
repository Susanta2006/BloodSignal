const MAX_PROXY_BODY_BYTES = 15_000_000;
const HOP_BY_HOP_HEADERS = new Set([
  "connection",
  "cookie",
  "content-length",
  "host",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
  "x-forwarded-for",
  "x-forwarded-proto",
  "cf-connecting-ip",
  "x-real-ip",
]);

export function backendTargetUrl(requestUrl: string, backendInternalUrl: string): URL {
  const incoming = new URL(requestUrl);
  if (!incoming.pathname.startsWith("/backend/")) throw new Error("Invalid backend proxy path");
  const backend = new URL(backendInternalUrl);
  if (
    backend.protocol !== "https:" &&
    backend.hostname !== "127.0.0.1" &&
    backend.hostname !== "localhost"
  )
    throw new Error("The backend URL must use HTTPS outside local development");
  const path = incoming.pathname.slice("/backend".length);
  if (path.split("/").some((segment) => segment === "." || segment === ".."))
    throw new Error("Invalid backend proxy path");
  return new URL(`${path}${incoming.search}`, backend.origin);
}

export async function proxyBackendRequest(
  request: Request,
  backendInternalUrl: string,
): Promise<Response> {
  const upstreamUrl = backendTargetUrl(request.url, backendInternalUrl);
  const declaredLength = Number(request.headers.get("content-length") || 0);
  if (declaredLength > MAX_PROXY_BODY_BYTES)
    return Response.json(
      { error: { code: "PAYLOAD_TOO_LARGE", message: "Request body too large." } },
      { status: 413, headers: { "Cache-Control": "no-store" } },
    );

  const headers = new Headers();
  request.headers.forEach((value, name) => {
    if (!HOP_BY_HOP_HEADERS.has(name.toLowerCase())) headers.set(name, value);
  });
  // CDN-provided client IP is forwarded only as a hint. FastAPI trusts it only when
  // TRUST_PROXY_HEADERS is enabled and direct backend access is restricted.
  const clientIp = request.headers.get("cf-connecting-ip") || request.headers.get("x-real-ip");
  if (clientIp) headers.set("X-Forwarded-For", clientIp);
  headers.set("X-Forwarded-Proto", new URL(request.url).protocol.slice(0, -1));

  let body: ArrayBuffer | undefined;
  if (request.method !== "GET" && request.method !== "HEAD") {
    body = await request.arrayBuffer();
    if (body.byteLength > MAX_PROXY_BODY_BYTES)
      return Response.json(
        { error: { code: "PAYLOAD_TOO_LARGE", message: "Request body too large." } },
        { status: 413, headers: { "Cache-Control": "no-store" } },
      );
  }

  try {
    const init: RequestInit = {
      method: request.method,
      headers,
      signal: AbortSignal.timeout(30_000),
    };
    if (body !== undefined) init.body = body;
    const upstream = await fetch(upstreamUrl, init);
    const responseHeaders = new Headers();
    upstream.headers.forEach((value, name) => {
      if (!HOP_BY_HOP_HEADERS.has(name.toLowerCase()) && name.toLowerCase() !== "set-cookie")
        responseHeaders.set(name, value);
    });
    return new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: responseHeaders,
    });
  } catch {
    return Response.json(
      {
        error: {
          code: "BACKEND_UNAVAILABLE",
          message: "The search service could not be reached. Try again shortly.",
          retryable: true,
        },
      },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }
}
