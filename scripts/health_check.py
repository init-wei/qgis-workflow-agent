#!/usr/bin/env python3
"""Developer environment health check for the QGIS workflow agent."""

from __future__ import annotations

import argparse
import asyncio
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import psycopg
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_DATABASE_URL = "postgresql://postgres:postgres@127.0.0.1:5432/qgis_db"
DEFAULT_MCP_URL = "http://localhost:8000/mcp"
PLACEHOLDER_API_KEYS = {
    "your_api_key_here",
    "your_openai_api_key_here",
    "your_deepseek_api_key_here",
    "your_deepseek_api_key",
}


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    message: str


def _is_placeholder(value: str) -> bool:
    return value.strip().lower() in PLACEHOLDER_API_KEYS


def _safe_message(message: str, secrets: Iterable[str]) -> str:
    safe = message
    for secret in secrets:
        if secret:
            safe = safe.replace(secret, "***")
    return safe


def check_llm_config(live: bool = False) -> CheckResult:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    base_url = os.getenv("OPENAI_BASE_URL", "").strip()
    model = os.getenv("OPENAI_MODEL_NAME", "").strip() or "deepseek-chat"

    if not api_key or _is_placeholder(api_key):
        return CheckResult("LLM config", False, "OPENAI_API_KEY is missing or still a placeholder.")
    if not base_url:
        return CheckResult("LLM config", False, "OPENAI_BASE_URL is missing.")
    if not model:
        return CheckResult("LLM config", False, "OPENAI_MODEL_NAME is missing.")

    if not live:
        return CheckResult("LLM config", True, f"Configured OpenAI-compatible model {model}.")

    try:
        from agent.llm import create_llm

        llm = create_llm()
        response = llm.invoke("Reply with the single word: ok")
        content = getattr(response, "content", "")
        if content:
            return CheckResult("LLM live call", True, f"Live model call succeeded for {model}.")
        return CheckResult("LLM live call", False, f"Live model call returned an empty response for {model}.")
    except Exception as exc:
        return CheckResult("LLM live call", False, f"Live model call failed: {exc}")


def check_database() -> CheckResult:
    database_url = os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
    try:
        with psycopg.connect(database_url, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("select 1")
                cur.fetchone()
        return CheckResult("PostgreSQL", True, "Connected and ran select 1.")
    except Exception as exc:
        return CheckResult(
            "PostgreSQL",
            False,
            (
                "Connection failed. Start Docker with 'docker compose up -d postgres' "
                "and initialize with 'uv run python scripts/setup_database.py'. "
                f"Error: {exc}"
            ),
        )


def check_qgis_socket() -> CheckResult:
    try:
        from qgis_mcp_server.qgis_mcp_server import QgisMCPServer

        client = QgisMCPServer(host="localhost", port=9876, timeout=5)
        try:
            if not client.connect():
                return CheckResult("QGIS socket", False, "Could not connect to localhost:9876.")
            response = client.send_command("ping")
            if isinstance(response, dict):
                return CheckResult("QGIS socket", True, "QGIS MCP plugin responded to ping.")
            return CheckResult("QGIS socket", False, f"Unexpected ping response: {response!r}")
        finally:
            client.disconnect()
    except Exception as exc:
        return CheckResult("QGIS socket", False, f"QGIS socket check failed: {exc}")


async def _check_http_mcp_async(url: str) -> CheckResult:
    from agent.tools.mcp_client import QGISMCPClient

    client = QGISMCPClient(url=url)
    try:
        tools = await client.list_tools()
        names = {tool["name"] for tool in tools}
        if "ping" not in names:
            return CheckResult("HTTP MCP", False, f"Connected but ping tool is missing ({len(names)} tools).")
        return CheckResult("HTTP MCP", True, f"Connected and listed {len(names)} tools, including ping.")
    finally:
        await client.close()


def check_http_mcp() -> CheckResult:
    url = os.getenv("QGIS_MCP_SERVER_URL", DEFAULT_MCP_URL)
    try:
        return asyncio.run(_check_http_mcp_async(url))
    except Exception as exc:
        return CheckResult("HTTP MCP", False, f"HTTP MCP check failed for {url}: {exc}")


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def _wait_for_port(host: str, port: int, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _port_open(host, port):
            return True
        time.sleep(0.5)
    return False


def start_http_mcp_if_needed() -> subprocess.Popen[str] | None:
    if _port_open("127.0.0.1", 8000):
        return None

    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    process = subprocess.Popen(
        [sys.executable, str(PROJECT_ROOT / "qgis_mcp_server" / "qgis_mcp_server.py")],
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if _wait_for_port("127.0.0.1", 8000):
        return process

    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
    return None


def summarize_results(results: Sequence[CheckResult], secrets: Sequence[str] | None = None) -> str:
    secret_values = list(secrets or [])
    lines = []
    for result in results:
        status = "PASS" if result.ok else "FAIL"
        message = _safe_message(result.message, secret_values)
        lines.append(f"[{status}] {result.name}: {message}")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check the local QGIS agent development environment.")
    parser.add_argument("--skip-db", action="store_true", help="Skip PostgreSQL connectivity.")
    parser.add_argument("--skip-qgis", action="store_true", help="Skip QGIS socket connectivity.")
    parser.add_argument("--skip-http-mcp", action="store_true", help="Skip HTTP MCP wrapper connectivity.")
    parser.add_argument("--skip-llm", action="store_true", help="Skip LLM configuration.")
    parser.add_argument("--start-http-mcp", action="store_true", help="Start the HTTP MCP wrapper temporarily if needed.")
    parser.add_argument("--llm-live", action="store_true", help="Run a live LLM call after config validation.")
    return parser.parse_args()


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    args = parse_args()
    process = None
    results: list[CheckResult] = []

    try:
        if not args.skip_llm:
            results.append(check_llm_config(live=args.llm_live))
        if not args.skip_db:
            results.append(check_database())
        if not args.skip_qgis:
            results.append(check_qgis_socket())
        if not args.skip_http_mcp:
            if args.start_http_mcp:
                process = start_http_mcp_if_needed()
                if process is None and not _port_open("127.0.0.1", 8000):
                    results.append(CheckResult("HTTP MCP startup", False, "Could not start HTTP MCP wrapper on port 8000."))
            results.append(check_http_mcp())

        secrets = [os.getenv("OPENAI_API_KEY", "")]
        print(summarize_results(results, secrets=secrets))
        return 0 if all(result.ok for result in results) else 1
    finally:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
