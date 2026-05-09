"""Execute LLM calls using the customer's own API key.

Supports OpenAI and Anthropic. Zero cost to ModelWatch — the customer
pays their own provider bill.
"""

import time
from dataclasses import dataclass
from typing import Optional

import anthropic
import openai

from app.models.schemas import Provider


@dataclass
class LLMResponse:
    output_text: str
    latency_ms: int
    token_usage: dict  # {"prompt_tokens": int, "completion_tokens": int}
    error: Optional[str] = None


async def execute_openai(
    api_key: str,
    model: str,
    input_text: str,
    system_prompt: Optional[str] = None,
    temperature: float = 0.0,
    max_tokens: int = 1024,
    extra_params: dict | None = None,
) -> LLMResponse:
    """Call OpenAI chat completions."""
    client = openai.AsyncOpenAI(api_key=api_key)
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": input_text})

    params = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        **(extra_params or {}),
    }

    start = time.perf_counter_ns()
    try:
        resp = await client.chat.completions.create(**params)
        latency = (time.perf_counter_ns() - start) // 1_000_000
        return LLMResponse(
            output_text=resp.choices[0].message.content or "",
            latency_ms=latency,
            token_usage={
                "prompt_tokens": resp.usage.prompt_tokens if resp.usage else 0,
                "completion_tokens": resp.usage.completion_tokens if resp.usage else 0,
            },
        )
    except Exception as exc:
        latency = (time.perf_counter_ns() - start) // 1_000_000
        return LLMResponse(
            output_text="",
            latency_ms=latency,
            token_usage={"prompt_tokens": 0, "completion_tokens": 0},
            error=str(exc),
        )


async def execute_anthropic(
    api_key: str,
    model: str,
    input_text: str,
    system_prompt: Optional[str] = None,
    temperature: float = 0.0,
    max_tokens: int = 1024,
    extra_params: dict | None = None,
) -> LLMResponse:
    """Call Anthropic messages API."""
    client = anthropic.AsyncAnthropic(api_key=api_key)
    messages = [{"role": "user", "content": input_text}]

    params = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        **(extra_params or {}),
    }
    if system_prompt:
        params["system"] = system_prompt

    start = time.perf_counter_ns()
    try:
        resp = await client.messages.create(**params)
        latency = (time.perf_counter_ns() - start) // 1_000_000
        output = "".join(
            block.text for block in resp.content if hasattr(block, "text")
        )
        return LLMResponse(
            output_text=output,
            latency_ms=latency,
            token_usage={
                "prompt_tokens": resp.usage.input_tokens,
                "completion_tokens": resp.usage.output_tokens,
            },
        )
    except Exception as exc:
        latency = (time.perf_counter_ns() - start) // 1_000_000
        return LLMResponse(
            output_text="",
            latency_ms=latency,
            token_usage={"prompt_tokens": 0, "completion_tokens": 0},
            error=str(exc),
        )


async def execute(
    provider: Provider,
    api_key: str,
    model: str,
    input_text: str,
    system_prompt: Optional[str] = None,
    temperature: float = 0.0,
    max_tokens: int = 1024,
    extra_params: dict | None = None,
) -> LLMResponse:
    """Dispatch to the correct provider executor."""
    fn = execute_openai if provider == Provider.openai else execute_anthropic
    return await fn(
        api_key=api_key,
        model=model,
        input_text=input_text,
        system_prompt=system_prompt,
        temperature=temperature,
        max_tokens=max_tokens,
        extra_params=extra_params,
    )
