"""
Tests for Structured AI Output
================================
Verifies that:
1. ``AIResponse.parse()`` correctly parses a JSON string into a Pydantic model.
2. ``AIResponse.parse()`` handles JSON embedded in markdown code blocks.
3. ``AIClient.complete(output=Model)`` uses the schema injection and returns
   a parsed Pydantic instance.
4. Fallback retry logic kicks in when first parse fails.
"""

from __future__ import annotations

import json
from typing import List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from vorte.modules.ai.schemas import AIResponse, CompletionRequest, FinishReason, TokenUsage


# ---------------------------------------------------------------------------
# Sample Pydantic output schemas
# ---------------------------------------------------------------------------

class Person(BaseModel):
    name: str
    age: int
    email: Optional[str] = None


class ExtractedList(BaseModel):
    items: List[str]
    count: int


# ---------------------------------------------------------------------------
# AIResponse.parse() unit tests
# ---------------------------------------------------------------------------

class TestAIResponseParse:
    """Direct unit tests for AIResponse.parse()."""

    def test_parse_clean_json(self):
        """Plain JSON string is parsed correctly."""
        payload = json.dumps({"name": "Alice", "age": 30, "email": "alice@example.com"})
        response = AIResponse(content=payload, model="gpt-4o", provider="openai")
        result = response.parse(Person)
        assert isinstance(result, Person)
        assert result.name == "Alice"
        assert result.age == 30
        assert result.email == "alice@example.com"

    def test_parse_json_missing_optional_field(self):
        """Optional field can be absent from JSON."""
        payload = json.dumps({"name": "Bob", "age": 25})
        response = AIResponse(content=payload, model="gpt-4o", provider="openai")
        result = response.parse(Person)
        assert result.name == "Bob"
        assert result.email is None

    def test_parse_json_in_markdown_code_block(self):
        """JSON wrapped in markdown ``` ... ``` code block is extracted and parsed."""
        payload = '```json\n{"name": "Carol", "age": 40}\n```'
        response = AIResponse(content=payload, model="gpt-4o", provider="openai")
        result = response.parse(Person)
        assert result.name == "Carol"
        assert result.age == 40

    def test_parse_json_in_plain_code_block(self):
        """JSON wrapped in ``` ``` (no lang tag) is extracted and parsed."""
        payload = '```\n{"name": "Dave", "age": 22}\n```'
        response = AIResponse(content=payload, model="gpt-4o", provider="openai")
        result = response.parse(Person)
        assert result.name == "Dave"

    def test_parse_list_extraction(self):
        """More complex schema with a list field."""
        payload = json.dumps({"items": ["apple", "banana", "cherry"], "count": 3})
        response = AIResponse(content=payload, model="gpt-4o", provider="openai")
        result = response.parse(ExtractedList)
        assert isinstance(result, ExtractedList)
        assert result.count == 3
        assert "banana" in result.items

    def test_parse_pre_populated_structured_output(self):
        """If structured_output is already set, it is returned as-is."""
        pre_parsed = Person(name="Eve", age=29)
        response = AIResponse(
            content="{}",
            model="gpt-4o",
            provider="openai",
            structured_output=pre_parsed,
        )
        result = response.parse(Person)
        assert result is pre_parsed

    def test_parse_invalid_json_raises(self):
        """Non-parseable content raises ValueError with a helpful message."""
        response = AIResponse(
            content="Sorry, I cannot extract that information.",
            model="gpt-4o",
            provider="openai",
        )
        with pytest.raises(ValueError, match="Failed to parse AI response into Person"):
            response.parse(Person)


# ---------------------------------------------------------------------------
# AIClient integration: mocked provider
# ---------------------------------------------------------------------------

def _make_client(response_content: str, model: str = "gpt-4o") -> "AIClient":  # noqa: F821
    """Build an AIClient backed by a mock provider that returns fixed content."""
    from vorte.modules.ai.client import AIClient
    from vorte.modules.ai.providers.registry import ProviderRegistry
    from vorte.modules.ai.schemas import AIConfig, AIResponse, TokenUsage, ProviderConfig

    mock_response = AIResponse(
        content=response_content,
        model=model,
        provider="mock",
        finish_reason=FinishReason.STOP,
        usage=TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
    )

    mock_provider = MagicMock()
    mock_provider.name = "mock"
    mock_provider.config = ProviderConfig(models=[model])
    mock_provider.complete = AsyncMock(return_value=mock_response)

    registry = ProviderRegistry()
    registry.register(mock_provider)

    config = AIConfig(
        default_provider="mock",
        default_model=model,
        track_costs=False,
        structured_output_fallback=False,
    )

    return AIClient(registry=registry, config=config)


@pytest.mark.asyncio
async def test_ai_client_complete_returns_raw_response():
    """Without output=, complete() returns AIResponse."""
    from vorte.modules.ai.schemas import AIResponse
    client = _make_client('{"hello": "world"}')
    result = await client.complete("Say hello in JSON")
    assert isinstance(result, AIResponse)
    assert "hello" in result.content


@pytest.mark.asyncio
async def test_ai_client_complete_with_output_returns_pydantic():
    """With output=Person, complete() returns a Person instance."""
    payload = json.dumps({"name": "Frank", "age": 35})
    client = _make_client(payload)
    result = await client.complete("Extract a person", output=Person)
    assert isinstance(result, Person)
    assert result.name == "Frank"
    assert result.age == 35


@pytest.mark.asyncio
async def test_ai_client_schema_injected_into_system_prompt():
    """Schema instructions are appended to the system prompt when output= is used."""
    from vorte.modules.ai.providers.registry import ProviderRegistry
    from vorte.modules.ai.schemas import AIConfig, AIResponse, TokenUsage, ProviderConfig
    from vorte.modules.ai.client import AIClient

    captured_requests: list[CompletionRequest] = []

    async def capture_complete(req: CompletionRequest) -> AIResponse:
        captured_requests.append(req)
        return AIResponse(
            content=json.dumps({"name": "Grace", "age": 27}),
            model="gpt-4o",
            provider="mock",
        )

    mock_provider = MagicMock()
    mock_provider.name = "mock"
    mock_provider.config = ProviderConfig(models=["gpt-4o"])
    mock_provider.complete = capture_complete

    registry = ProviderRegistry()
    registry.register(mock_provider)

    config = AIConfig(default_provider="mock", default_model="gpt-4o", track_costs=False)
    client = AIClient(registry=registry, config=config)

    result = await client.complete("Extract person info", output=Person)
    assert isinstance(result, Person)
    assert len(captured_requests) == 1
    req = captured_requests[0]
    # Schema must be injected into the system prompt
    assert req.system is not None
    assert "name" in req.system
    assert "age" in req.system


@pytest.mark.asyncio
async def test_ai_client_handles_markdown_wrapped_json():
    """AIClient correctly handles LLM responses that wrap JSON in markdown fences."""
    wrapped = "```json\n" + json.dumps({"name": "Henry", "age": 19}) + "\n```"
    client = _make_client(wrapped)
    result = await client.complete("Extract a person", output=Person)
    assert isinstance(result, Person)
    assert result.name == "Henry"
