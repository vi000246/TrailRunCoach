import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from backend.engine.ai.client import get_ai_client, GEMINI_MODELS, GeminiClient


def test_gemini_models_listed():
    assert GEMINI_MODELS, "expected at least one Gemini model"
    assert any("gemini" in m for m in GEMINI_MODELS)


def test_get_ai_client_returns_gemini():
    c = get_ai_client("gemini", api_key="k", model="gemini-2.0-flash")
    assert isinstance(c, GeminiClient)
    assert c.model == "gemini-2.0-flash"
    # Gemini uses Google's OpenAI-compatible endpoint
    assert "generativelanguage.googleapis.com" in c.base_url


def test_unknown_provider_still_raises():
    import pytest
    with pytest.raises(ValueError):
        get_ai_client("bogus", api_key="k", model="x")
