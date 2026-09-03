from pathlib import Path

from perf_engineer.optimizer import _request


def test_request_includes_code_without_static_findings(tmp_path: Path) -> None:
    source = tmp_path / "clean.py"
    source.write_text("def add(left, right):\n    return left + right\n")
    request = _request(tmp_path, tmp_path, 2)
    assert request.files == {"clean.py": source.read_text()}
    assert request.findings == ()


def test_request_redacts_secrets_and_hashes_original(tmp_path: Path) -> None:
    source = tmp_path / "config.py"
    source.write_text('api_key = "sk-abcdefghijklmnop1234"\n')
    request = _request(tmp_path, tmp_path, 1)
    assert "sk-" not in request.files["config.py"]
    assert request.file_hashes and request.file_hashes["config.py"]
    assert request.redaction_counts == {"config.py": 1}
