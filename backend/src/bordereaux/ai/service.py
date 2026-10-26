"""Optional LLM layer.

The platform follows the project principle of "deterministic first, AI second".
The AI layer is intentionally isolated and disabled by default so the core
workflow continues to work without any external model configuration.
"""

from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from bordereaux.config import Settings, get_settings
from bordereaux.core.exceptions import LLMError

_DEFAULT_SYSTEM_PROMPT = (
    "You are an interpretation assistant for a reinsurance bordereaux workflow. "
    "Use only the provided evidence; never change the underlying financial state, "
    "never fabricate a mapping or a discrepancy outcome, and return a compact JSON object. "
    "Explain your reasoning briefly and keep the answer grounded in the supplied context."
)


class LLMProvider(ABC):
    """Pluggable LLM provider interface."""

    @abstractmethod
    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.0,
        **kwargs: Any,
    ) -> str:
        """Return the raw model output as a string."""


class NoOpLLMProvider(LLMProvider):
    """Default provider used when LLM is disabled or not configured."""

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.0,
        **kwargs: Any,
    ) -> str:
        return ""


class OpenAICompatibleProvider(LLMProvider):
    """HTTP-based provider compatible with Anthropic or OpenAI-style APIs.

    This keeps the integration pluggable without forcing a vendor dependency into
    the application runtime. Any model behind an OpenAI-compatible endpoint can be
    configured via environment variables.
    """

    @staticmethod
    def _classify_http_error(status_code: int | None, detail: str) -> tuple[str, str]:
        text = detail.lower()
        if status_code == 429 or "rate limit" in text or "quota" in text or "exhausted" in text:
            return (
                "rate_limited",
                "The LLM API is currently rate-limited or quota-exhausted. Please retry later or switch to a different model/key.",
            )
        if status_code in (401, 403) or "invalid api" in text or "api key" in text or "authentication" in text:
            return (
                "invalid_api_key",
                "The LLM API key is invalid or missing permissions. Update the key in the environment and retry.",
            )
        if status_code == 400 or "invalid request" in text:
            return (
                "invalid_request",
                "The request to the LLM provider was rejected because the model request was malformed or unsupported.",
            )
        if status_code is None or status_code >= 500 or "temporar" in text or "timeout" in text or "unreachable" in text:
            return (
                "provider_unavailable",
                "The LLM provider is currently unavailable. The deterministic pipeline remains active, but AI-assisted hints are temporarily disabled.",
            )
        return (
            "provider_error",
            "The LLM provider returned an error. Please retry the request or review the configured provider settings.",
        )

    _DEFAULT_BASE_URLS = {
        "openai": "https://api.openai.com/v1",
        "anthropic": "https://api.anthropic.com",
        "openai_compatible": "https://api.openai.com/v1",
        "generic": "https://api.openai.com/v1",
    }

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str | None,
        provider: str = "openai",
        base_url: str | None = None,
        timeout_seconds: int = 30,
        api_version: str | None = None,
    ) -> None:
        self.provider = (provider or "openai").lower()
        self.api_key = api_key
        self.model = model or self._default_model_for_provider(self.provider)
        self.base_url = (base_url or self._DEFAULT_BASE_URLS.get(self.provider, self._DEFAULT_BASE_URLS["generic"]))
        self.api_version = api_version or self._default_api_version_for_provider(self.provider)
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _default_model_for_provider(provider: str) -> str:
        return {
            "openai": "gpt-4o-mini",
            "anthropic": "claude-sonnet-4-20250514",
            "openai_compatible": "gpt-4o-mini",
            "generic": "gpt-4o-mini",
            "azure": "gpt-5-mini-2025-08-07",
            "azure_openai": "gpt-5-mini-2025-08-07",
            "dial": "gpt-5-mini-2025-08-07",
        }.get(provider, "gpt-4o-mini")

    @staticmethod
    def _default_api_version_for_provider(provider: str) -> str | None:
        if provider in {"azure", "azure_openai", "dial"}:
            return "2025-04-01-preview"
        return None

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.0,
        **kwargs: Any,
    ) -> str:
        if not self.api_key:
            raise LLMError(
                "LLM is enabled but no API key was provided. Configure LLM_API_KEY before using AI-assisted interpretation.",
                details={"provider": self.provider, "model": self.model},
            )
        if not self.model:
            raise LLMError(
                "LLM model is missing. Set LLM_MODEL or configure the provider default.",
                details={"provider": self.provider},
            )

        endpoint, headers, payload = self._build_request(prompt, system=system, max_tokens=max_tokens, temperature=temperature)
        request = Request(endpoint, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except HTTPError as exc:  # pragma: no cover - network call path, not unit-tested here
            status_code = getattr(exc, "code", None)
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            kind, message = self._classify_http_error(status_code, detail)
            raise LLMError(message, details={"status_code": status_code, "kind": kind, "provider": self.provider, "model": self.model}) from exc
        except URLError as exc:  # pragma: no cover - network call path, not unit-tested here
            raise LLMError(
                "The LLM provider is unreachable. Check network access and the configured endpoint before retrying.",
                details={"kind": "provider_unavailable", "provider": self.provider, "model": self.model, "error": str(exc.reason)},
            ) from exc

        response_json = json.loads(body)
        return self._extract_text(response_json)

    def _build_request(
        self,
        prompt: str,
        *,
        system: str | None,
        max_tokens: int | None,
        temperature: float,
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        if self.provider == "anthropic":
            endpoint = f"{self.base_url.rstrip('/')}/v1/messages"
            headers = {
                "Content-Type": "application/json",
                "x-api-key": self.api_key or "",
                "anthropic-version": "2023-06-01",
            }
            payload = {
                "model": self.model,
                "max_tokens": max_tokens or 2000,
                "temperature": temperature,
                "system": system or _DEFAULT_SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": prompt}],
            }
            return endpoint, headers, payload

        if self.provider in {"azure", "azure_openai", "dial"}:
            base = self.base_url.rstrip("/")
            endpoint = f"{base}/openai/deployments/{self.model}/chat/completions"
            if self.api_version:
                endpoint = f"{endpoint}?api-version={quote(self.api_version)}"
            headers = {
                "Content-Type": "application/json",
                "Api-Key": self.api_key or "",
            }
            messages: list[dict[str, str]] = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            payload = {
                "messages": messages,
                "max_completion_tokens": max_tokens or 2000,
            }
            if temperature != 0:
                payload["temperature"] = temperature
            return endpoint, headers, payload

        endpoint = f"{self.base_url.rstrip('/')}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key or ''}",
        }
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens or 2000,
        }
        return endpoint, headers, payload

    @staticmethod
    def _extract_text(response_json: dict[str, Any]) -> str:
        if isinstance(response_json.get("choices"), list) and response_json["choices"]:
            choice = response_json["choices"][0]
            message = choice.get("message", {})
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    return "\n".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
        if isinstance(response_json.get("content"), list) and response_json["content"]:
            first = response_json["content"][0]
            if isinstance(first, dict):
                text = first.get("text")
                if isinstance(text, str):
                    return text
        raise LLMError("LLM provider returned an unexpected response shape. The model must return structured text or JSON.")


