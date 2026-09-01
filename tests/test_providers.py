import sys

import pytest

from perf_engineer.providers import CommandProvider, OptimizationRequest, ProviderError


def request() -> OptimizationRequest:
    return OptimizationRequest("faster", "python", (), {"example.py": "value = 1"}, 2)


def test_command_provider_parses_structured_candidates() -> None:
    payload = {
        "candidates": [
            {
                "candidate_id": "one",
                "title": "Use set",
                "rationale": "O(1)",
                "patch": "diff",
            }
        ]
    }
    command = [sys.executable, "-c", f"import json; print(json.dumps({payload!r}))"]
    candidates = CommandProvider(command).generate(request())
    assert candidates[0].candidate_id == "one"


def test_command_provider_rejects_invalid_response() -> None:
    command = [sys.executable, "-c", "print('not json')"]
    with pytest.raises(ProviderError):
        CommandProvider(command).generate(request())
