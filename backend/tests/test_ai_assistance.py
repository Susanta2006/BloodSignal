from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

from app import ai_assistance
from app.config import get_gemini_api_key, get_settings


def _install_fake_genai(monkeypatch, client):
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
    genai_module.types = types_module
    genai_module.Client = lambda **kwargs: client
    google_module.genai = genai_module
    monkeypatch.setitem(sys.modules, "google", google_module)
    monkeypatch.setitem(sys.modules, "google.genai", genai_module)
    monkeypatch.setitem(sys.modules, "google.genai.types", types_module)


def test_gemini_key_can_come_from_backend_dotenv(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("GEMINI_API_KEY=local-test-key\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    get_settings.cache_clear()
    try:
        assert get_gemini_api_key() == "local-test-key"
    finally:
        get_settings.cache_clear()


def test_google_api_key_alias_is_supported(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "alias-test-key")
    assert get_gemini_api_key() == "alias-test-key"


def test_suggestion_extractor_accepts_structured_sdk_response(monkeypatch):
    result = ai_assistance.SuggestedFields(
        blood_group="O-",
        component="whole_blood",
        quantity=2,
        quantity_unit="units",
        district="Imphal West",
        state="Manipur",
    )

    class Models:
        def generate_content(self, **kwargs):
            return SimpleNamespace(parsed=result, text="")

    client = SimpleNamespace(models=Models(), close=lambda: None)
    _install_fake_genai(monkeypatch, client)

    actual = ai_assistance._suggest_with_gemini("2 units O negative whole blood", None, None, "test-key")
    assert actual == result


def test_suggest_fields_endpoint_uses_server_key_and_returns_fields(monkeypatch, make_client):
    monkeypatch.setenv("GEMINI_API_KEY", "server-test-key")
    result = ai_assistance.SuggestedFields(
        blood_group="O-",
        component="whole_blood",
        quantity=2,
        quantity_unit="units",
        district="Imphal West",
        state="Manipur",
    )
    used_keys = []

    def fake_suggest(text, image, mime_type, api_key):
        used_keys.append(api_key)
        return result

    monkeypatch.setattr(ai_assistance, "_suggest_with_gemini", fake_suggest)
    client, _ = make_client()
    response = client.post(
        "/api/suggest-fields",
        json={"text": "Need 2 units O negative whole blood in Imphal West, Manipur"},
    )

    assert response.status_code == 200, response.text
    assert response.json() == result.model_dump(mode="json")
    assert used_keys == ["server-test-key"]


def test_suggestion_extractor_uses_fallback_model_after_transient_error(monkeypatch):
    result = ai_assistance.SuggestedFields(
        blood_group="B+",
        component="prbc",
        quantity=1,
        quantity_unit="units",
        district=None,
        state=None,
    )
    calls = []

    class ProviderError(RuntimeError):
        status_code = 503

    class Models:
        def generate_content(self, *, model, **kwargs):
            calls.append(model)
            if len(calls) == 1:
                raise ProviderError("temporary provider error")
            return SimpleNamespace(parsed=result, text="")

    client = SimpleNamespace(models=Models(), close=lambda: None)
    _install_fake_genai(monkeypatch, client)

    actual = ai_assistance._suggest_with_gemini("one unit B positive PRBC", None, None, "test-key")
    assert actual == result
    assert calls == [ai_assistance.PRIMARY_MODEL, ai_assistance.FALLBACK_MODEL]
