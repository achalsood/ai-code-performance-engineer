import io
import json
from unittest.mock import patch

from perf_engineer.providers import OllamaProvider, OpenAICompatibleProvider, OptimizationRequest


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def optimization_request() -> OptimizationRequest:
    return OptimizationRequest("faster", "python", (), {"work.py": "pass"}, 1)


def candidate_payload() -> dict[str, object]:
    return {
        "candidates": [
            {
                "candidate_id": "one",
                "title": "Fast",
                "rationale": "Less work",
                "patch": "diff",
            }
        ]
    }


def test_openai_compatible_provider() -> None:
    content = json.dumps(candidate_payload())
    response = Response(json.dumps({"choices": [{"message": {"content": content}}]}).encode())
    with patch("urllib.request.urlopen", return_value=response):
        candidates = OpenAICompatibleProvider(model="test", api_key="key").generate(
            optimization_request()
        )
    assert candidates[0].candidate_id == "one"


def test_ollama_provider() -> None:
    content = json.dumps(candidate_payload())
    response = Response(json.dumps({"message": {"content": content}}).encode())
    with patch("urllib.request.urlopen", return_value=response):
        candidates = OllamaProvider(model="test").generate(optimization_request())
    assert candidates[0].candidate_id == "one"
