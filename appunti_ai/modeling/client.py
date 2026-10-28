"""Interfaccia comune e registry degli adapter modello."""

from __future__ import annotations

import base64
import json
import os
import time
from urllib.parse import urlsplit
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .profiles import ModelProfile, ProfileError


@dataclass(frozen=True)
class ModelRequest:
    messages: list[dict[str, Any]]
    required_capabilities: frozenset[str] = frozenset({"text"})
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelResponse:
    content: str
    done_reason: str | None
    usage: dict[str, Any] | None
    duration_seconds: float
    provider: str
    model: str
    raw_metadata: dict[str, Any] = field(default_factory=dict)


class ModelClient(Protocol):
    profile: ModelProfile

    def generate(self, request: ModelRequest) -> ModelResponse:
        ...


def _merge_options(profile: ModelProfile, request: ModelRequest) -> dict[str, Any]:
    options = dict(profile.options)
    options.update(request.options)
    return options


class OllamaClient:
    def __init__(self, profile: ModelProfile):
        self.profile = profile
        try:
            import ollama
        except ImportError as exc:
            raise ProfileError("Il backend Ollama richiede il pacchetto Python ollama.") from exc
        self._ollama = ollama

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.profile.require(request.required_capabilities)
        started = time.perf_counter()
        kwargs: dict[str, Any] = {
            "model": self.profile.model,
            "messages": request.messages,
            "options": _merge_options(self.profile, request),
        }
        response = self._ollama.chat(**kwargs)
        message = getattr(response, "message", None)
        content = getattr(message, "content", "") or ""
        done_reason = getattr(response, "done_reason", None)
        usage = {
            key: getattr(response, key)
            for key in ("prompt_eval_count", "eval_count")
            if getattr(response, key, None) is not None
        }
        return ModelResponse(
            content=content,
            done_reason=done_reason,
            usage=usage or None,
            duration_seconds=time.perf_counter() - started,
            provider="ollama",
            model=self.profile.model,
        )


class OpenAICompatibleClient:
    """Minimal adapter per endpoint compatibili con /v1/chat/completions."""

    def __init__(self, profile: ModelProfile, allow_remote: bool):
        if not allow_remote:
            raise ProfileError(
                f"Il profilo remoto {profile.name!r} richiede opt-in esplicito."
            )
        self.profile = profile
        endpoint = profile.endpoint or os.environ.get(profile.endpoint_env or "")
        if not endpoint:
            raise ProfileError(f"Endpoint non disponibile per il profilo {profile.name!r}.")
        self.endpoint = endpoint.rstrip("/") + "/chat/completions"
        self.api_key = os.environ.get(profile.api_key_env or "") if profile.api_key_env else None

    @staticmethod
    def _encode_images(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        encoded: list[dict[str, Any]] = []
        for message in messages:
            copied = dict(message)
            images = copied.pop("images", [])
            if images:
                parts: list[dict[str, Any]] = [{"type": "text", "text": copied.get("content", "")}]
                for image in images:
                    path = Path(image)
                    mime = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
                    data = base64.b64encode(path.read_bytes()).decode("ascii")
                    parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}})
                copied["content"] = parts
            encoded.append(copied)
        return encoded

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.profile.require(request.required_capabilities)
        options = _merge_options(self.profile, request)
        timeout = float(options.pop("timeout", 180))
        payload = {
            "model": self.profile.model,
            "messages": self._encode_images(request.messages),
            **options,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        started = time.perf_counter()
        try:
            with urlopen(
                Request(self.endpoint, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"),
                timeout=timeout,
            ) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:1000]
            except OSError:
                detail = str(exc)
            raise ProfileError(f"Errore provider remoto {self.profile.name!r}: HTTP {exc.code}: {detail}") from exc
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            raise ProfileError(f"Errore provider remoto {self.profile.name!r}: {exc}") from exc
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content") or ""
        return ModelResponse(
            content=content if isinstance(content, str) else str(content),
            done_reason=choice.get("finish_reason"),
            usage=data.get("usage"),
            duration_seconds=time.perf_counter() - started,
            provider="openai_compatible",
            model=self.profile.model,
        )


class AzureOpenAIClient(OpenAICompatibleClient):
    """Adapter nativo Azure OpenAI Chat Completions senza SDK aggiuntivo."""

    def __init__(self, profile: ModelProfile, allow_remote: bool):
        super().__init__(profile, allow_remote)
        if not profile.deployment or not profile.api_version:
            raise ProfileError("Un profilo azure_openai richiede deployment e api_version.")
        configured_endpoint = profile.endpoint or os.environ.get(profile.endpoint_env or "")
        parsed = urlsplit(configured_endpoint)
        if not parsed.scheme or not parsed.netloc:
            raise ProfileError("L'endpoint Azure OpenAI non è un URL valido.")
        # Accept both the resource root and URLs copied from Azure's sample
        # code (which may already contain /openai/... and a query string).
        base = f"{parsed.scheme}://{parsed.netloc}"
        self.endpoint = f"{base}/openai/deployments/{profile.deployment}/chat/completions?api-version={profile.api_version}"
        self.api_key = os.environ.get(profile.api_key_env or "") if profile.api_key_env else None

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.profile.require(request.required_capabilities)
        options = _merge_options(self.profile, request)
        timeout = float(options.pop("timeout", 180))
        payload = {"messages": self._encode_images(request.messages), **options}
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["api-key"] = self.api_key
        started = time.perf_counter()
        try:
            with urlopen(Request(self.endpoint, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"), timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:1000]
            except OSError:
                detail = str(exc)
            raise ProfileError(f"Azure OpenAI request failed for profile {self.profile.name!r}: HTTP {exc.code}: {detail}") from exc
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            raise ProfileError(f"Azure OpenAI request failed for profile {self.profile.name!r}: {exc}") from exc
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        return ModelResponse(content=str(message.get("content") or ""), done_reason=choice.get("finish_reason"), usage=data.get("usage"), duration_seconds=time.perf_counter() - started, provider="azure_openai", model=self.profile.model)


def create_client(profile: ModelProfile, *, allow_remote: bool = False) -> ModelClient:
    if profile.backend == "ollama":
        return OllamaClient(profile)
    if profile.backend == "openai_compatible":
        return OpenAICompatibleClient(profile, allow_remote=allow_remote)
    if profile.backend == "azure_openai":
        return AzureOpenAIClient(profile, allow_remote=allow_remote)
    raise ProfileError(f"Backend non supportato: {profile.backend}")
