
"""Voice transcription using server-side Gemini audio transcription."""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from email import policy
from email.parser import BytesParser
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.config import get_gemini_api_key

router = APIRouter()
log = logging.getLogger("voice")

MAX_AUDIO_BYTES = 14 * 1024 * 1024

AUDIO_MIME_TYPES = {
    "audio/aac",
    "audio/aiff",
    "audio/alaw",
    "audio/flac",
    "audio/l16",
    "audio/m4a",
    "audio/mp3",
    "audio/mpeg",
    "audio/mulaw",
    "audio/ogg",
    "audio/opus",
    "audio/wav",
    "audio/webm",
}

AUDIO_MIME_ALIASES = {
    "audio/mp4": "audio/m4a",
    "audio/x-m4a": "audio/m4a",
    "audio/x-wav": "audio/wav",
    "audio/wave": "audio/wav",
    "audio/x-flac": "audio/flac",
}


def _normalize_audio_mime_type(media_type: str) -> str | None:
    """Normalize and validate an incoming audio MIME type."""
    clean = (media_type or "").split(";", 1)[0].strip().lower()
    clean = AUDIO_MIME_ALIASES.get(clean, clean)

    return clean if clean in AUDIO_MIME_TYPES else None


def _provider_status_code(error: BaseException) -> int | None:
    """Find an HTTP status code in an exception or its wrapped causes."""
    current: BaseException | None = error
    seen: set[int] = set()

    while current is not None and id(current) not in seen:
        seen.add(id(current))

        for attr in ("status_code", "statusCode", "code"):
            value = getattr(current, attr, None)

            if isinstance(value, int) and 100 <= value <= 599:
                return value

        current = current.__cause__ or current.__context__

    return None


def _transcribe_with_gemini(
    content: bytes,
    filename: str,
    media_type: str,
    language: str | None,
    api_key: str,
) -> str:
    """Transcribe an uploaded audio recording using Gemini Interactions."""
    from google import genai
    from google.genai import types

    normalized_type = _normalize_audio_mime_type(media_type)
    if not normalized_type:
        raise ValueError("Unsupported audio format")

    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            timeout=20_000,
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )

    temp_path: str | None = None
    uploaded_file = None

    try:
        suffix = Path(filename).suffix[:10] or ".audio"

        # The dedicated transcription API uses an uploaded audio file URI.
        with tempfile.NamedTemporaryFile(
            suffix=suffix,
            delete=False,
        ) as temp_file:
            temp_file.write(content)
            temp_path = temp_file.name

        uploaded_file = client.files.upload(
            file=temp_path,
            config={"mime_type": normalized_type},
        )

        request_args: dict = {
            "model": "gemini-3.5-transcribe",
            "input": [
                {
                    "type": "audio",
                    "uri": uploaded_file.uri,
                    "mime_type": normalized_type,
                }
            ],
        }

        # Only send an explicit language when one was supplied.
        # Confirm supported language-code formats for your SDK/model version.
        if language:
            request_args["generation_config"] = {
                "transcription_config": {
                    "language_codes": [language],
                }
            }

        interaction = client.interactions.create(**request_args)
        transcript = (
            getattr(interaction, "output_text", None) or ""
        ).strip()

        if not transcript:
            log.warning(
                "Gemini returned an empty transcript (model=%s)",
                "gemini-3.5-transcribe",
            )
            raise RuntimeError("Gemini returned no transcript")

        return transcript

    except Exception:
        # Keep provider diagnostics on the server. Never log audio or API keys.
        log.exception("Gemini transcription request failed")
        raise

    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                log.debug("Temporary audio file cleanup failed")

        if uploaded_file is not None:
            try:
                client.files.delete(name=uploaded_file.name)
            except Exception as exc:
                log.warning(
                    "Gemini uploaded-file cleanup failed (%s)",
                    type(exc).__name__,
                )

        try:
            client.close()
        except Exception as exc:
            log.debug(
                "Gemini client cleanup failed (%s)",
                type(exc).__name__,
            )


