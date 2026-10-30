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
            if requests == {"capabilities": True}:
                return inspect_capabilities()
            server = QgisMCPServer(iface=None)
            return server.execute_command({
                "type": "get_qgis_info", "params": {"processing_requests": requests},
            })
        finally:
            app.exitQgis()


def inspect_capabilities():
    """Describe only the allowed algorithms using the installed QGIS definitions."""
    from qgis.core import Qgis, QgsApplication, QgsProcessingParameterDefinition
    from qgis_mcp_server.qgis_mcp_plugin.processing_policy import SUPPORTED_PROCESSING_ALGORITHMS

    algorithms = []
    for algorithm_id in sorted(SUPPORTED_PROCESSING_ALGORITHMS):
        definition = QgsApplication.processingRegistry().algorithmById(algorithm_id)
        item = {"algorithm": algorithm_id, "available": definition is not None}
        if definition is not None:
            parameters = []
            for parameter in definition.parameterDefinitions():
                flags = parameter.flags()
                if flags & QgsProcessingParameterDefinition.FlagHidden:
                    continue
                details = {
                    "name": parameter.name(), "type": parameter.type(),
                    "description": parameter.description(),
                    "optional": bool(flags & QgsProcessingParameterDefinition.FlagOptional),
                }
                default = parameter.defaultValue()
                if isinstance(default, (str, int, float, bool, list)) or default is None:
                    details["default"] = default
                if parameter.type() == "enum":
                    details["options"] = parameter.options()
                parameters.append(details)
            item.update(name=definition.displayName(), provider=definition.provider().id(),
                        parameters=parameters)
        algorithms.append(item)
    return {"status": "success", "result": {
        "qgis_version": Qgis.version(), "processing_capabilities": algorithms,
    }}


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
