"""AI provider drivers (patch §4).

One external contract for every provider (`AIProviderBase`), with concrete
drivers for Ollama, LM Studio, OpenAI, Anthropic and DeepSeek. All network I/O
is async (httpx) so it never blocks the event loop / UI thread (patch §4.1).

Local providers (Ollama/LM Studio) attempt a real resource release after
generation only where the backend actually supports it (patch §4.2); we never
invent fake unload commands. Cloud providers just close request state (§4.3).
"""

from __future__ import annotations

import httpx

from app.config import logger
from app.models.ai_assistant_models import AIProvider, AIProviderSettings


class AIProviderError(Exception):
    """Provider failure with a user-readable message (patch §13)."""


class AIProviderBase:
    """Common contract implemented by every provider."""

    name = "base"

    def __init__(self, settings: AIProviderSettings) -> None:
        self.settings = settings

    async def generate_chat_completion(self, messages: list[dict]) -> str:
        raise NotImplementedError

    async def release_resources(self) -> dict:
        """Release/close provider resources. Returns a status dict describing
        what actually happened (honest — no fake unload)."""
        return {"released": False, "supports_unload": self.supports_explicit_unload(),
                "message": "No local resources to release for this provider."}

    def supports_explicit_unload(self) -> bool:
        return self.settings.supports_explicit_unload()

    # ---- shared helpers ---------------------------------------------------
    def _timeout(self) -> httpx.Timeout:
        return httpx.Timeout(float(self.settings.timeout_seconds), connect=10.0)

    def _raise_http(self, exc: Exception) -> "AIProviderError":
        if isinstance(exc, httpx.ConnectError):
            return AIProviderError(
                f"Provider is not reachable at {self.settings.effective_base_url()}. "
                "Check the Base URL and that the model server is running.")
        if isinstance(exc, httpx.TimeoutException):
            return AIProviderError(
                f"The AI request timed out after {self.settings.timeout_seconds}s. "
                "Increase the timeout or use a smaller/faster model.")
        return AIProviderError(f"Provider request failed: {exc}")


class _OpenAICompatibleProvider(AIProviderBase):
    """OpenAI-compatible /chat/completions (OpenAI, DeepSeek, LM Studio)."""

    name = "openai_compatible"

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        key = (self.settings.api_key or "").strip()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    async def generate_chat_completion(self, messages: list[dict]) -> str:
        if self.settings.requires_key() and not (self.settings.api_key or "").strip():
            raise AIProviderError("API key is missing for the selected provider.")
        url = f"{self.settings.effective_base_url()}/chat/completions"
        body = {
            "model": self.settings.effective_model(),
            "messages": messages,
            "temperature": self.settings.temperature,
            "max_tokens": self.settings.max_tokens,
            "stream": False,
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout()) as client:
                resp = await client.post(url, headers=self._headers(), json=body)
        except httpx.HTTPError as exc:
            raise self._raise_http(exc)
        if resp.status_code == 401:
            raise AIProviderError("Authentication failed — the API key was rejected.")
        if resp.status_code == 404:
            raise AIProviderError(
                f"Model '{self.settings.effective_model()}' or endpoint not found. "
                "Check the model name and Base URL.")
        if resp.status_code >= 400:
            raise AIProviderError(_error_detail(resp))
        try:
            data = resp.json()
            return (data["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, ValueError) as exc:
            raise AIProviderError(f"Unexpected response from provider: {exc}")


class OpenAIProvider(_OpenAICompatibleProvider):
    name = "openai"


class DeepSeekProvider(_OpenAICompatibleProvider):
    name = "deepseek"


class LMStudioProvider(_OpenAICompatibleProvider):
    name = "lmstudio"

    async def release_resources(self) -> dict:
        # LM Studio's local server exposes no explicit unload — close the request
        # (already closed by the context manager) and mark idle (patch §4.2).
        return {"released": False, "supports_unload": False,
                "message": "LM Studio does not support explicit unload; the local "
                           "server may keep the model in memory."}


class OllamaProvider(AIProviderBase):
    """Ollama native /api/chat, with real keep_alive-based unload (patch §4.2)."""

    name = "ollama"

    async def generate_chat_completion(self, messages: list[dict]) -> str:
        url = f"{self.settings.effective_base_url()}/api/chat"
        body = {
            "model": self.settings.effective_model(),
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": self.settings.temperature,
                "num_predict": self.settings.max_tokens,
            },
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout()) as client:
                resp = await client.post(url, json=body)
        except httpx.HTTPError as exc:
            raise self._raise_http(exc)
        if resp.status_code == 404:
            raise AIProviderError(
                f"Model '{self.settings.effective_model()}' is not available in Ollama. "
                "Pull it first (e.g. `ollama pull " + self.settings.effective_model() + "`).")
        if resp.status_code >= 400:
            raise AIProviderError(_error_detail(resp))
        try:
            data = resp.json()
            return (data.get("message", {}).get("content") or "").strip()
        except ValueError as exc:
            raise AIProviderError(f"Unexpected response from Ollama: {exc}")

    async def release_resources(self) -> dict:
        """Ask Ollama to unload the model now (keep_alive=0). This is a real,
        supported operation — not a fake command (patch §4.2)."""
        url = f"{self.settings.effective_base_url()}/api/generate"
        body = {"model": self.settings.effective_model(), "keep_alive": 0}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0)) as client:
                resp = await client.post(url, json=body)
            if resp.status_code >= 400:
                logger.info("Ollama unload returned %s (non-fatal).", resp.status_code)
                return {"released": False, "supports_unload": True,
                        "message": "Ollama did not confirm unload; the server may still "
                                   "hold the model briefly."}
            return {"released": True, "supports_unload": True,
                    "message": "Requested Ollama to unload the model (keep_alive=0)."}
        except httpx.HTTPError as exc:
            logger.info("Ollama unload failed (non-fatal): %s", exc)
            return {"released": False, "supports_unload": True,
                    "message": f"Could not reach Ollama to unload the model: {exc}"}


