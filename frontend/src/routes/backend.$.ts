import { createFileRoute } from "@tanstack/react-router";
import { proxyBackendRequest } from "@/lib/backend-proxy";

async function forward(request: Request): Promise<Response> {
  const backendUrl = process.env["BACKEND_INTERNAL_URL"] || "http://127.0.0.1:8000";
  try {
    return await proxyBackendRequest(request, backendUrl);
  } catch {
    return Response.json(
      {
        error: {
          code: "BACKEND_PROXY_MISCONFIGURED",
          message: "The search service is not configured correctly.",
          retryable: false,
        },
      },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }
}

export const Route = createFileRoute("/backend/$")({
  server: {
    handlers: {
      GET: ({ request }) => forward(request),
      POST: ({ request }) => forward(request),
    },
  },
});