def build_llm_provider(settings: Settings | None = None) -> LLMProvider:
    settings = settings or get_settings()
    if not settings.llm_enabled or settings.llm_provider in {"disabled", "off", "none"}:
        return NoOpLLMProvider()
    if settings.llm_provider in {"mock", "noop"}:
        return NoOpLLMProvider()
    return OpenAICompatibleProvider(
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        provider=settings.llm_provider,
        base_url=settings.llm_base_url,
        timeout_seconds=settings.llm_timeout_seconds,
        api_version=settings.llm_api_version,
    )


class LLMService:
    """Thin application facade around the configured provider."""

    def __init__(self, provider: LLMProvider | None = None, *, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.provider = provider or build_llm_provider(self.settings)

    def is_available(self) -> bool:
        return (
            self.settings.llm_enabled
            and self.settings.llm_provider not in {"disabled", "off", "none"}
            and (self.settings.llm_provider in {"mock", "noop"} or bool(self.settings.llm_api_key or self.settings.llm_model))
        )

    @staticmethod
    def _compact_context(context: Any) -> str:
        return json.dumps(context, ensure_ascii=False, default=str, indent=2, sort_keys=True)

    def _build_prompt(self, question: str, context: Any) -> str:
        return (
            "Question:\n"
            f"{question}\n\n"
            "Context:\n"
            f"{self._compact_context(context)}\n\n"
            "Instruction:\n"
            "- Interpret the evidence, not the raw file contents.\n"
            "- Do not alter or approve a financial decision.\n"
            "- Return concise JSON with keys: verdict, explanation, recommendation, confidence.\n"
            "- Confidence is a value between 0.0 and 1.0.\n"
            "- If the data is insufficient, say so clearly and offer a safer fallback."
        )

    def answer(self, question: str, context: Any) -> dict[str, Any]:
        if not self.is_available():
            return {
                "status": "disabled",
                "message": (
                    "LLM is not configured. Set LLM_ENABLED=true, choose a provider/model, and provide "
                    "LLM_API_KEY before enabling semantic AI hints. The deterministic workflow will continue without AI."
                ),
            }

        prompt = self._build_prompt(question, context)

        call = self.provider.complete(
            prompt,
            system=self.settings.llm_system_prompt or _DEFAULT_SYSTEM_PROMPT,
            max_tokens=self.settings.llm_max_tokens,
            temperature=0.0,
        )
        try:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                response = asyncio.run(call)
            else:
                with ThreadPoolExecutor(max_workers=1) as executor:
                    response = executor.submit(asyncio.run, call).result()
        except LLMError as exc:
            details = exc.details if getattr(exc, "details", None) else []
            payload = {"status": "error", "message": exc.message, "details": details}
            if details and isinstance(details, list):
                first = details[0]
                if isinstance(first, dict) and first.get("kind"):
                    payload["status"] = first["kind"]
            return payload

        try:
            payload = json.loads(response)
        except json.JSONDecodeError:
            payload = {"answer": response}

        return {
            "status": "ok",
            "model": self.settings.llm_model,
            "provider": self.settings.llm_provider,
            "answer": payload,
        }


def build_llm_service(settings: Settings | None = None) -> LLMService:
    settings = settings or get_settings()
    return LLMService(settings=settings)
