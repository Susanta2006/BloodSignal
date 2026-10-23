"""Voice transcription using server-side Gemini with fallback models."""

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

TRANSCRIPTION_MODELS = (
    "gemini-3.8-flash",
    "gemini-3.5-flash-lite",
)

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
    clean = (media_type or "").split(";", 1)[0].strip().lower()
    clean = AUDIO_MIME_ALIASES.get(clean, clean)
    return clean if clean in AUDIO_MIME_TYPES else None


def _provider_status_code(error: BaseException) -> int | None:
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
    """Transcribe audio using Gemini with automatic fallback."""

    from google import genai
    from google.genai import types

    normalized_type = _normalize_audio_mime_type(media_type)
    if not normalized_type:
        raise ValueError("Unsupported audio format")

    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            timeout=15000,
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )

    uploaded_file = None
    temp_path: str | None = None
    last_error: Exception | None = None

    prompt = (
        "Transcribe all spoken words in this audio. "
        "Return ONLY the transcript. "
        "Do not summarize. "
        "Do not translate. "
        "Preserve names, numbers, blood groups, and units exactly."
    )

    if language:
        prompt += f" Language: {language}."

    try:
        suffix = Path(filename).suffix or ".audio"

        with tempfile.NamedTemporaryFile(
            suffix=suffix,
            delete=False,
        ) as f:
            f.write(content)
            temp_path = f.name

        uploaded_file = client.files.upload(
            file=temp_path,
            config={"mime_type": normalized_type},
        )

        for model in TRANSCRIPTION_MODELS:
            try:
                log.info("Trying transcription model=%s", model)

                response = client.models.generate_content(
                    model=model,
                    contents=[prompt, uploaded_file],
                    config=types.GenerateContentConfig(
                        temperature=0,
                        max_output_tokens=4096,
                    ),
                )

                transcript = (getattr(response, "text", None) or "").strip()

                if not transcript:
                    for candidate in getattr(response, "candidates", []) or []:
                        content_obj = getattr(candidate, "content", None)

                        for part in getattr(content_obj, "parts", []) or []:
                            audio = getattr(part, "audio_transcription", None)
                            if audio is not None:
                                transcript = (
                                    getattr(audio, "text", None) or ""
                                ).strip()

                            if transcript:
                                break

                        if transcript:
                            break

                if transcript:
                    log.info("Transcription succeeded using %s", model)
                    return transcript

                log.warning(
                    "No transcript returned from %s",
                    model,
                )

            except Exception as exc:
                last_error = exc
                status = _provider_status_code(exc)

                log.warning(
                    "Model failed model=%s error=%s status=%s",
                    model,
                    type(exc).__name__,
                    status,
                )

                if status in (401, 403):
                    break

        if last_error:
            raise RuntimeError("All Gemini models failed") from last_error

        raise RuntimeError("Gemini returned no transcript")

    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass

        if uploaded_file is not None:
            try:
                client.files.delete(name=uploaded_file.name)
            except Exception:
                pass

        try:
            client.close()
        except Exception:
            pass


@router.post("/api/transcribe")
async def transcribe(request: Request):
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
        )

    raw = await request.body()

    message = BytesParser(policy=policy.default).parsebytes(
        b"Content-Type: "
        + content_type.encode("ascii", "ignore")
        + b"\r\nMIME-Version: 1.0\r\n\r\n"
        + raw
    )

    content = None
    filename = "voice-note.webm"
    media_type = ""
    language = None

    try:
        for part in message.iter_parts():
            if part.get_content_disposition() != "form-data":
                continue

            field = part.get_param(
                "name",
                header="content-disposition",
            )

            if field == "file":
                content = part.get_payload(decode=True)
                filename = part.get_filename() or filename
                media_type = part.get_content_type()

            elif field == "language":
                language = str(part.get_content()).strip()

    except Exception:
        return JSONResponse(
            {"message": "The voice note could not be read."},
            status_code=400,
        )

    if not content:
        return JSONResponse(
            {"message": "Choose a voice note before transcribing."},
            status_code=400,
        )

    if len(content) > MAX_AUDIO_BYTES:
        return JSONResponse(
            {
                "message": "That recording is too large. Try a shorter voice note."
            },
            status_code=413,
        )

    media_type = _normalize_audio_mime_type(media_type)

    if not media_type:
        return JSONResponse(
            {
                "message": "This recording format is not supported."
            },
            status_code=415,
        )

    if language and (
        len(language) > 16
        or not all(c.isalnum() or c == "-" for c in language)
    ):
        language = None

    api_key = get_gemini_api_key()

    if not api_key:
        return JSONResponse(
            {"message": "Voice assistance is not configured yet."},
            status_code=503,
        )

    try:
        text = await asyncio.to_thread(
            _transcribe_with_gemini,
            content,
            filename,
            media_type,
            language,
            api_key,
        )

    except Exception as exc:
        status = _provider_status_code(exc)

        log.warning(
            "Transcription endpoint failed error=%s status=%s",
            type(exc).__name__,
            status,
        )

        if status in (401, 403):
            message = (
                "Voice assistance is not configured correctly. Check the Gemini API key."
            )
            code = 503

        elif status == 429:
            message = (
                "Gemini is busy or quota has been reached. Try again shortly."
            )
            code = 429

        elif status == 503:
            message = (
                "The transcription service is temporarily unavailable. Try again shortly."
            )
            code = 503

        else:
            message = (
                "Voice transcription is temporarily unavailable. Try again shortly."
            )
            code = 503

        return JSONResponse(
            {"message": message},
            status_code=code,
        )

    if not text:
        return JSONResponse(
            {"message": "No clear speech was detected."},
            status_code=422,
        )

    return JSONResponse({"text": text})
