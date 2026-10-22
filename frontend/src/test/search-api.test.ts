import { afterEach, describe, expect, it, vi } from "vitest";

import { createProposal, getCandidates, getIdempotencyKey } from "@/features/blood-search/api";

afterEach(() => vi.unstubAllGlobals());

describe("search API integration", () => {
  it("maps the backend candidate contract into the existing result card fields", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      Response.json({
        candidates: [
          {
            candidate_id: "candidate-1",
            section: "A_ELIGIBLE",
            match_state: "MATCHED_REPORTED",
            evidence_state: "FRESH",
            source_display_name: "Authorized stock feed",
            source_updated_at: "2026-10-20T08:00:00Z",
            retrieved_at: "2026-10-20T08:05:00Z",
            blood_group_reported: "O-",
            component_reported: "whole_blood",
            quantity_reported: 3,
            quantity_unit_reported: "units",
            route_mode: "DRIVE",
            route_meters: 2500,
            bank: {
              bank_id: "bank-1",
              name: "City Blood Centre",
              city: "Imphal",
              district: "Imphal West",
              state: "Manipur",
              address: "Main Road",
              phone: "+91 12345 67890",
              latitude: 24.8,
              longitude: 93.9,
              directory_source: "data.gov.in Blood Bank Directory",
            },
          },
        ],
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const [candidate] = await getCandidates("run-1");

    expect(candidate).toMatchObject({
      candidate_id: "candidate-1",
      name: "City Blood Centre",
      city: "Imphal",
      district: "Imphal West",
      state: "Manipur",
      address: "Main Road",
      phone: "+91 12345 67890",
      latitude: 24.8,
      longitude: 93.9,
      directory_source: "data.gov.in Blood Bank Directory",
      driving_distance: "2.5 km",
    });
    expect(candidate?.["stock"]).toMatchObject({
      blood_group: "O-",
      component: "whole_blood",
      quantity: 3,
      quantity_unit: "units",
      source_name: "Authorized stock feed",
    });
    expect(fetchMock.mock.calls[0]?.[0]).toContain("/backend/api/runs/run-1/candidates");
  });

  it("requests an idempotency key from the backend instead of the browser crypto API", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(Response.json({ idempotency_key: "backend-issued-random-key-123456" }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getIdempotencyKey()).resolves.toBe("backend-issued-random-key-123456");
    expect(fetchMock.mock.calls[0]?.[0]).toContain("/backend/api/idempotency-key");
  });

  it("preserves phone and source update time in the human-review call-list proposal", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      Response.json({
        approval_id: "approval-1",
        run_id: "run-1",
        proposal_version: 1,
        proposal_hash: "a".repeat(64),
        items: [
          {
            candidate_id: "candidate-1",
            bank_id: "bank-1",
            bank_name: "City Blood Centre",
            phone: "+91 1234567890",
            section: "A_ELIGIBLE",
            evidence_state: "FRESH",
            source_updated_at: "2026-10-20T08:00:00Z",
            display_note: "Call to reconfirm before travelling.",
          },
        ],
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const proposal = await createProposal("run-1", ["candidate-1"], "backend-key-1234567890");

    expect(proposal.items).toEqual([
      {
        candidate_id: "candidate-1",
        bank_id: "bank-1",
        bank_name: "City Blood Centre",
        phone: "+91 1234567890",
        section: "A_ELIGIBLE",
        evidence_state: "FRESH",
        source_updated_at: "2026-10-20T08:00:00Z",
        display_note: "Call to reconfirm before travelling.",
      },
    ]);
    expect(fetchMock.mock.calls[0]?.[0]).toContain("/backend/api/runs/run-1/proposal");
  });

  it("does not present a distance as driving distance without a driving route mode", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      Response.json({
        candidates: [
          {
            candidate_id: "candidate-without-route-mode",
            route_meters: 2500,
            bank: { bank_id: "bank-1", name: "City Blood Centre" },
          },
        ],
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const [candidate] = await getCandidates("run-1");

    expect(candidate?.["driving_distance"]).toBeUndefined();
  });
});
