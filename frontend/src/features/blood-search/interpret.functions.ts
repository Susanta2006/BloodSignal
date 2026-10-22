import { createServerFn } from "@tanstack/react-start";
import { z } from "zod";

import { suggestRequestFields } from "./interpret.server";

const inputSchema = z
  .object({
    text: z.string().trim().max(2000).optional(),
    imageBase64: z.string().max(7_000_000).optional(),
    imageMimeType: z.enum(["image/jpeg", "image/png", "image/webp"]).optional(),
  })
  .refine(
    (value) => Boolean(value.text || value.imageBase64),
    "Add a short description or a prescription image.",
  )
  .refine(
    (value) => !value.imageBase64 || Boolean(value.imageMimeType),
    "The image type could not be read.",
  );

export const suggestFields = createServerFn({ method: "POST" })
  .validator((input: unknown) => inputSchema.parse(input))
  .handler(async ({ data }) => suggestRequestFields(data));
