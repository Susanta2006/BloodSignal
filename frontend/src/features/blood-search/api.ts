import { z } from "zod";

const identifier = z.string().trim().min(1);

export type RunSummary = {
  run_id: string;
  run_token?: string;
  status: string;
  tasks: Record<string, unknown>[];
  [key: string]: unknown;
};

export type Candidate = Record<string, unknown> & { candidate_id?: string; id?: string };

export type CallListItem = {
  candidate_id: string;
  bank_id: string;
  bank_name: string;
  phone: string | null;
  section: string;
  evidence_state: string | null;
  source_updated_at: string | null;
  display_note: string;
  address?: string | null;
  facility_source_type?: "demo" | "live";
  directory_source?: string | null;
  retrieved_at?: string | null;
  source_name?: string | null;
  source_type?: "demo" | "live" | "unknown";
  source_url?: string | null;
  blood_group?: string | null;
  component?: string | null;
  quantity?: number | null;
  quantity_unit?: string | null;
  distance_km?: number | null;
  distance_type?: string | null;
};

export type CallListProposal = {
  approval_id?: string;
  run_id?: string;
  proposal_version: string | number;
  proposal_hash: string;
  items: CallListItem[];
  [key: string]: unknown;
};

export class SearchApiError extends Error {
  readonly status: number;
  readonly fieldErrors: Record<string, string>;
  readonly correlationId: string | undefined;

  constructor(
    message: string,
    status: number,
    fieldErrors: Record<string, string> = {},
    correlationId?: string,
  ) {
    super(message);
    this.name = "SearchApiError";
    this.status = status;
    this.fieldErrors = fieldErrors;
    this.correlationId = correlationId;
  }
}

async function getResponseBody(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return {};
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return { message: text.slice(0, 500) };
  }
}

function responseError(body: unknown, status: number): SearchApiError {
  const record = asRecord(body);
  const errorRecord = asRecord(record["error"]);
  const detail = record["detail"];
  const fields: Record<string, string> = {};

  if (Array.isArray(detail)) {
    for (const issue of detail) {
      const item = asRecord(issue);
      const location = item["loc"];
      if (Array.isArray(location)) {
        const field = location.filter((part): part is string => typeof part === "string").at(-1);
        if (field && typeof item["msg"] === "string") fields[field] = item["msg"];
      }
    }
  }
  if (Array.isArray(errorRecord["fields"])) {
    for (const issue of errorRecord["fields"]) {
      const item = asRecord(issue);
      if (typeof item["field"] === "string" && typeof item["message"] === "string")
        fields[item["field"]] = item["message"];
    }
  }

  const message =
    (typeof errorRecord["message"] === "string" && errorRecord["message"]) ||
    (typeof record["message"] === "string" && record["message"]) ||
    (typeof detail === "string" && detail) ||
    (status === 404
      ? "Search service is not connected yet. No blood-bank or stock information was returned."
      : `Search could not be completed (request ${status}).`);

  return new SearchApiError(
    message,
    status,
    fields,
    typeof errorRecord["correlation_id"] === "string" ? errorRecord["correlation_id"] : undefined,
  );
}

const configuredApiBase =
  (import.meta.env["VITE_API_BASE_URL"] as string | undefined)
    ?.trim()
    .replace(/\/+$/, "") ?? "";

function apiUrl(path: string): string {
  if (configuredApiBase) {
    return `${configuredApiBase}${path}`;
  }

  // Route backend requests through Vite's existing /backend proxy.
  return `/backend${path}`;
}

const runTokenKey = (runId: string) => `bloodsignal:run-token:${runId}`;
const memoryRunTokens = new Map<string, string>();

function saveRunToken(runId: string, token: string): void {
  // A short-lived, per-tab capability token; never log it or send it to unrelated endpoints.
  memoryRunTokens.set(runId, token);
  try {
    window.sessionStorage.setItem(runTokenKey(runId), token);
  } catch {
    /* The in-memory token remains usable for this page. */
  }
}

function getRunToken(runId: string): string | null {
  const memoryToken = memoryRunTokens.get(runId);
  if (memoryToken) return memoryToken;
  try {
    const stored = window.sessionStorage.getItem(runTokenKey(runId));
    if (stored) memoryRunTokens.set(runId, stored);
    return stored;
  } catch {
    return null;
  }
}

