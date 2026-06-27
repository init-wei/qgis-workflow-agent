#!/usr/bin/env python3
"""
LLM 配置与实例创建工具
默认读取环境变量，允许通过前端配置覆盖
"""

from __future__ import annotations

from typing import Any, Dict, Optional
import os

from langchain_openai import ChatOpenAI


def _as_float(value: Optional[str], default: float) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _as_int(value: Optional[str]) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        parsed = int(value)
        return parsed if parsed > 0 else None
    except Exception:
        return None


def _clean_override(override: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not override:
        return {}
    cleaned: Dict[str, Any] = {}
    for key, value in override.items():
        if value is None:
            continue
        if isinstance(value, str) and value.strip() == "":
            continue
        cleaned[key] = value
    return cleaned


def get_env_llm_config() -> Dict[str, Any]:
    """
    从环境变量读取默认 LLM 配置
    """
    return {
        "api_key": os.getenv("OPENAI_API_KEY", ""),
        "api_base": os.getenv("OPENAI_BASE_URL", ""),
        "model": os.getenv("OPENAI_MODEL_NAME", "deepseek-chat"),
        "temperature": _as_float(os.getenv("OPENAI_TEMPERATURE"), 0.1),
        "max_tokens": _as_int(os.getenv("OPENAI_MAX_TOKENS")),
    }


def build_llm_config(override: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    合并 env 默认与覆盖配置
    """
    config = get_env_llm_config()
    override_clean = _clean_override(override)
    config.update(override_clean)
    return config


def create_llm(override: Optional[Dict[str, Any]] = None) -> ChatOpenAI:
    """
    创建 ChatOpenAI 实例（支持覆盖配置）
    """
    config = build_llm_config(override)

    kwargs: Dict[str, Any] = {
        "model": config.get("model"),
        "temperature": config.get("temperature"),
    }

    if config.get("max_tokens") is not None:
        kwargs["max_tokens"] = config.get("max_tokens")

    if config.get("api_key"):
        kwargs["api_key"] = config.get("api_key")

    if config.get("api_base"):
        kwargs["base_url"] = config.get("api_base")

    return ChatOpenAI(**kwargs)
