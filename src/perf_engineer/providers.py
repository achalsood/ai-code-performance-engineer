from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from typing import Protocol

from .models import Finding


class ProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class OptimizationRequest:
    objective: str
    language: str
    findings: tuple[Finding, ...]
    files: dict[str, str]
    maximum_candidates: int
    file_hashes: dict[str, str] | None = None
    redaction_counts: dict[str, int] | None = None
    optimization_hints: tuple[str, ...] = ()


@dataclass(frozen=True)
class OptimizationCandidate:
    candidate_id: str
    title: str
    rationale: str
    patch: str


class CandidateProvider(Protocol):
    def generate(self, request: OptimizationRequest) -> list[OptimizationCandidate]: ...


class CommandProvider:
    """JSON-lines adapter for local models, hosted agents, or custom AI gateways."""

    def __init__(self, command: list[str], *, timeout: float = 180.0) -> None:
        if not command:
            raise ValueError("provider command cannot be empty")
        self.command = command
        self.timeout = timeout

    def generate(self, request: OptimizationRequest) -> list[OptimizationCandidate]:
        payload = json.dumps(asdict(request))
        try:
            completed = subprocess.run(
                self.command,
                input=payload,
                text=True,
                capture_output=True,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ProviderError(f"provider timed out after {self.timeout:g}s") from exc
        if completed.returncode:
            error = completed.stderr.strip()[-1000:]
            raise ProviderError(f"provider exited with {completed.returncode}: {error}")
        try:
            response = json.loads(completed.stdout)
            raw_candidates = response["candidates"]
            candidates = [OptimizationCandidate(**item) for item in raw_candidates]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ProviderError("provider returned an invalid candidate response") from exc
        if len(candidates) > request.maximum_candidates:
            raise ProviderError("provider returned more candidates than requested")
        if len({candidate.candidate_id for candidate in candidates}) != len(candidates):
            raise ProviderError("provider returned duplicate candidate IDs")
        return candidates


def _system_prompt(maximum_candidates: int) -> str:
    return (
        "You are a code performance engineer. Return only JSON with a candidates array. "
        "Each candidate requires candidate_id, title, rationale, and a unified diff in patch. "
        "Use the ranked findings and optimization_hints to target measured hot paths. "
        "Prefer algorithmic or allocation reductions over cosmetic rewrites. Each candidate "
        "must isolate one optimization so the benchmark can attribute its effect. Preserve "
        "observable behavior, modify only existing supported source files, and "
        "produce at most "
        f"{maximum_candidates} independent candidates. Do not use markdown fences."
    )


def _decode_candidates(payload: object, maximum_candidates: int) -> list[OptimizationCandidate]:
    if not isinstance(payload, dict) or not isinstance(payload.get("candidates"), list):
        raise ProviderError("provider response must contain a candidates array")
    try:
        candidates = [OptimizationCandidate(**item) for item in payload["candidates"]]
    except TypeError as exc:
        raise ProviderError("provider returned an invalid candidate") from exc
    if len(candidates) > maximum_candidates:
        raise ProviderError("provider returned more candidates than requested")
    if len({item.candidate_id for item in candidates}) != len(candidates):
        raise ProviderError("provider returned duplicate candidate IDs")
    return candidates


class OpenAICompatibleProvider:
    """Built-in adapter for OpenAI and servers implementing the Chat Completions API."""

    def __init__(
        self,
        *,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        api_key: str | None = None,
        timeout: float = 180.0,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.timeout = timeout
        if not self.api_key:
            raise ProviderError("OPENAI_API_KEY is required for the OpenAI provider")

    def generate(self, request: OptimizationRequest) -> list[OptimizationCandidate]:
        body = {
            "model": self.model,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": _system_prompt(request.maximum_candidates)},
                {"role": "user", "content": json.dumps(asdict(request), separators=(",", ":"))},
            ],
        }
        response = _post_json(
            f"{self.base_url}/chat/completions",
            body,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=self.timeout,
        )
        try:
            choices = response["choices"]
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                raise TypeError
            message = choices[0]["message"]
            if not isinstance(message, dict) or not isinstance(message.get("content"), str):
                raise TypeError
            content = message["content"]
            payload = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ProviderError("OpenAI-compatible provider returned invalid JSON") from exc
        return _decode_candidates(payload, request.maximum_candidates)


class OllamaProvider:
    """Built-in adapter for a local Ollama server."""

    def __init__(
        self,
        *,
        model: str,
        base_url: str = "http://127.0.0.1:11434",
        timeout: float = 180.0,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def generate(self, request: OptimizationRequest) -> list[OptimizationCandidate]:
        body = {
            "model": self.model,
            "stream": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": _system_prompt(request.maximum_candidates)},
                {"role": "user", "content": json.dumps(asdict(request), separators=(",", ":"))},
            ],
            "options": {"temperature": 0.2},
        }
        response = _post_json(f"{self.base_url}/api/chat", body, timeout=self.timeout)
        try:
            message = response["message"]
            if not isinstance(message, dict) or not isinstance(message.get("content"), str):
                raise TypeError
            payload = json.loads(message["content"])
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ProviderError("Ollama returned invalid JSON") from exc
        return _decode_candidates(payload, request.maximum_candidates)


def _post_json(
    url: str,
    body: dict[str, object],
    *,
    headers: dict[str, str] | None = None,
    timeout: float,
    maximum_response_bytes: int = 2_000_000,
    attempts: int = 3,
) -> dict[str, object]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body, separators=(",", ":")).encode(),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    last_error: BaseException | None = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                content = response.read(maximum_response_bytes + 1)
            if len(content) > maximum_response_bytes:
                raise ProviderError("provider response exceeded the size limit")
            payload = json.loads(content)
            break
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code != 429 and exc.code < 500:
                raise ProviderError(f"provider request failed with HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
        if attempt + 1 < attempts:
            time.sleep(0.25 * (2**attempt))
    else:
        raise ProviderError(f"provider request failed after {attempts} attempts: {last_error}")
    if not isinstance(payload, dict):
        raise ProviderError("provider returned a non-object response")
    return payload
