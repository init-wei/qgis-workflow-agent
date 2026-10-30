"""Read-only Processing preflight in system Python before a plan is approved.

stdin contains the same request list used by the paired plugin's get_qgis_info.
No socket is opened and no algorithm is executed. The Agent venv stays separate.
"""

import contextlib
import json
import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def inspect_processing(requests):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    sys.path.insert(0, str(ROOT))
    with tempfile.TemporaryDirectory(prefix="qwa-processing-inspect-") as profile:
        os.environ["QGIS_CUSTOM_CONFIG_PATH"] = profile
        from qgis.core import QgsApplication

        app = QgsApplication([], False, profile)
        app.initQgis()
        try:
            sys.path.append(str(Path(QgsApplication.pkgDataPath()) / "python/plugins"))
            from processing.core.Processing import Processing
            from qgis_mcp_server.qgis_mcp_plugin.qgis_mcp_plugin import QgisMCPServer

            Processing.initialize()
            server = QgisMCPServer(iface=None)
            return server.execute_command({
                "type": "get_qgis_info", "params": {"processing_requests": requests},
            })
        finally:
            app.exitQgis()


def main():
    try:
        requests = json.load(sys.stdin)
        # Reserve stdout for the machine-readable protocol, including during init.
        with contextlib.redirect_stdout(sys.stderr):
            result = inspect_processing(requests)
    except Exception as exc:
        result = {
            "status": "error", "outcome": "not_started",
            "message": f"Processing preflight unavailable ({type(exc).__name__}).",
        }
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("status") == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
