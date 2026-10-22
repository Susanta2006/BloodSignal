"""Voice transcription proxy. Audio and provider credentials stay server-side."""
from __future__ import annotations

import json
from email import policy
from email.parser import BytesParser

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()
MAX_AUDIO_BYTES = 14 * 1024 * 1024
TRANSCRIPTION_MODEL = "google/gemini-3.5-transcribe"


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
        return JSONResponse({"message": "The voice note could not be read."}, status_code=400)

    raw = await request.body()  # API middleware enforces the configured total size limit first.
    message = BytesParser(policy=policy.default).parsebytes(
        b"Content-Type: " + content_type.encode("ascii", "ignore") +
        b"\r\nMIME-Version: 1.0\r\n\r\n" + raw
    )
    content, filename, media_type, language = None, "voice-note.webm", "", None
    try:
        for part in message.iter_parts():
            if part.get_content_disposition() != "form-data":
                continue
            field_name = part.get_param("name", header="content-disposition")
            if field_name == "file":
                content = part.get_payload(decode=True)
                filename = part.get_filename() or filename
                media_type = part.get_content_type()
            elif field_name == "language":
                language = part.get_content()
    except (AttributeError, TypeError, ValueError):
        return JSONResponse({"message": "The voice note could not be read."}, status_code=400)
    if content is None:
        return JSONResponse({"message": "Choose a voice note before transcribing."}, status_code=400)
    if not media_type.startswith("audio/"):
        return JSONResponse({"message": "Choose an audio recording, such as a voice note."},
                            status_code=415, headers={"Cache-Control": "no-store"})
    if not content:
        return JSONResponse({"message": "Choose a voice note before transcribing."}, status_code=400)
    if len(content) > MAX_AUDIO_BYTES:
        return JSONResponse({"message": "That recording is too large. Try a shorter voice note."},
                            status_code=413)

    settings = request.app.state.container.settings
    api_key = settings.lovable_ai_gateway_key.get_secret_value()
    if not api_key:
        return JSONResponse({"message": "Voice assistance is not configured yet."}, status_code=503,
                            headers={"Cache-Control": "no-store"})
    data = {"model": TRANSCRIPTION_MODEL, "stream": "false", "response_format": "json"}
    if language and len(language) <= 16 and all(ch.isalnum() or ch == "-" for ch in language):
        data["language"] = language
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(45, connect=5)) as client:
            response = await client.post(
                "https://ai.gateway.lovable.dev/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {api_key}"}, data=data,
                files={"file": (filename, content, media_type)},
            )
    except httpx.HTTPError:
        return JSONResponse({"message": "Voice assistance could not be reached. You can type your request instead."},
                            status_code=503, headers={"Cache-Control": "no-store"})
    if not response.is_success:
        return JSONResponse({"message": f"Voice transcription is unavailable (request {response.status_code})."},
                            status_code=503 if response.status_code >= 500 else response.status_code,
                            headers={"Cache-Control": "no-store"})
    try:
        result = response.json()
        text = result.get("text", "") if isinstance(result, dict) else ""
    except (json.JSONDecodeError, ValueError):
        text = ""
    if not isinstance(text, str) or not text.strip():
        return JSONResponse({"message": "No clear speech was detected. Try again or type your request."},
                            status_code=422, headers={"Cache-Control": "no-store"})
    return JSONResponse({"text": text.strip()}, headers={"Cache-Control": "no-store"})
