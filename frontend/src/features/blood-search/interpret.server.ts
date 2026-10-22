
import { createGoogleGenerativeAI } from "@ai-sdk/google";
import { streamText } from "ai";
import { z } from "zod";

const primaryModel = "gemini-3.8-flash";
const fallbackModel = "gemini-3.5-flash-lite";

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

function parseModelJson(text: string): SuggestedFields {
  const start = text.indexOf("{");
  const end = text.lastIndexOf("}");

  if (start < 0 || end <= start) {
    throw new Error(
      "The request details could not be read. You can enter them manually.",
    );
  }

  try {
    return fieldsSchema.parse(
      JSON.parse(text.slice(start, end + 1)),
    );
  } catch {
    throw new Error(
      "The request details could not be read. You can enter them manually.",
    );
  }
}

function getFailureMessage(error: unknown): string {
  const value =
    typeof error === "object" && error !== null
      ? (error as Record<string, unknown>)
      : {};

  const status = value["statusCode"] ?? value["status"];
  const message =
    typeof value["message"] === "string" ? value["message"] : "";

  if (status === 401) {
    return "AI assistance is not configured correctly. Your search form is still available.";
  }

  if (status === 403) {
    return (
      message ||
      "AI assistance is currently unavailable. Your search form is still available."
    );
  }

  if (status === 429) {
    return "AI assistance is busy right now. Your search form is still available.";
  }

  if (typeof status === "number" && status >= 500) {
    return "AI assistance is temporarily unavailable. Your search form is still available.";
  }

  return "AI assistance could not read this request. You can enter the fields manually.";
}

export async function suggestRequestFields(input: {
  text?: string | undefined;
  imageBase64?: string | undefined;
  imageMimeType?: "image/jpeg" | "image/png" | "image/webp" | undefined;
}): Promise<SuggestedFields> {
  const apiKey = process.env["GEMINI_API_KEY"];

  if (!apiKey) {
    throw new Error(
      "AI assistance is not configured yet. Your search form is still available.",
    );
  }

  const google = createGoogleGenerativeAI({ apiKey });

  const instructions = [
    "Extract only explicit blood-request details from the user's text or prescription image.",
    "Do not infer a blood group from a diagnosis, medication, or other medical context.",
    "Never give medical advice. Do not guess any missing detail; use null.",
    "Understand multilingual text and handwriting when legible.",
    "Preserve district and state only when clearly present.",
    "Return only one valid JSON object with keys blood_group, component, quantity, quantity_unit, district, state.",
    "Allowed blood_group values: O-, O+, A-, A+, B-, B+, AB-, AB+.",
    "Allowed component values: cryo_poor_plasma, cryoprecipitate, fresh_frozen_plasma, irradiated_rbc, leukoreduced_rbc, prbc, plasma, platelet_concentrate, platelet_rich_plasma, random_donor_platelets, sagm_packed_red_blood_cells, single_donor_plasma, single_donor_platelets, whole_blood, platelets.",
    "For a generic mention of platelets, use platelet_concentrate only as the source selection and do not infer clinical equivalence to other platelet products.",
    "Allowed quantity_unit values: units, bags, ml.",
    "quantity must be a positive number or null.",
    "Use null for every missing or ambiguous field.",
    "Return JSON only, without Markdown fences or explanatory text.",
  ].join(" ");

  const content: Array<
    | { type: "text"; text: string }
    | { type: "image"; image: Uint8Array; mediaType: string }
  > = [
    {
      type: "text",
      text: `${instructions}\n\nRequest: ${
        input.text?.trim() || "No written request."
      }`,
    },
  ];

  if (input.imageBase64 && input.imageMimeType) {
    const binary = atob(input.imageBase64);
    const image = Uint8Array.from(binary, (char) =>
      char.charCodeAt(0),
    );

    content.push({
      type: "image",
      image,
      mediaType: input.imageMimeType,
    });
  }

  const run = async (model: string): Promise<SuggestedFields> => {
    const result = streamText({
      model: google(model),
      maxRetries: 0,
      messages: [
        {
          role: "user",
          content,
        },
      ],
    });

    return parseModelJson(await result.text);
  };

  try {
    return await run(primaryModel);
  } catch (error) {
    if (
      error instanceof Error &&
      error.message.startsWith(
        "The request details could not be read",
      )
    ) {
      throw error;
    }

    const record =
      typeof error === "object" && error !== null
        ? (error as Record<string, unknown>)
        : {};

    const status = record["statusCode"] ?? record["status"];

    if (
      status === 429 ||
      (typeof status === "number" && status >= 500)
    ) {
      try {
        return await run(fallbackModel);
      } catch (fallbackError) {
        throw new Error(getFailureMessage(fallbackError));
      }
    }

    throw new Error(getFailureMessage(error));
  }
}
