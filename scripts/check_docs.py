"""Check local links in the current planning and agent documentation."""

from pathlib import Path
import re
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    documents = [ROOT / name for name in ("README.md", "AGENTS.md", "CONTEXT.md", "docs/README.md")]
    for name in ("agents", "adr", "onboarding", "specs", "tickets", "architecture"):
        documents.extend((ROOT / "docs" / name).rglob("*.md"))
    documents.extend(ROOT / "docs" / name for name in (
        "development.md", "testing.md", "documentation.md", "implementation-plan.md", "size-estimate.md"
    ))
    failures = []
    for path in sorted(set(documents)):
        if not path.is_file():
            failures.append(f"Missing document: {path.relative_to(ROOT)}")
            continue
        content = re.sub(r"```.*?```", "", path.read_text(), flags=re.DOTALL)
        for link in re.findall(r"\]\(([^)]+)\)", content):
            target = link.split("#", 1)[0].strip("<>")
            if not target or re.match(r"[a-zA-Z][a-zA-Z0-9+.-]*:", target):
                continue
            if not (path.parent / unquote(target)).exists():
                failures.append(f"{path.relative_to(ROOT)}: missing {target}")
    if failures:
        print("\n".join(failures))
        return 1
    print(f"Checked {len(set(documents))} current documents; local link targets exist.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
