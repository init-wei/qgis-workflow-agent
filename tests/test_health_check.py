#!/usr/bin/env python3
"""Tests for the developer environment health check."""

from scripts.health_check import (
    PLACEHOLDER_API_KEYS,
    CheckResult,
    check_llm_config,
    summarize_results,
)


def test_llm_config_rejects_placeholder_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", next(iter(PLACEHOLDER_API_KEYS)))
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("OPENAI_MODEL_NAME", "deepseek-chat")

    result = check_llm_config(live=False)

    assert result.ok is False
    assert "OPENAI_API_KEY" in result.message
    assert next(iter(PLACEHOLDER_API_KEYS)) not in result.message


def test_llm_config_accepts_deepseek_settings_without_exposing_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("OPENAI_MODEL_NAME", "deepseek-chat")

    result = check_llm_config(live=False)

    assert result.ok is True
    assert "deepseek-chat" in result.message
    assert "sk-test-secret" not in result.message


def test_summarize_results_never_prints_secret_values():
    results = [
        CheckResult(
            name="LLM config",
            ok=True,
            message="Configured model deepseek-chat with key sk-test-secret",
        )
    ]

    summary = summarize_results(results, secrets=["sk-test-secret"])

    assert "sk-test-secret" not in summary
    assert "***" in summary
