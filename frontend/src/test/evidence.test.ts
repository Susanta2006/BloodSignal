import { describe, expect, it } from "vitest";

import { evidenceState } from "@/features/blood-search/evidence";

const base = {
  source_name: "District blood bank portal",
  source_updated_at: "2026-10-20T08:00:00Z",
};

describe("evidence labels", () => {
  it("labels conflicting stock", () => {
    expect(evidenceState({ ...base, stock: { evidence_state: "conflicting" } }).title).toBe(
      "Conflicting source data",
    );
  });
  it("labels stale stock", () => {
    expect(evidenceState({ ...base, is_stale: true, stock: { status: "available" } }).title).toBe(
      "Stale source data",
    );
  });
  it("shows current stock as unknown when no inventory source is identified", () => {
    expect(
      evidenceState({ source_updated_at: base.source_updated_at, stock: { status: "available" } })
        .title,
    ).toBe("Current stock unknown");
  });
  it("never shows a match without a source update time", () => {
    expect(
      evidenceState({ source_name: base.source_name, stock: { status: "available" } }).title,
    ).toBe("Freshness unknown");
  });
  it("shows a match only with source and update time", () => {
    expect(evidenceState({ ...base, stock: { status: "available" } }).title).toBe("Source match");
  });
  it("labels synthetic inventory separately from a verified live match", () => {
    const result = evidenceState({
      ...base,
      source_type: "demo",
      stock: { status: "available" },
    });
    expect(result.title).toBe("Simulated demo inventory");
    expect(result.warning).toContain("not verified hospital inventory");
  });
  it("does not treat an insufficient report as a source match", () => {
    expect(
      evidenceState({ ...base, section: "C_INSUFFICIENT", stock: { evidence_state: "FRESH" } })
        .title,
    ).toBe("Reported quantity is insufficient");
  });
  it("does not treat a missing inventory record as zero stock", () => {
    const result = evidenceState({ directory_source: "data.gov.in", section: "E_UNKNOWN" });
    expect(result.title).toBe("Current stock unknown");
    expect(result.warning).toContain("no current inventory evidence");
  });
});
