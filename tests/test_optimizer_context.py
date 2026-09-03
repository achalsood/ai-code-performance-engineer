from pathlib import Path

from perf_engineer.optimizer import _request
from perf_engineer.profiling import Hotspot, ProfileResult


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


def test_request_prioritizes_measured_hotspot_context(tmp_path: Path) -> None:
    cold = tmp_path / "a_cold.py"
    hot = tmp_path / "z_hot.py"
    cold.write_text("value = 1\n")
    hot.write_text("def expensive():\n    return sum(range(1000))\n")
    profile = ProfileResult(
        "cprofile",
        ("python", "z_hot.py"),
        1.0,
        0.9,
        100,
        (Hotspot("expensive", "z_hot.py", 1, 50, 0.4, 0.8),),
    )
    request = _request(tmp_path, tmp_path, 2, profile)
    assert next(iter(request.files)) == "z_hot.py"
    assert request.hotspots == profile.hotspots
    assert request.optimization_hints[0].startswith("Measured hotspot z_hot.py:1")
