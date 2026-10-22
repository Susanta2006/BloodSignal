
"""Voice transcription proxy. Audio and provider credentials stay server-side."""
from __future__ import annotations

import asyncio
import os
import tempfile
from email import policy
from email.parser import BytesParser
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from google import genai

router = APIRouter()

MAX_AUDIO_BYTES = 14 * 1024 * 1024
TRANSCRIPTION_MODEL = "gemini-3.5-transcribe"


def _transcribe_with_gemini(
    content: bytes,
    filename: str,
    media_type: str,
    language: str | None,
    api_key: str,
) -> str:
    """Run Gemini's file-transcription workflow."""
    client = genai.Client(api_key=api_key)
    uploaded_file = None
    temp_path = None

    try:
        suffix = Path(filename).suffix[:10] or ".audio"

        with tempfile.NamedTemporaryFile(
            suffix=suffix, delete=False
        ) as temp_file:
            temp_file.write(content)
            temp_path = temp_file.name

        uploaded_file = client.files.upload(
            file=temp_path,
            config={"mime_type": media_type},
        )

        transcription_config = {}
        if language:
            transcription_config["language_codes"] = [language]

        interaction_args = {
            "model": TRANSCRIPTION_MODEL,
            "input": [
                {
                    "type": "audio",
                    "uri": uploaded_file.uri,
                    "mime_type": media_type,
                }
            ],
        }

        if transcription_config:
            interaction_args["generation_config"] = {
                "transcription_config": transcription_config
            }

        interaction = client.interactions.create(**interaction_args)
        return (interaction.output_text or "").strip()

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
                # Do not mask the transcription result if cleanup fails.
                pass

        client.close()


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

    if not media_type.startswith("audio/"):
        return JSONResponse(
            {
                "message": (
                    "Choose an audio recording, such as a voice note."
                )
            },
            status_code=415,
            headers={"Cache-Control": "no-store"},
        )

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

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()

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

    except Exception:
        # Keep provider details and credentials out of client responses.
        return JSONResponse(
            {
                "message": (
                    "Voice transcription is unavailable. "
                    "You can type your request instead."
                )
            },
            status_code=503,
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
