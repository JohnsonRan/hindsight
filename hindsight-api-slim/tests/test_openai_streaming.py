"""Exercise extra_body.stream through the real OpenAI SDK and an HTTP SSE endpoint."""

import asyncio
import json
from typing import Any

import pytest
from aiohttp import web
from pydantic import BaseModel

from hindsight_api.engine.llm_interface import LLMInterface, OutputTooLongError
from hindsight_api.engine.llm_trace import current_response_usage
from hindsight_api.engine.providers.openai_compatible_llm import OpenAICompatibleLLM, ProviderResponseError
from hindsight_api.engine.providers.openai_responses_llm import OpenAIResponsesLLM, RetryableResponsesError
from tests.aiohttp_stub import stub_server

pytestmark = pytest.mark.asyncio
PROVIDERS = ["openai", "openai-responses"]
MESSAGES = [{"role": "user", "content": "Answer the question"}]


class Answer(BaseModel):
    answer: str


def _llm(provider: str, url: str, **kwargs: Any) -> LLMInterface:
    cls = OpenAIResponsesLLM if provider == "openai-responses" else OpenAICompatibleLLM
    return cls(provider=provider, api_key="test", base_url=f"{url}/v1", model="test-model", **kwargs)


def _events(provider: str, content: str = "hello", *, truncated: bool = False) -> list[dict[str, Any]]:
    if provider == "openai":
        base = {"id": "chatcmpl-test", "object": "chat.completion.chunk", "created": 1, "model": "test-model"}
        return [
            {**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": content[:3]}}]},
            {
                **base,
                "choices": [
                    {"index": 0, "delta": {"content": content[3:]}, "finish_reason": "length" if truncated else "stop"}
                ],
            },
            {
                **base,
                "choices": [],
                "usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 9,
                    "total_tokens": 29,
                    "prompt_tokens_details": {"cached_tokens": 4},
                    "completion_tokens_details": {"reasoning_tokens": 3},
                },
            },
        ]
    response = {
        "id": "resp-test",
        "object": "response",
        "created_at": 1,
        "model": "test-model",
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "tools": [],
        "status": "incomplete" if truncated else "completed",
        "incomplete_details": {"reason": "max_output_tokens"} if truncated else None,
        "output": [
            {
                "id": "msg-test",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": content, "annotations": []}],
            }
        ],
        "usage": {
            "input_tokens": 20,
            "output_tokens": 9,
            "total_tokens": 29,
            "input_tokens_details": {"cached_tokens": 4},
            "output_tokens_details": {"reasoning_tokens": 3},
        },
    }
    return [
        {"type": "response.output_text.delta", "delta": content[:3]},
        {"type": "response.output_text.delta", "delta": content[3:]},
        {"type": "response.incomplete" if truncated else "response.completed", "response": response},
    ]


