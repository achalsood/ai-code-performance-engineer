from perf_engineer.environment import environment_fingerprint


def test_environment_fingerprint_contains_reproduction_fields() -> None:
    fingerprint = environment_fingerprint()
    assert fingerprint["tool_version"] == "1.0.0"
    assert fingerprint["python_version"]
    assert fingerprint["system"]