async function request(path: string, init: RequestInit = {}, runId?: string): Promise<unknown> {
  let response: Response;
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (runId) {
    const token = getRunToken(runId);
    if (token) headers.set("X-Run-Token", token);
  }
  try {
    response = await fetch(apiUrl(path), {
      ...init,
      cache: "no-store",
      signal: init.signal ?? AbortSignal.timeout(30_000),
      headers,
    });
  } catch (error) {
    if (error instanceof Error && ["AbortError", "TimeoutError"].includes(error.name)) {
      throw new SearchApiError(
        "The search service did not respond. Try again when the connection is available.",
        0,
      );
    }
    throw new SearchApiError(
      "The search service could not be reached. No blood availability was verified.",
      0,
    );
  }

  const body = await getResponseBody(response);
  if (!response.ok) throw responseError(body, response.status);
  return body;
}

export async function getIdempotencyKey(): Promise<string> {
  const body = asRecord(await request("/api/idempotency-key"));
  const key = identifier.safeParse(body["idempotency_key"]);
  if (!key.success || key.data.length < 24) {
    throw new SearchApiError("The search service did not issue a valid request key.", 502);
  }
  return key.data;
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function firstDefined(...values: unknown[]): unknown {
  return values.find((value) => value !== undefined && value !== null);
}

/** First non-empty source among arrays/strings, flattened to unique trimmed strings. */
function stringList(...sources: unknown[]): string[] {
  const out: string[] = [];
  for (const source of sources) {
    const items = Array.isArray(source) ? source : [source];
    for (const item of items) {
      if (typeof item === "string" && item.trim() && !out.includes(item.trim())) out.push(item.trim());
    }
    if (out.length) break;
  }
  return out;
}

function facilityCoordinate(
  candidate: Record<string, unknown>,
  bank: Record<string, unknown>,
  axis: "latitude" | "longitude",
): unknown {
  const keys =
    axis === "latitude" ? ["latitude", "lat"] : ["longitude", "lng", "lon"];
  const candidateLocation = asRecord(candidate["location"]);
  const bankLocation = asRecord(bank["location"]);
  const candidateCoordinates = asRecord(candidate["coordinates"]);
  const bankCoordinates = asRecord(bank["coordinates"]);
  const candidateLocationCoordinates = asRecord(candidateLocation["coordinates"]);
  const bankLocationCoordinates = asRecord(bankLocation["coordinates"]);
  const records = [
    bank,
    bankLocation,
    bankCoordinates,
    bankLocationCoordinates,
    candidate,
    candidateLocation,
    candidateCoordinates,
    candidateLocationCoordinates,
  ];

  return firstDefined(...records.flatMap((record) => keys.map((key) => record[key])));
}

function parseRun(body: unknown, fallbackRunId?: string): RunSummary {
  const value = asRecord(body);
  const runId = identifier.safeParse(value["run_id"] ?? fallbackRunId);
  const status = identifier.safeParse(value["status"]);
  if (!runId.success || !status.success) {
    throw new SearchApiError("The search service returned an incomplete run status.", 502);
  }
  return {
    ...value,
    run_id: runId.data,
    status: status.data,
    tasks: Array.isArray(value["tasks"]) ? value["tasks"].map(asRecord) : [],
  };
}

export async function createRun(payload: unknown, idempotencyKey: string): Promise<RunSummary> {
  const response = await request("/api/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
    body: JSON.stringify(payload),
  });
  const run = parseRun(response);
  if (typeof run.run_token === "string" && run.run_token.length >= 16)
    saveRunToken(run.run_id, run.run_token);
  return run;
}

export async function getRun(runId: string): Promise<RunSummary> {
  const response = await request(`/api/runs/${encodeURIComponent(runId)}`, {}, runId);
  return parseRun(response, runId);
}

