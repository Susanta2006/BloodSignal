type Tone = "success" | "warning" | "danger" | "neutral";

export type EvidenceStatus = { title: string; tone: string; warning?: string };

const tones: Record<Tone, string> = {
  success: "border-success/30 bg-success/10 text-success",
  warning: "border-warning/40 bg-warning/10 text-warning",
  danger: "border-destructive/30 bg-destructive/10 text-destructive",
  neutral: "border-border bg-muted text-muted-foreground",
};

function record(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function text(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

/** Deterministic label for a result, using only fields the search service returned. */
export function evidenceState(candidate: Record<string, unknown>): EvidenceStatus {
  const stock = record(candidate["stock"] ?? candidate["availability"]);
  const sourceType = candidate["source_type"] ?? stock["source_type"];
  if (sourceType === "demo")
    return {
      title: "Simulated demo inventory",
      tone: tones.warning,
      warning: "Simulated demo data — not verified hospital inventory.",
    };
  const state = text(
    stock["evidence_state"] ??
      stock["status"] ??
      candidate["evidence_state"] ??
      candidate["availability_status"],
  );
  const normalized = state?.toLowerCase().replaceAll(" ", "_") ?? "";
  const update = text(stock["source_updated_at"] ?? candidate["source_updated_at"]);
  const source = text(candidate["source_name"] ?? candidate["source"] ?? stock["source_name"]);
  const conflicting =
    stock["is_conflicting"] === true ||
    candidate["is_conflicting"] === true ||
    ["conflicting", "conflict", "conflicting_reports"].includes(normalized);
  const stale =
    stock["is_stale"] === true ||
    candidate["is_stale"] === true ||
    ["stale", "outdated"].includes(normalized);

  if (conflicting)
    return {
      title: "Conflicting source data",
      tone: tones.warning,
      warning:
        "Sources disagree about this stock. Call the blood bank to confirm before travelling.",
    };
  if (stale)
    return {
      title: "Stale source data",
      tone: tones.warning,
      warning: "This stock report is old and may no longer be accurate. Call to confirm.",
    };
  if (["unavailable", "source_unavailable"].includes(normalized))
    return {
      title: "Source unavailable",
      tone: tones.neutral,
      warning: "The stock source could not be reached. No availability is confirmed.",
    };
  if (candidate["section"] === "C_INSUFFICIENT" || candidate["match_state"] === "INSUFFICIENT")
    return {
      title: "Reported quantity is insufficient",
      tone: tones.warning,
      warning: "The source reports less than requested. Call to confirm current availability.",
    };
  if (candidate["section"] === "D_MISMATCH" || candidate["match_state"] === "NOT_MATCHED")
    return {
      title: "Request does not match source record",
      tone: tones.danger,
      warning: "The reported blood group or component does not match this request.",
    };
  if (!source)
    return {
      title: "Current stock unknown",
      tone: tones.neutral,
      warning:
        "The facility is listed, but no current inventory evidence was returned. Call to confirm.",
    };
  if (!update || ["unknown", "unknown_freshness", "freshness_unknown"].includes(normalized))
    return {
      title: "Freshness unknown",
      tone: tones.neutral,
      warning: "The source did not say when this stock was updated. Call to confirm.",
    };
  if (["insufficient_quantity", "quantity_insufficient"].includes(normalized))
    return { title: "Quantity may be insufficient", tone: tones.warning };
  if (["component_mismatch", "mismatched_component"].includes(normalized))
    return { title: "Component mismatch", tone: tones.danger };
  if (["fresh", "verified", "available", "match", "matched"].includes(normalized))
    return { title: "Source match", tone: tones.success };
  return { title: "Availability not verified", tone: tones.neutral };
}
