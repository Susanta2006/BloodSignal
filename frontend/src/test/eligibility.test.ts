import { describe, expect, it } from "vitest";

import {
  hasPositiveReportedStock,
  hasReportedNonPositiveStock,
  isConfirmedCandidate,
  isReviewableCandidate,
} from "@/features/blood-search/eligibility";

const request = {
  blood_group: "O-",
  component: "whole_blood",
  quantity: 2,
  quantity_unit: "units",
};

const valid = {
  section: "A_ELIGIBLE",
  match_state: "MATCHED_REPORTED",
  evidence_state: "FRESH",
  verified_for_search: true,
  source_type: "live",
  blood_group_reported: "O-",
  component_reported: "whole_blood",
  quantity_reported: 5,
  quantity_unit_reported: "units",
};

describe("ranked candidate eligibility", () => {
  it("accepts only a verified, fresh, full match", () => {
    expect(isConfirmedCandidate(valid, request)).toBe(true);
  });

  it.each([
    ["zero stock", { quantity_reported: 0 }],
    ["negative stock", { quantity_reported: -1 }],
    ["unknown stock", { quantity_reported: null }],
    ["insufficient stock", { quantity_reported: 1 }],
    ["wrong blood group", { blood_group_reported: "O+" }],
    ["wrong component", { component_reported: "platelets" }],
    ["unknown freshness", { evidence_state: "UNKNOWN_FRESHNESS" }],
    ["stale section", { section: "B_UNCERTAIN" }],
    ["unverified backend result", { verified_for_search: false }],
    ["demo stock", { source_type: "demo" }],
    ["wrong unit", { quantity_unit_reported: "ml" }],
  ])("rejects %s from confirmed matches", (_name, patch) => {
    expect(isConfirmedCandidate({ ...valid, ...patch }, request)).toBe(false);
  });

  it("allows an exactly scoped uncertain live record only for call review", () => {
    const uncertain = {
      ...valid,
      section: "B_UNCERTAIN",
      evidence_state: "STALE",
      verified_for_search: false,
      quantity_reported: 1,
    };
    expect(isConfirmedCandidate(uncertain, request)).toBe(false);
    expect(isReviewableCandidate(uncertain, request)).toBe(true);
  });

  it("keeps positive partial stock available for ranking but hides zero or negative stock", () => {
    const partial = { ...valid, section: "C_INSUFFICIENT", quantity_reported: 1 };
    expect(hasPositiveReportedStock(partial, request)).toBe(true);
    expect(isConfirmedCandidate(partial, request)).toBe(false);
    expect(hasReportedNonPositiveStock({ ...valid, quantity_reported: 0 })).toBe(true);
    expect(hasReportedNonPositiveStock({ ...valid, quantity_reported: -1 })).toBe(true);
    expect(hasPositiveReportedStock({ ...valid, quantity_reported: 0 }, request)).toBe(false);
  });

  it("does not let mismatched, unknown, insufficient or demo records enter a call list", () => {
    expect(isReviewableCandidate({ ...valid, section: "D_MISMATCH" }, request)).toBe(false);
    expect(isReviewableCandidate({ ...valid, section: "E_UNKNOWN" }, request)).toBe(false);
    expect(isReviewableCandidate({ ...valid, section: "C_INSUFFICIENT" }, request)).toBe(false);
    expect(isReviewableCandidate({ ...valid, source_type: "demo" }, request)).toBe(false);
  });
});