export async function getCandidates(runId: string): Promise<Candidate[]> {
  const response = await request(`/api/runs/${encodeURIComponent(runId)}/candidates`, {}, runId);
  const value = asRecord(response);
  if (!Array.isArray(value["candidates"])) {
    throw new SearchApiError("The search service response did not include a candidate list.", 502);
  }
  return value["candidates"].map((raw) => {
    const candidate = asRecord(raw);
    const bank = asRecord(candidate["bank"]);
    const sourceName = candidate["source_display_name"];
    const stock = {
      evidence_state: candidate["evidence_state"],
      source_updated_at: candidate["source_updated_at"],
      retrieved_at: candidate["retrieved_at"],
      source_name: sourceName,
      source_type: candidate["source_type"],
      source_label: candidate["source_label"],
      source_url: candidate["official_source_url"],
      blood_group: candidate["blood_group_reported"],
      component: candidate["component_reported"],
      quantity: candidate["quantity_reported"],
      quantity_unit: candidate["quantity_unit_reported"],
      status: candidate["match_state"],
    };

    // The backend's typed Candidate keeps facility details under `bank` and the
    // evidence fields on the candidate. Flatten those into the existing result-card contract.
    return {
      ...candidate,
      bank_id: bank["bank_id"],
      name: firstDefined(bank["name"], candidate["bank_name"], candidate["name"]),
      address: firstDefined(bank["address"], candidate["address"], candidate["formatted_address"]),
      city: firstDefined(bank["city"], candidate["city"]),
      district: firstDefined(bank["district"], candidate["district"]),
      state: firstDefined(bank["state"], candidate["state"]),
      facility_source_type: bank["source_type"],
      phone: firstDefined(bank["phone"], candidate["phone"]),
      emails: stringList(bank["emails"], bank["email"], candidate["emails"], candidate["email"]),
      faxes: stringList(bank["faxes"], bank["fax"], candidate["faxes"], candidate["fax"]),
      email: stringList(bank["emails"], bank["email"], candidate["emails"], candidate["email"])[0],
      fax: stringList(bank["faxes"], bank["fax"], candidate["faxes"], candidate["fax"])[0],
      facility_type: firstDefined(bank["facility_type"], bank["type"], candidate["facility_type"]),
      latitude: facilityCoordinate(candidate, bank, "latitude"),
      longitude: facilityCoordinate(candidate, bank, "longitude"),
      location_precision: firstDefined(bank["location_precision"], candidate["location_precision"]),
      location_note: firstDefined(bank["location_note"], candidate["location_note"]),
      directory_source: bank["directory_source"],
      directory_reference: bank["directory_reference"],
      directory_retrieved_at: bank["directory_retrieved_at"],
      opening_hours: Array.isArray(bank["opening_hours"])
        ? bank["opening_hours"]
            .filter((item): item is string => typeof item === "string")
            .join("; ")
        : bank["opening_hours"],
      driving_distance:
        candidate["route_mode"] === "DRIVE" &&
        typeof candidate["route_meters"] === "number" &&
        Number.isFinite(candidate["route_meters"])
          ? `${(candidate["route_meters"] / 1000).toFixed(1)} km`
          : undefined,
      source_name: sourceName,
      source: candidate["source_id"],
      stock,
    };
  });
}

export async function createProposal(
  runId: string,
  candidateIds: string[],
  idempotencyKey: string,
): Promise<CallListProposal> {
  const response = await request(
    `/api/runs/${encodeURIComponent(runId)}/proposal`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
      body: JSON.stringify({ candidate_ids: candidateIds }),
    },
    runId,
  );

  const value = asRecord(response);
  const version = value["proposal_version"];
  const hash = identifier.safeParse(value["proposal_hash"]);
  if ((typeof version !== "string" && typeof version !== "number") || !hash.success) {
    throw new SearchApiError("The service did not return a versioned call-list proposal.", 502);
  }
  return {
    ...value,
    ...(typeof value["approval_id"] === "string" ? { approval_id: value["approval_id"] } : {}),
    ...(typeof value["run_id"] === "string" ? { run_id: value["run_id"] } : {}),
    proposal_version: version,
    proposal_hash: hash.data,
    items: Array.isArray(value["items"])
      ? value["items"].map((raw) => {
          const item = asRecord(raw);
          return {
            ...item,
            candidate_id: typeof item["candidate_id"] === "string" ? item["candidate_id"] : "",
            bank_id: typeof item["bank_id"] === "string" ? item["bank_id"] : "",
            bank_name: typeof item["bank_name"] === "string" ? item["bank_name"] : "Facility",
            phone: typeof item["phone"] === "string" ? item["phone"] : null,
            section: typeof item["section"] === "string" ? item["section"] : "UNKNOWN",
            evidence_state:
              typeof item["evidence_state"] === "string" ? item["evidence_state"] : null,
            source_updated_at:
              typeof item["source_updated_at"] === "string" ? item["source_updated_at"] : null,
            display_note: typeof item["display_note"] === "string" ? item["display_note"] : "",
          } satisfies CallListItem;
        })
      : [],
  };
}

export async function approveProposal(
  proposal: CallListProposal,
  idempotencyKey: string,
): Promise<Record<string, unknown>> {
  if (!proposal.approval_id) {
    throw new SearchApiError(
      "Approval is unavailable because the service did not return an approval ID.",
      409,
    );
  }
  const runId = typeof proposal["run_id"] === "string" ? proposal["run_id"] : "";
  if (!runId)
    throw new SearchApiError("Approval response did not include its run identifier.", 502);
  const response = await request(
    `/api/approvals/${encodeURIComponent(proposal.approval_id)}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
      body: JSON.stringify({
        proposal_version: proposal.proposal_version,
        proposal_hash: proposal.proposal_hash,
      }),
    },
    runId,
  );
  return asRecord(response);
}

export function asCandidate(value: unknown): Candidate {
  return asRecord(value);
}

export function asValue(value: unknown): Record<string, unknown> {
  return asRecord(value);
}
