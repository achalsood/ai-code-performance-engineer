import sys

import pytest

from perf_engineer.providers import (
    CommandProvider,
    OptimizationRequest,
    ProviderError,
    _system_prompt,
)


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
                "strategy": "data-structure",
                "expected_impact": "linear to constant lookup",
                "risk": "low",
            }
        ]
    }
    command = [sys.executable, "-c", f"import json; print(json.dumps({payload!r}))"]
    candidates = CommandProvider(command).generate(request())
    assert candidates[0].candidate_id == "one"
    assert candidates[0].strategy == "data-structure"


def test_command_provider_rejects_invalid_response() -> None:
    command = [sys.executable, "-c", "print('not json')"]
    with pytest.raises(ProviderError):
        CommandProvider(command).generate(request())


def test_command_provider_rejects_empty_command() -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        CommandProvider([])


def test_command_provider_rejects_too_many_candidates() -> None:
    payload = {
        "candidates": [
            {
                "candidate_id": str(index),
                "title": "Candidate",
                "rationale": "Reason",
                "patch": "diff",
            }
            for index in range(3)
        ]
    }
    command = [sys.executable, "-c", f"import json; print(json.dumps({payload!r}))"]
    with pytest.raises(ProviderError, match="more candidates"):
        CommandProvider(command).generate(request())


def test_command_provider_rejects_duplicate_candidate_ids() -> None:
    candidate = {
        "candidate_id": "duplicate",
        "title": "Candidate",
        "rationale": "Reason",
        "patch": "diff",
    }
    payload = {"candidates": [candidate, candidate]}
    command = [sys.executable, "-c", f"import json; print(json.dumps({payload!r}))"]
    with pytest.raises(ProviderError, match="duplicate candidate IDs"):
        CommandProvider(command).generate(request())


def test_command_provider_reports_nonzero_exit() -> None:
    command = [
        sys.executable,
        "-c",
        "import sys; print('provider failed', file=sys.stderr); raise SystemExit(7)",
    ]
    with pytest.raises(ProviderError, match="provider exited with 7: provider failed"):
        CommandProvider(command).generate(request())


def test_command_provider_reports_timeout() -> None:
    command = [sys.executable, "-c", "import time; time.sleep(1)"]
    with pytest.raises(ProviderError, match="provider timed out"):
        CommandProvider(command, timeout=0.01).generate(request())


def test_system_prompt_uses_attribution_to_change_refinement_strategy() -> None:
    prompt = _system_prompt(3)

    assert "measured attribution as experimental evidence" in prompt
    assert "Do not repeat a rejected strategy against the same evidence" in prompt
    assert "improved wall time but regressed CPU or memory" in prompt
    assert "different strategy for the same hotspot after a high-confidence rejection" in prompt
