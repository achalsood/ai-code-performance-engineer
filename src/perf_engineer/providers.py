from __future__ import annotations

import json
import subprocess
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