@router.post("/api/transcribe")
async def transcribe(request: Request):
    """Receive a multipart audio upload and return its transcript."""
    content_type = request.headers.get("content-type", "")
    boundary = None

    if content_type.lower().startswith("multipart/form-data"):
        for item in content_type.split(";")[1:]:
            name, sep, value = item.strip().partition("=")

            if sep and name.lower() == "boundary":
                boundary = value.strip().strip('"')
                break

    if not boundary or len(boundary) > 200:
        return JSONResponse(
            {"message": "The voice note could not be read."},
            status_code=400,
            headers={"Cache-Control": "no-store"},
        )

    raw = await request.body()

    # Reject oversized requests before parsing their multipart contents.
    # Multipart framing adds a small amount of overhead beyond the audio bytes.
    if len(raw) > MAX_AUDIO_BYTES + 1024 * 1024:
        return JSONResponse(
            {
                "message": (
                    "That recording is too large. Try a shorter voice note."
                )
            },
            status_code=413,
            headers={"Cache-Control": "no-store"},
        )

    try:
        message = BytesParser(policy=policy.default).parsebytes(
            b"Content-Type: "
            + content_type.encode("ascii", "ignore")
            + b"\r\nMIME-Version: 1.0\r\n\r\n"
            + raw
        )

        content: bytes | None = None
        filename = "voice-note.webm"
        media_type = ""
        language: str | None = None

        for part in message.iter_parts():
            if part.get_content_disposition() != "form-data":
                continue

            field_name = part.get_param(
                "name",
                header="content-disposition",
            )

            if field_name == "file":
                content = part.get_payload(decode=True)
                filename = part.get_filename() or filename
                media_type = part.get_content_type()

            elif field_name == "language":
                language = str(part.get_content()).strip()

    except (AttributeError, TypeError, ValueError):
        return JSONResponse(
            {"message": "The voice note could not be read."},
            status_code=400,
            headers={"Cache-Control": "no-store"},
        )

    if not content:
        return JSONResponse(
            {"message": "Choose a voice note before transcribing."},
            status_code=400,
            headers={"Cache-Control": "no-store"},
        )

    if len(content) > MAX_AUDIO_BYTES:
        return JSONResponse(
            {
                "message": (
                    "That recording is too large. Try a shorter voice note."
                )
            },
            status_code=413,
            headers={"Cache-Control": "no-store"},
        )

    normalized_media_type = _normalize_audio_mime_type(media_type)

    if not normalized_media_type:
        return JSONResponse(
            {
                "message": (
                    "This recording format is not supported. "
                    "Try recording again in your browser."
                )
            },
            status_code=415,
            headers={"Cache-Control": "no-store"},
        )

    media_type = normalized_media_type

    if language and (
        len(language) > 16
        or not all(ch.isalnum() or ch == "-" for ch in language)
    ):
        language = None

    api_key = get_gemini_api_key()

    if not api_key:
        return JSONResponse(
            {"message": "Voice assistance is not configured yet."},
            status_code=503,
            headers={"Cache-Control": "no-store"},
        )

    try:
        transcript = await asyncio.to_thread(
            _transcribe_with_gemini,
            content,
            filename,
            media_type,
            language,
            api_key,
        )

    except Exception as exc:
        provider_status = _provider_status_code(exc)

        # Log safe diagnostics. The helper logs the provider traceback.
        log.warning(
            "Transcription endpoint failed (error=%s, provider_status=%s)",
            type(exc).__name__,
            provider_status,
        )

        if provider_status in (401, 403):
            message = (
                "Voice assistance is not configured correctly. "
                "Check the Gemini API key and API access."
            )
            status_code = 503

        elif provider_status == 429:
            message = (
                "Gemini is busy or the API quota has been reached. "
                "Try again shortly, or type your request."
            )
            status_code = 429

        elif provider_status == 503:
            message = (
                "The transcription service is temporarily unavailable. "
                "Try again shortly or type your request."
            )
            status_code = 503

        else:
            message = (
                "Voice transcription is temporarily unavailable. "
                "Try again shortly or type your request."
            )
            status_code = 503

        return JSONResponse(
            {"message": message},
            status_code=status_code,
            headers={"Cache-Control": "no-store"},
        )

    if not transcript:
        return JSONResponse(
            {
                "message": (
                    "No clear speech was detected. "
                    "Try again or type your request."
                )
            },
            status_code=422,
            headers={"Cache-Control": "no-store"},
        )

    return JSONResponse(
        {"text": transcript},
        headers={"Cache-Control": "no-store"},
    )
