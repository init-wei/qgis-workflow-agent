"""QGIS --code startup for the dedicated development profile (system Python)."""

import os
from pathlib import Path

from qgis.core import QgsProject
from qgis.PyQt.QtCore import Qt
from qgis.utils import iface
from qgis_mcp_plugin.qgis_mcp_plugin import QgisMCPDockWidget

root = Path(os.environ["QWA_ROOT"]).resolve()
project = root / ".runtime/qgis/project.qgz"
if project.exists():
    if not QgsProject.instance().read(str(project)):
        raise RuntimeError("Could not read the isolated development project.")
else:
    QgsProject.instance().setFileName(str(project))
    if not QgsProject.instance().write():
        raise RuntimeError("Could not create the isolated development project.")
# Retain the widget on iface for the lifetime of this QGIS instance.
iface.qwa_dev_dock = QgisMCPDockWidget(iface)
iface.addDockWidget(Qt.RightDockWidgetArea, iface.qwa_dev_dock)
iface.qwa_dev_dock.show()
iface.qwa_dev_dock.start_server()
