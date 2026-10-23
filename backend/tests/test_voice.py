from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

from app.voice import _normalize_audio_mime_type, _provider_status_code, _transcribe_with_gemini


class ProviderError(RuntimeError):
    def __init__(self, status_code: int):
        super().__init__("provider failure")
        self.status_code = status_code


def _install_fake_genai(monkeypatch, models):
    google_module = ModuleType("google")
    genai_module = ModuleType("google.genai")
    types_module = ModuleType("google.genai.types")

    class Options:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class Part:
        @staticmethod
        def from_bytes(*, data, mime_type):
            return SimpleNamespace(data=data, mime_type=mime_type)

    types_module.HttpOptions = Options
    types_module.HttpRetryOptions = Options
    types_module.GenerateContentConfig = Options
    types_module.Part = Part
    client = SimpleNamespace(models=models, close=lambda: None)
    genai_module.types = types_module
    genai_module.Client = lambda **kwargs: client
    google_module.genai = genai_module
    monkeypatch.setitem(sys.modules, "google", google_module)
    monkeypatch.setitem(sys.modules, "google.genai", genai_module)
    monkeypatch.setitem(sys.modules, "google.genai.types", types_module)


def test_normalizes_mobile_safari_audio_and_rejects_unknown_types():
    assert _normalize_audio_mime_type("audio/mp4;codecs=mp4a.40.2") == "audio/m4a"
    assert _normalize_audio_mime_type("audio/webm;codecs=opus") == "audio/webm"
    assert _normalize_audio_mime_type("video/mp4") is None


def test_transcription_falls_back_quickly_after_gemini_503(monkeypatch):
    calls = []
    received_audio = []

    class Models:
        def generate_content(self, *, model, contents, config):
            calls.append(model)
            received_audio.extend(item for item in contents if hasattr(item, "mime_type"))
            if model == "gemini-3.8-flash":
                raise ProviderError(503)
            return SimpleNamespace(text="Need two units of O negative whole blood.")

    _install_fake_genai(monkeypatch, Models())
    transcript = _transcribe_with_gemini(
        b"test-audio-bytes", "recording.m4a", "audio/mp4", "en", "test-key"
    )

    assert transcript == "Need two units of O negative whole blood."
    assert calls == ["gemini-3.8-flash", "gemini-3.5-flash-lite"]
    assert received_audio and received_audio[0].mime_type == "audio/m4a"


def test_provider_status_finds_wrapped_error():
    try:
        try:
            raise ProviderError(429)
        except ProviderError as inner:
            raise RuntimeError("wrapped") from inner
    except RuntimeError as outer:
        assert _provider_status_code(outer) == 429
