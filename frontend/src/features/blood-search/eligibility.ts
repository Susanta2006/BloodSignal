/** Defense-in-depth checks for the confirmed-match section and human-review workflow. */

export type EligibilityRequest = {
  blood_group: string;
  component: string;
  quantity: number;
  quantity_unit: string;
};

export type CandidateRecord = Record<string, unknown>;

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function number(value: unknown): number | null {
  if (typeof value !== "number" && typeof value !== "string") return null;
  if (typeof value === "string" && !value.trim()) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function componentKey(value: unknown): string {
  return text(value).toLowerCase().replace(/[\s-]+/g, "_");
}

function groupKey(value: unknown): string {
  return text(value).toUpperCase().replace(/\s+/g, "");
}

function sameRequestFields(candidate: CandidateRecord, request: EligibilityRequest): boolean {
  const group = candidate["blood_group_reported"] ?? candidate["blood_group"];
  const component = candidate["component_reported"] ?? candidate["component"];
  return (
    groupKey(group) === groupKey(request.blood_group) &&
    componentKey(component) === componentKey(request.component)
  );
}

/** True only when the backend and every displayed stock field support a current full match. */
export function isConfirmedCandidate(
  candidate: CandidateRecord,
  request: EligibilityRequest | null,
): boolean {
  if (!request) return false;
  if (
    candidate["section"] !== "A_ELIGIBLE" ||
    candidate["match_state"] !== "MATCHED_REPORTED" ||
    candidate["evidence_state"] !== "FRESH" ||
    candidate["verified_for_search"] !== true ||
    candidate["source_type"] !== "live"
  ) {
    return false;
  }
  if (!sameRequestFields(candidate, request)) return false;

  const quantity = number(candidate["quantity_reported"]);
  const unit = text(candidate["quantity_unit_reported"]).toLowerCase();
  const required = number(request.quantity);
  const requestedUnit = text(request.quantity_unit).toLowerCase();
  return (
    quantity !== null &&
    required !== null &&
    required > 0 &&
    quantity > 0 &&
    quantity >= required &&
    unit !== "" &&
    unit === requestedUnit
  );
}

/** Only eligible or correctly scoped uncertain live stock can enter a human-review call list. */
export function isReviewableCandidate(
  candidate: CandidateRecord,
  request: EligibilityRequest | null,
): boolean {
  if (!request || candidate["source_type"] !== "live") return false;
  if (candidate["section"] !== "A_ELIGIBLE" && candidate["section"] !== "B_UNCERTAIN")
    return false;
  return sameRequestFields(candidate, request);
}
