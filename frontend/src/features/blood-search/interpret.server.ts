import { z } from "zod";

const fieldsSchema = z.object({
  blood_group: z
    .enum(["O-", "O+", "A-", "A+", "B-", "B+", "AB-", "AB+"])
    .nullable(),
  component: z
    .enum([
      "cryo_poor_plasma",
      "cryoprecipitate",
      "fresh_frozen_plasma",
      "irradiated_rbc",
      "leukoreduced_rbc",
      "prbc",
      "plasma",
      "platelet_concentrate",
      "platelet_rich_plasma",
      "random_donor_platelets",
      "sagm_packed_red_blood_cells",
      "single_donor_plasma",
      "single_donor_platelets",
      "whole_blood",
      "platelets",
    ])
    .nullable(),
  quantity: z.number().finite().positive().nullable(),
  quantity_unit: z.enum(["units", "bags", "ml"]).nullable(),
  district: z.string().trim().max(120).nullable(),
  state: z.string().trim().max(120).nullable(),
});

export type SuggestedFields = z.infer<typeof fieldsSchema>;

type SuggestionInput = {
  text?: string | undefined;
  imageBase64?: string | undefined;
  imageMimeType?: "image/jpeg" | "image/png" | "image/webp" | undefined;
};

type BackendResponse = {
  message?: unknown;
  error?: { message?: unknown };
};

/**
 * Field extraction runs on the backend so all AI assistance uses one server-side
 * Gemini key. This avoids requiring a second key on the frontend hosting provider.
 */
export async function suggestRequestFields(input: SuggestionInput): Promise<SuggestedFields> {
  const configuredBackendUrl = process.env["BACKEND_INTERNAL_URL"]?.trim();
  const backendUrl = configuredBackendUrl || (
    process.env["NODE_ENV"] === "production" ? "" : "http://127.0.0.1:8000"
  );
  if (!backendUrl) {
    throw new Error(
      "AI assistance is not connected to the backend. Set BACKEND_INTERNAL_URL in the frontend deployment.",
    );
  }

  let response: Response;
  try {
    response = await fetch(`${backendUrl.replace(/\/+$/, "")}/api/suggest-fields`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
      cache: "no-store",
      signal: AbortSignal.timeout(35_000),
    });
  } catch (error) {
    if (error instanceof Error && error.name === "TimeoutError") {
      throw new Error("AI field suggestions took too long. Try again, or enter the request manually.");
    }
    throw new Error(
      "AI assistance could not reach the backend. Check BACKEND_INTERNAL_URL and try again.",
    );
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new Error("AI assistance returned an unreadable response. Your form is still available.");
  }

  if (!response.ok) {
    const error = payload as BackendResponse;
    const message =
      typeof error?.message === "string"
        ? error.message
        : typeof error?.error?.message === "string"
          ? error.error.message
          : response.status === 404
            ? "AI assistance is not available on the backend deployment yet. Redeploy the latest backend."
            : "AI assistance is temporarily unavailable. Your search form is still available.";
    throw new Error(message);
  }

  const parsed = fieldsSchema.safeParse(payload);
  if (!parsed.success) {
    throw new Error("AI assistance returned incomplete fields. You can enter them manually.");
  }
  return parsed.data;
}
