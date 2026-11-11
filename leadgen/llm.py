"""Model-agnostic LLM access.

Two transports cover practically every model:

  anthropic           Claude models through the official `anthropic` SDK.
  openai_compatible   Any server that speaks the OpenAI Chat Completions format:
                      OpenAI, Azure OpenAI, Google Gemini (its /openai endpoint),
                      Groq, OpenRouter, Mistral, DeepSeek, Together, xAI,
                      Ollama / LM Studio / vLLM (local), ...

The bot only ever calls `complete_json(system, user)` or `complete_text(...)`,
so switching model or vendor is a config change, not a code change.
"""

from __future__ import annotations

import json
import re
import time
from typing import Protocol

import requests

PROVIDERS = ("anthropic", "openai_compatible")


class LLMError(RuntimeError):
    pass


class LLM(Protocol):
    provider: str
    model: str

    def complete_text(self, system: str, user: str, max_tokens: int = 1200) -> str: ...


def extract_json(text: str) -> dict:
    """Parse a JSON object out of a model reply, tolerating ```json fences and chatter."""
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", cleaned, re.S)
    if fence:
        cleaned = fence.group(1)
    try:
        return json.loads(cleaned)
    except ValueError:
        pass
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(cleaned[start:end + 1])
        except ValueError as exc:
            raise LLMError(f"model did not return valid JSON: {exc}") from exc
    raise LLMError("model did not return a JSON object")


class AnthropicLLM:
    provider = "anthropic"

    def __init__(self, api_key: str, model: str, timeout: float = 90.0):
        if not api_key:
            raise LLMError("LLM_API_KEY (or ANTHROPIC_API_KEY) is not set")
        import anthropic  # official SDK
        self.model = model
        self.client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=3)

    def complete_text(self, system: str, user: str, max_tokens: int = 1200) -> str:
        try:
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        except Exception as exc:  # SDK raises typed errors; surface them uniformly
            raise LLMError(f"Anthropic request failed: {exc}") from exc
        return "".join(b.text for b in resp.content if b.type == "text")


class OpenAICompatibleLLM:
    provider = "openai_compatible"

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 90.0,
                 session: requests.Session | None = None):
        if not base_url:
            raise LLMError("llm.base_url is required for the openai_compatible provider")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.session = session or requests.Session()

    def _post(self, body: dict) -> str:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        last = None
        for attempt in range(4):
            try:
                resp = self.session.post(f"{self.base_url}/chat/completions", headers=headers,
                                         json=body, timeout=self.timeout)
            except requests.RequestException as exc:
                last = f"connection error: {exc}"
                time.sleep(2 ** attempt)
                continue
            if resp.status_code in (429, 500, 502, 503):
                last = f"HTTP {resp.status_code}: {resp.text[:200]}"
                time.sleep(3 * (attempt + 1))
                continue
            if resp.status_code != 200:
                raise LLMError(f"LLM HTTP {resp.status_code}: {resp.text[:300]}")
            try:
                return resp.json()["choices"][0]["message"]["content"] or ""
            except (KeyError, IndexError, ValueError) as exc:
                raise LLMError(f"unexpected LLM response shape: {exc}") from exc
        raise LLMError(f"LLM request failed after retries: {last}")

    def complete_text(self, system: str, user: str, max_tokens: int = 1200) -> str:
        return self._post({
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_tokens": max_tokens,
            "temperature": 0.2,
        })


def complete_json(llm: LLM, system: str, user: str, max_tokens: int = 1200) -> dict:
    """Ask for JSON and parse it; one retry with a stricter instruction if parsing fails."""
    text = llm.complete_text(system, user, max_tokens)
    try:
        return extract_json(text)
    except LLMError:
        text = llm.complete_text(
            system, user + "\n\nReturn ONLY a single valid JSON object. No prose.", max_tokens)
        return extract_json(text)


def build_llm(provider: str, model: str, base_url: str, api_key: str) -> LLM:
    if provider == "anthropic":
        return AnthropicLLM(api_key, model)
    if provider == "openai_compatible":
        return OpenAICompatibleLLM(base_url, api_key, model)
    raise LLMError(f"unknown llm.provider '{provider}' (use one of {', '.join(PROVIDERS)})")
