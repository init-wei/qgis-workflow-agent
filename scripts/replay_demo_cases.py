#!/usr/bin/env python3
"""Replay Phase 9/10 static demo cases without QGIS, LLM, MCP, or PostgreSQL."""

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.tools.demo_cases import load_demo_case, load_demo_cases, replay_demo_case


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--case-id",
        help="Replay one demo case ID instead of all fixtures.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of text lines.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        cases = [load_demo_case(args.case_id)] if args.case_id else load_demo_cases()
    except KeyError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    results = [replay_demo_case(case) for case in cases]
    if args.json:
        print(json.dumps([result.model_dump(mode="json") for result in results], indent=2))
        return 0

    for result in results:
        print(
            f"{result.case_id}: status={result.status} "
            f"attempts={result.attempts_used} artifacts={result.artifact_count}"
        )
        if result.repair_summaries:
            print(f"  repairs={len(result.repair_summaries)}")
        if result.skill_draft:
            print(f"  skill_draft={result.skill_draft['draft_id']} registered=false")
        if result.registered_skill:
            print(f"  registered_skill={result.registered_skill['skill_id']}")
        if result.skill_execution_plan:
            print(f"  skill_execution_plan={result.skill_execution_plan['status']}")
        if result.reuse_preview:
            print(f"  reuse_tool={result.reuse_preview['tool_name']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
