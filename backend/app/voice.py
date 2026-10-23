"""Voice transcription using server-side Gemini audio understanding."""
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
MAX_INLINE_AUDIO_BYTES = 10 * 1024 * 1024
TRANSCRIPTION_MODELS = ("gemini-3.5-transcribe",)
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
    "audio/mp4": "audio/m4a",  # Common MediaRecorder output on Safari / iOS.
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
    """Read an HTTP status from a provider exception or its wrapped cause."""
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


def _transcribe_with_gemini(
    content: bytes,
    filename: str,
    media_type: str,
    language: str | None,
    api_key: str,
) -> str:
    """Transcribe audio through Gemini's general audio endpoint with bounded fallback.

    The dedicated Interactions transcription endpoint was returning repeated 503s in
    production and kept requests open for over 100 seconds. Gemini's ordinary
    generate_content audio input supports WebM and common mobile audio formats, so this
    path uses that endpoint first and tries one alternate model if needed. SDK retries
    are disabled; the application controls the number of attempts and total latency.
    """
    from google import genai
    from google.genai import types

    normalized_type = _normalize_audio_mime_type(media_type)
    if not normalized_type:
        raise ValueError("Unsupported audio format")

    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            timeout=12_000,
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )
    uploaded_file = None
    temp_path: str | None = None

    language_hint = (
        f"The selected language code is {language}. Transcribe in that language and do not translate."
        if language
        else "Detect the spoken language automatically and transcribe in the original language; do not translate."
    )
    prompt = (
        "Transcribe the speech in this audio recording faithfully. Return only the transcript, "
        "without quotes, explanations, summaries, or medical advice. Preserve names, blood groups "
        "such as O negative, numbers, quantities, and units exactly as spoken. " + language_hint
    )

    try:
        if len(content) <= MAX_INLINE_AUDIO_BYTES:
            # Most voice notes are small; avoid a separate Files API round trip.
            contents: list[object] = [
                prompt,
                types.Part.from_bytes(data=content, mime_type=normalized_type),
            ]
        else:
            suffix = Path(filename).suffix[:10] or ".audio"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp_file:
                temp_file.write(content)
                temp_path = temp_file.name
            uploaded_file = client.files.upload(
                file=temp_path,
                config={"mime_type": normalized_type},
            )
            contents = [prompt, uploaded_file]

        last_error: Exception | None = None
        for model in TRANSCRIPTION_MODELS:
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        temperature=0,
                        max_output_tokens=4096,
                    ),
                )
                transcript = (getattr(response, "text", None) or "").strip()

                # Gemini 3.5 Transcribe may return the transcript in
                # an audio_transcription response part instead of response.text.
                if not transcript:
                    for candidate in getattr(response, "candidates", None) or []:
                        candidate_content = getattr(candidate, "content", None)
                        for part in getattr(candidate_content, "parts", None) or []:
                            audio_transcription = getattr(part, "audio_transcription", None)
                            if audio_transcription is not None:
                                transcript = (
                                    getattr(audio_transcription, "text", None) or ""
                                ).strip()
                                if transcript:
                                    break
                        if transcript:
                            break
                
                if transcript:
                    return transcript
                
                log.warning(
                    "Gemini audio transcription returned no transcript (model=%s)",
                    model,
                )
            except Exception as exc:
                last_error = exc
                status = _provider_status_code(exc)
                log.warning(
                    "Gemini audio model failed (model=%s, error=%s, status=%s)",
                    model,
                    type(exc).__name__,
                    status,
                )
                if status in (401, 403):
                    break

        if last_error is not None:
            raise RuntimeError("Gemini audio transcription failed") from last_error
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
            except Exception as exc:
                log.debug("Gemini audio cleanup failed (%s)", type(exc).__name__)
        try:
            client.close()
        except Exception as exc:
            log.debug("Gemini client cleanup failed (%s)", type(exc).__name__)


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

            field_name = part.get_param(
                "name", header="content-disposition"
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
        )

    if content is None or not content:
        return JSONResponse(
            {"message": "Choose a voice note before transcribing."},
            status_code=400,
        )

    normalized_media_type = _normalize_audio_mime_type(media_type)
    if not normalized_media_type:
        return JSONResponse(
            {
                "message": (
                    "This recording format is not supported. Try recording again in your browser."
                )
            },
            status_code=415,
            headers={"Cache-Control": "no-store"},
        )
    media_type = normalized_media_type

    if len(content) > MAX_AUDIO_BYTES:
        return JSONResponse(
            {
                "message": (
                    "That recording is too large. "
                    "Try a shorter voice note."
                )
            },
            status_code=413,
            headers={"Cache-Control": "no-store"},
        )

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
        text = await asyncio.to_thread(
            _transcribe_with_gemini,
            content,
            filename,
            media_type,
            language,
            api_key,
        )

    except Exception as exc:
        # Log only safe diagnostics; never log credentials or request audio.
        provider_status = _provider_status_code(exc)
        log.warning(
            "Gemini transcription failed (error=%s, status=%s)",
            type(exc).__name__,
            provider_status,
        )
        if provider_status in (401, 403):
            message = "Voice assistance is not configured correctly. Check the Gemini API key and API access."
            status_code = 503
        elif provider_status == 429:
            message = "Gemini is busy or the API quota has been reached. Try again shortly, or type your request."
            status_code = 429
        else:
            message = "Voice transcription is temporarily unavailable. Try again shortly or type your request."
            status_code = 503
        return JSONResponse(
            {"message": message},
            status_code=status_code,
            headers={"Cache-Control": "no-store"},
        )

    if not text:
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
        {"text": text},
        headers={"Cache-Control": "no-store"},
    )
