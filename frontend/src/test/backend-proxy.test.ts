import { afterEach, describe, expect, it, vi } from "vitest";
import { backendTargetUrl, proxyBackendRequest } from "@/lib/backend-proxy";

afterEach(() => vi.unstubAllGlobals());

describe("same-origin backend proxy target", () => {
  it("removes the frontend proxy prefix and preserves query parameters", () => {
    const target = backendTargetUrl(
      "https://bloodsignal.test/backend/api/runs?limit=3",
      "https://api.bloodsignal.test/",
    );
    expect(target.href).toBe("https://api.bloodsignal.test/api/runs?limit=3");
  });

  it("rejects insecure remote backend addresses", () => {
    expect(() => backendTargetUrl("https://site.test/backend/api/runs", "http://api.test")).toThrow(
      "The backend URL must use HTTPS outside local development",
    );
  });

  it("does not normalize a traversal path into a backend endpoint", () => {
    expect(() =>
      backendTargetUrl("https://site.test/backend/../api/runs", "https://api.test"),
    ).toThrow("Invalid backend proxy path");
  });

  it("forwards scoped tokens but strips cookies and spoofed proxy headers", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      const headers = new Headers(init?.headers);
      expect(headers.get("x-run-token")).toBe("scoped-token");
      expect(headers.has("cookie")).toBe(false);
      expect(headers.get("x-forwarded-for")).toBe("203.0.113.8");
      expect(headers.get("x-forwarded-proto")).toBe("https");
      return new Response("{}", { status: 202 });
    });
    vi.stubGlobal("fetch", fetchMock);
    const request = new Request("https://site.test/backend/api/runs", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Run-Token": "scoped-token",
        Cookie: "session=private",
        "X-Forwarded-For": "127.0.0.1",
        "CF-Connecting-IP": "203.0.113.8",
      },
      body: "{}",
    });
    const response = await proxyBackendRequest(request, "https://api.test");
    expect(response.status).toBe(202);
  });
});