def _sse(events: list[dict[str, Any]]) -> str:
    return "".join(f"data: {json.dumps(event)}\n\n" for event in events) + "data: [DONE]\n\n"


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("mode", ["text", "soft", "strict", "skip"])
async def test_stream_preserves_content_schema_and_usage(provider: str, mode: str) -> None:
    content = "hello" if mode == "text" else '{"answer":"hello"}'
    extra_body = {"stream": True, "top_p": 0.9}

    async def handler(request: web.Request) -> web.Response:
        body = await request.json()
        assert request.path == ("/v1/responses" if provider == "openai-responses" else "/v1/chat/completions")
        assert body["stream"] is True
        assert body["top_p"] == 0.9
        if provider == "openai":
            assert body["stream_options"] == {"include_usage": True}
        return web.Response(text=_sse(_events(provider, content)), content_type="text/event-stream")

    async with stub_server(handler) as url:
        llm = _llm(provider, url, extra_body=extra_body)
        try:
            result = await llm.call(
                MESSAGES,
                response_format=None if mode == "text" else Answer,
                strict_schema=mode == "strict",
                skip_validation=mode == "skip",
                max_retries=0,
            )
            assert result.content == (
                "hello" if mode == "text" else {"answer": "hello"} if mode == "skip" else Answer(answer="hello")
            )
            assert result.usage.input_tokens == 20
            assert result.usage.output_tokens == 6
            assert result.usage.total_tokens == 26
            assert result.usage.cached_tokens == 4
            assert result.usage.thoughts_tokens == 3
            assert current_response_usage().output_tokens == 6
            assert extra_body == {"stream": True, "top_p": 0.9}
        finally:
            await llm.cleanup()


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_stream_truncation_is_not_retried_or_repaired(provider: str) -> None:
    calls = 0

    async def handler(request: web.Request) -> web.Response:
        nonlocal calls
        calls += 1
        return web.Response(
            text=_sse(_events(provider, '{"answer":', truncated=True)), content_type="text/event-stream"
        )

    async with stub_server(handler) as url:
        llm = _llm(provider, url, extra_body={"stream": True})
        try:
            with pytest.raises(OutputTooLongError):
                await llm.call(MESSAGES, response_format=Answer, max_retries=2, initial_backoff=0)
            assert calls == 1
            assert current_response_usage().input_tokens == 20
        finally:
            await llm.cleanup()


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("failure", ["empty", "unfinished", "error", "disconnect"])
async def test_broken_stream_retries_the_whole_request(provider: str, failure: str) -> None:
    calls = 0

    async def handler(request: web.Request) -> web.StreamResponse:
        nonlocal calls
        calls += 1
        events = _events(provider)
        if calls == 1:
            if failure == "disconnect":
                response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
                await response.prepare(request)
                await response.write(f"data: {json.dumps(events[0])}\n\n".encode())
                request.transport.close()
                return response
            events = [] if failure == "empty" else events[:1]
            if failure == "error":
                events = [{"error": {"message": "upstream failed", "code": "server_error"}}]
        return web.Response(text=_sse(events), content_type="text/event-stream")

    async with stub_server(handler) as url:
        llm = _llm(provider, url, extra_body={"stream": True})
        try:
            assert (await llm.call(MESSAGES, max_retries=1, initial_backoff=0)).content == "hello"
            assert calls == 2
        finally:
            await llm.cleanup()


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_unfinished_stream_fails_when_retry_budget_is_spent(provider: str) -> None:
    async def handler(request: web.Request) -> web.Response:
        return web.Response(text=_sse(_events(provider)[:1]), content_type="text/event-stream")

    async with stub_server(handler) as url:
        llm = _llm(provider, url, extra_body={"stream": True})
        try:
            with pytest.raises((ProviderResponseError, RetryableResponsesError)):
                await llm.call(MESSAGES, max_retries=0)
        finally:
            await llm.cleanup()


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_stream_consumption_keeps_wall_clock_timeout(provider: str) -> None:
    async def handler(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        for _ in range(20):
            await response.write(b": keepalive\n\n")
            await asyncio.sleep(0.05)
        return response

    async with stub_server(handler) as url:
        llm = _llm(provider, url, extra_body={"stream": True}, timeout=0.2)
        try:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(llm.call(MESSAGES, max_retries=0), timeout=5)
        finally:
            await llm.cleanup()


async def test_responses_stream_selects_final_answer_instead_of_joining_messages() -> None:
    events = _events("openai-responses", "final answer")
    final = events[-1]["response"]["output"][0]
    final["phase"] = "final_answer"
    commentary = {
        **final,
        "id": "msg-commentary",
        "phase": "commentary",
        "content": [{"type": "output_text", "text": "preamble", "annotations": []}],
    }
    events[-1]["response"]["output"].insert(0, commentary)

    async def handler(request: web.Request) -> web.Response:
        return web.Response(text=_sse(events), content_type="text/event-stream")

    async with stub_server(handler) as url:
        llm = _llm("openai-responses", url, extra_body={"stream": True})
        try:
            assert (await llm.call(MESSAGES, max_retries=0)).content == "final answer"
        finally:
            await llm.cleanup()


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_usage_is_optional_and_chat_usage_request_can_be_disabled(provider: str) -> None:
    events = _events(provider)
    if provider == "openai":
        events.pop()
    else:
        events[-1]["response"]["usage"] = None

    async def handler(request: web.Request) -> web.Response:
        body = await request.json()
        if provider == "openai":
            assert body["stream_options"] == {"include_usage": False}
        return web.Response(text=_sse(events), content_type="text/event-stream")

    extra_body = {"stream": True}
    if provider == "openai":
        extra_body["stream_options"] = {"include_usage": False}
    async with stub_server(handler) as url:
        llm = _llm(provider, url, extra_body=extra_body)
        try:
            result = await llm.call(MESSAGES, max_retries=0)
            assert result.content == "hello"
            assert result.usage.total_tokens == 0
        finally:
            await llm.cleanup()


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_stream_false_keeps_non_streaming_transport(provider: str) -> None:
    async def handler(request: web.Request) -> web.Response:
        body = await request.json()
        assert body["stream"] is False
        assert "stream_options" not in body
        events = _events(provider)
        if provider == "openai":
            completion = {
                **events[0],
                "object": "chat.completion",
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": "hello"}, "finish_reason": "stop"}
                ],
                "usage": events[-1]["usage"],
            }
        else:
            completion = events[-1]["response"]
        return web.json_response(completion)

    async with stub_server(handler) as url:
        llm = _llm(provider, url, extra_body={"stream": False})
        try:
            assert (await llm.call(MESSAGES, max_retries=0)).content == "hello"
        finally:
            await llm.cleanup()