class AnthropicProvider(AIProviderBase):
    """Anthropic Messages API (patch §4.3 — cloud, no local VRAM to unload)."""

    name = "anthropic"

    async def generate_chat_completion(self, messages: list[dict]) -> str:
        if not (self.settings.api_key or "").strip():
            raise AIProviderError("API key is missing for the selected provider.")
        # Anthropic takes the system prompt as a top-level field, not a message.
        system_parts = [m["content"] for m in messages if m.get("role") == "system"]
        convo = [m for m in messages if m.get("role") != "system"]
        url = f"{self.settings.effective_base_url()}/messages"
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.settings.api_key.strip(),
            "anthropic-version": "2023-06-01",
        }
        body = {
            "model": self.settings.effective_model(),
            "max_tokens": self.settings.max_tokens,
            "temperature": self.settings.temperature,
            "messages": convo,
        }
        if system_parts:
            body["system"] = "\n\n".join(system_parts)
        try:
            async with httpx.AsyncClient(timeout=self._timeout()) as client:
                resp = await client.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise self._raise_http(exc)
        if resp.status_code == 401:
            raise AIProviderError("Authentication failed — the Anthropic API key was rejected.")
        if resp.status_code >= 400:
            raise AIProviderError(_error_detail(resp))
        try:
            data = resp.json()
            blocks = data.get("content") or []
            text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
            return text.strip()
        except (ValueError, AttributeError) as exc:
            raise AIProviderError(f"Unexpected response from Anthropic: {exc}")


_PROVIDERS: dict[str, type[AIProviderBase]] = {
    AIProvider.OLLAMA.value: OllamaProvider,
    AIProvider.LMSTUDIO.value: LMStudioProvider,
    AIProvider.OPENAI.value: OpenAIProvider,
    AIProvider.ANTHROPIC.value: AnthropicProvider,
    AIProvider.DEEPSEEK.value: DeepSeekProvider,
}


def get_provider(settings: AIProviderSettings) -> AIProviderBase:
    cls = _PROVIDERS.get(settings.provider.value)
    if cls is None:
        raise AIProviderError(f"Unknown AI provider: {settings.provider}")
    return cls(settings)


def _error_detail(resp: httpx.Response) -> str:
    try:
        data = resp.json()
        if isinstance(data, dict):
            err = data.get("error")
            if isinstance(err, dict) and err.get("message"):
                return f"Provider error ({resp.status_code}): {err['message']}"
            if isinstance(err, str):
                return f"Provider error ({resp.status_code}): {err}"
            if data.get("message"):
                return f"Provider error ({resp.status_code}): {data['message']}"
    except ValueError:
        pass
    return f"Provider returned HTTP {resp.status_code}."
