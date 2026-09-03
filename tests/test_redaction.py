from perf_engineer.redaction import redact_secrets


def test_redacts_common_credentials_and_preserves_surrounding_code() -> None:
    source = 'api_key = "sk-abcdefghijklmnop1234"\nvalue = 42\n'
    result = redact_secrets(source)
    assert "sk-" not in result.content
    assert "value = 42" in result.content
    assert result.redaction_count >= 1


def test_redacts_private_key_block() -> None:
    source = "-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----"
    assert redact_secrets(source).content == "[REDACTED]"
