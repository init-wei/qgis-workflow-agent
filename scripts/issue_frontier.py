"""Print unclaimed V1 issues whose native GitHub blockers are all closed."""

import json
import subprocess

REPOSITORY = "init-wei/qgis-workflow-agent"


def query(endpoint: str):
    return json.loads(subprocess.check_output(["gh", "api", endpoint], text=True))


def main() -> None:
    issues = query(f"repos/{REPOSITORY}/issues?state=open&labels=qwa-v1&per_page=100")
    for issue in sorted(issues, key=lambda row: row["number"]):
        if "pull_request" in issue:
            continue
        blockers = query(f"repos/{REPOSITORY}/issues/{issue['number']}/dependencies/blocked_by")
        open_blockers = [str(row["number"]) for row in blockers if row["state"] != "closed"]
        if open_blockers:
            state = "blocked by #" + ", #".join(open_blockers)
        elif "ready-for-agent" not in {label["name"] for label in issue["labels"]}:
            state = "not marked ready-for-agent"
        elif issue["assignees"]:
            state = "claimed; check run comments"
        else:
            state = "READY; check run comments before claiming"
        print(f"#{issue['number']} {issue['title']}: {state}")


if __name__ == "__main__":
    main()
