"""Server-side Gemini assistance for typed requests and prescription images."""
from __future__ import annotations

import asyncio
import base64
import binascii
import logging
from typing import Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.config import get_gemini_api_key

router = APIRouter()
log = logging.getLogger("ai_assistance")

PRIMARY_MODEL = "gemini-3.8-flash"
FALLBACK_MODEL = "gemini-3.5-flash-lite"
MAX_IMAGE_BYTES = 5 * 1024 * 1024

BloodGroup = Literal["O-", "O+", "A-", "A+", "B-", "B+", "AB-", "AB+"]
Component = Literal[
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
]
QuantityUnit = Literal["units", "bags", "ml"]
ImageMimeType = Literal["image/jpeg", "image/png", "image/webp"]


class SuggestFieldsRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    text: str | None = Field(default=None, max_length=2000)
    image_base64: str | None = Field(default=None, alias="imageBase64", max_length=7_000_000)
    image_mime_type: ImageMimeType | None = Field(default=None, alias="imageMimeType")

    @model_validator(mode="after")
    def has_source(self) -> "SuggestFieldsRequest":
        if not (self.text and self.text.strip()) and not self.image_base64:
            raise ValueError("Add a short description or a prescription image.")
        if self.image_base64 and not self.image_mime_type:
            raise ValueError("The image type could not be read.")
        return self


class SuggestedFields(BaseModel):
    model_config = ConfigDict(extra="ignore")

    blood_group: BloodGroup | None = None
    component: Component | None = None
    quantity: float | None = Field(default=None, gt=0, le=1_000_000)
    quantity_unit: QuantityUnit | None = None
    district: str | None = Field(default=None, max_length=120)
    state: str | None = Field(default=None, max_length=120)


INSTRUCTIONS = """
Extract only explicit blood-request details from the user's text and/or prescription image.
Do not infer a blood group from a diagnosis, medication, or other medical context.
Never provide medical advice. Never guess missing or ambiguous details; return null.
Understand multilingual text and handwriting only when legible.
Preserve district and state only when clearly present.
For a generic mention of platelets, use platelet_concentrate as a request label; do not infer clinical equivalence to another platelet product.
Return a single JSON object with exactly these keys: blood_group, component, quantity, quantity_unit, district, state.
Allowed blood_group values: O-, O+, A-, A+, B-, B+, AB-, AB+.
Allowed component values: cryo_poor_plasma, cryoprecipitate, fresh_frozen_plasma, irradiated_rbc, leukoreduced_rbc, prbc, plasma, platelet_concentrate, platelet_rich_plasma, random_donor_platelets, sagm_packed_red_blood_cells, single_donor_plasma, single_donor_platelets, whole_blood, platelets.
Allowed quantity_unit values: units, bags, ml. quantity must be a positive number or null.
Return null for each missing field. Do not wrap JSON in Markdown or add explanation.
""".strip()


def _read_json_result(text: str) -> SuggestedFields:
    if not text or not text.strip():
        raise ValueError("The model returned an empty response")
    stripped = text.strip()
    start, end = stripped.find("{"), stripped.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("The model response was not JSON")
    return SuggestedFields.model_validate_json(stripped[start : end + 1])


def _provider_status_code(error: BaseException) -> int | None:
    """Return the HTTP status from a Gemini exception, including wrapped causes."""
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        for attr in ("status_code", "statusCode"):
            value = getattr(current, attr, None)
            if isinstance(value, int) and 100 <= value <= 599:
                return value
        current = current.__cause__ or current.__context__
    return None


def _suggest_with_gemini(text: str | None, image: bytes | None, mime_type: str | None, api_key: str) -> SuggestedFields:
    # Optional AI SDK dependency is imported on demand so non-AI search stays available.
    from google import genai
    from google.genai import types

    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            timeout=15_000,
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )
    contents: list[object] = [f"{INSTRUCTIONS}\n\nRequest text: {text.strip() if text and text.strip() else 'No written request provided.'}"]
    if image is not None and mime_type:
        contents.append(types.Part.from_bytes(data=image, mime_type=mime_type))

    try:
        last_error: Exception | None = None
        for model in (PRIMARY_MODEL, FALLBACK_MODEL):
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=SuggestedFields,
                        temperature=0,
                        max_output_tokens=512,
                    ),
                )
                # The SDK can return the structured Pydantic value directly. Prefer it,
                # then fall back to parsing response.text for SDK/model compatibility.
                parsed = getattr(response, "parsed", None)
                if isinstance(parsed, SuggestedFields):
                    return parsed
                if parsed is not None:
                    try:
                        return SuggestedFields.model_validate(parsed)
                    except (TypeError, ValueError, ValidationError):
                        pass
                return _read_json_result(getattr(response, "text", None) or "")
            except Exception as exc:
                last_error = exc
                status = _provider_status_code(exc)
                log.warning(
                    "Gemini field extraction model failed (model=%s, error=%s, status=%s)",
                    model,
                    type(exc).__name__,
                    status,
                )
                if status in (401, 403):
                    break
        raise RuntimeError("Gemini field extraction failed") from last_error
    finally:
        try:
            client.close()
        except Exception as exc:
            log.debug("Gemini client cleanup failed (%s)", type(exc).__name__)


@router.post("/api/suggest-fields")
async def suggest_fields(body: SuggestFieldsRequest):
    api_key = get_gemini_api_key()
    if not api_key:
        return JSONResponse(
            {"message": "AI assistance is not configured on the backend. Set GEMINI_API_KEY in the backend service."},
            status_code=503,
            headers={"Cache-Control": "no-store"},
        )

    image: bytes | None = None
    if body.image_base64:
        try:
            image = base64.b64decode(body.image_base64, validate=True)
        except (ValueError, binascii.Error):
            return JSONResponse({"message": "The prescription image could not be read."}, status_code=400)
        if not image or len(image) > MAX_IMAGE_BYTES:
            return JSONResponse({"message": "Prescription images must be 5 MB or smaller."}, status_code=413)

    try:
        result = await asyncio.to_thread(
            _suggest_with_gemini,
            body.text,
            image,
            body.image_mime_type,
            api_key,
        )
    except Exception as exc:
        print(exc)
        provider_status = _provider_status_code(exc)
        if provider_status in (401, 403):
            message = "AI assistance is not configured correctly. Check the Gemini API key and API access."
            response_status = 503
        elif provider_status == 429:
            message = "Gemini is busy or the API quota has been reached. Please try again shortly."
            response_status = 429
            
        else:
            message = "AI assistance is temporarily unavailable. Your search form is still available."
            response_status = 503
        log.warning(
            "Gemini field extraction failed (error=%s, status=%s)",
            type(exc).__name__,
            provider_status,
        )
        return JSONResponse({"message": message}, status_code=response_status, headers={"Cache-Control": "no-store"})

    return JSONResponse(result.model_dump(mode="json"), headers={"Cache-Control": "no-store"})
