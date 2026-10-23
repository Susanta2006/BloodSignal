import { z } from "zod";

export const bloodGroups = ["O-", "O+", "A-", "A+", "B-", "B+", "AB-", "AB+"] as const;
export const components = [
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
  // Backwards compatibility for the current suggestion extractor; normalized before search.
  "platelets",
] as const;
export const quantityUnits = ["units", "ml"] as const;

const manualLocationSchema = z.object({
  mode: z.literal("manual"),
  district: z
    .string()
    .trim()
    .min(2, "Enter a district or city.")
    .max(120, "Use 120 characters or fewer."),
  state: z.string().trim().min(2, "Enter a state.").max(120, "Use 120 characters or fewer."),
  country: z.literal("IN"),
});

const deviceLocationSchema = z.object({
  mode: z.literal("device"),
  // GPS supplies the origin for distance calculations; the selected search area still
  // determines which state's inventory is queried and which district is filtered.
  district: z.string().trim().min(2, "Choose a district or city.").max(120),
  state: z.string().trim().min(2, "Choose a state.").max(120),
  latitude: z.number().finite().min(-90).max(90),
  longitude: z.number().finite().min(-180).max(180),
  country: z.literal("IN"),
  location_consent: z.literal(true),
});

export const searchRequestSchema = z.object({
  blood_group: z.enum(
    [...bloodGroups] as [(typeof bloodGroups)[number], ...(typeof bloodGroups)[number][]],
    { message: "Choose a supported blood group." },
  ),
  component: z.enum(
    [...components] as [(typeof components)[number], ...(typeof components)[number][]],
    { message: "Choose a blood component." },
  ),
  quantity: z
    .number({ message: "Enter a whole-number quantity." })
    .int({ message: "Quantity must be a whole number." })
    .min(1, "Quantity must be at least 1.")
    .max(20, "Quantity cannot exceed 20."),
  quantity_unit: z.enum(
    [...quantityUnits] as [(typeof quantityUnits)[number], ...(typeof quantityUnits)[number][]],
    { message: "Choose a quantity unit." },
  ),
  location: z.discriminatedUnion("mode", [manualLocationSchema, deviceLocationSchema]),
  search_options: z.object({
    include_nearby_districts: z.boolean(),
    max_radius_km: z.number().positive().finite().optional(),
  }),
});

export type SearchRequest = z.infer<typeof searchRequestSchema>;

// Before submit, a GPS origin may be ready before the user selects an inventory area.
// buildSearchRequest combines these coordinates with the selected form state and district.
export type LocationSelection =
  | z.infer<typeof manualLocationSchema>
  | Omit<z.infer<typeof deviceLocationSchema>, "district" | "state">;

export type SearchFormValues = {
  blood_group: (typeof bloodGroups)[number];
  component: (typeof components)[number];
  quantity: string;
  quantity_unit: (typeof quantityUnits)[number];
  district: string;
  state: string;
};

export const initialSearchForm: SearchFormValues = {
  blood_group: "O-",
  component: "whole_blood",
  quantity: "2",
  quantity_unit: "units",
  district: "",
  state: "",
};

export function buildSearchRequest(
  values: SearchFormValues,
  location: LocationSelection,
  includeNearbyDistricts: boolean,
): SearchRequest {
  const district = values.district.trim();
  const state = values.state.trim();
  return searchRequestSchema.parse({
    blood_group: values.blood_group,
    component: values.component,
    quantity: Number(values.quantity),
    quantity_unit: values.quantity_unit,
    location:
      location.mode === "device"
        ? { ...location, district, state, location_consent: true as const }
        : { mode: "manual", district, state, country: "IN" as const },
    search_options: { include_nearby_districts: includeNearbyDistricts },
  });
}

export function fieldErrors(error: z.ZodError): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const issue of error.issues) {
    const field = issue.path.at(-1);
    if (typeof field === "string" && !(field in errors)) errors[field] = issue.message;
  }
  return errors;
}
