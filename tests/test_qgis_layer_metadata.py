"""QGIS plugin metadata checked against the distribution's real QGIS API."""
import json
import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.external
def test_loaded_layer_metadata_matches_known_three_point_fixture():
    script = '''
import json
import sys
from qgis.core import QgsApplication
app = QgsApplication([], False)
app.initQgis()
from qgis_mcp_server.qgis_mcp_plugin.qgis_mcp_plugin import QgisMCPServer
server = QgisMCPServer()
loaded = server.add_vector_layer(sys.argv[1], "phase11_points")
layers = server.get_layers()
print(json.dumps({"loaded": loaded, "layers": layers}))
'''
    source = ROOT / "tests/fixtures/gis/phase11_points.geojson"
    result = subprocess.run(
        ["/usr/bin/python3", "-c", script, str(source)],
        cwd=ROOT,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    metadata = json.loads(result.stdout)
    assert len(metadata["layers"]) == 1
    for layer in (metadata["loaded"], metadata["layers"][0]):
        assert layer["source"] == str(source)
        assert layer["crs"] == "EPSG:4326"
        assert layer["geometry"] == "Point"
        assert layer["feature_count"] == 3
        assert layer["fields"] == ["id", "group", "value"]


@pytest.mark.external
def test_public_plugin_metadata_failure_cannot_add_a_layer():
    script = '''
import json
import sys
from qgis.core import QgsApplication, QgsProject
app = QgsApplication([], False)
app.initQgis()
from qgis_mcp_server.qgis_mcp_plugin.qgis_mcp_plugin import QgisMCPServer
server = QgisMCPServer()
def broken(layer):
    raise RuntimeError('metadata unavailable')
server._vector_layer_details = broken
reply = server.execute_command({'type': 'add_vector_layer', 'params': {'path': sys.argv[1]}})
print(json.dumps({'reply': reply, 'count': len(QgsProject.instance().mapLayers())}))
'''
    result = subprocess.run(['/usr/bin/python3', '-c', script, str(ROOT / 'tests/fixtures/gis/phase11_points.geojson')],
                            cwd=ROOT, env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'},
                            capture_output=True, text=True, timeout=30, check=True)
    result = json.loads(result.stdout)
    assert result['count'] == 0
    assert result['reply']['status'] == 'error'
    assert result['reply']['outcome'] == 'not_started'


@pytest.mark.external
def test_public_plugin_error_after_layer_mutation_reports_unknown_outcome():
    script = '''
import json
import sys
from qgis.core import QgsApplication, QgsProject
app = QgsApplication([], False)
app.initQgis()
import qgis_mcp_server.qgis_mcp_plugin.qgis_mcp_plugin as plugin
project = QgsProject.instance()
class ProjectProxy:
    @staticmethod
    def instance():
        return ProjectProxy()
    def addMapLayer(self, layer):
        project.addMapLayer(layer)
        raise RuntimeError('injected after mutation')
plugin.QgsProject = ProjectProxy
server = plugin.QgisMCPServer()
reply = server.execute_command({'type': 'add_vector_layer', 'params': {'path': sys.argv[1]}})
print(json.dumps({'reply': reply, 'count': len(project.mapLayers())}))
'''
    result = subprocess.run(['/usr/bin/python3', '-c', script, str(ROOT / 'tests/fixtures/gis/phase11_points.geojson')],
                            cwd=ROOT, env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'},
                            capture_output=True, text=True, timeout=30, check=True)
    result = json.loads(result.stdout)
    assert result['count'] == 1
    assert result['reply']['status'] == 'error'
    assert result['reply']['outcome'] == 'unknown'


@pytest.mark.external
def test_current_qgis_definitions_reject_invalid_parameters_for_all_six_algorithms(tmp_path):
    script = '''
import json, sys
sys.path.append('/usr/share/qgis/python/plugins')
from qgis.core import QgsApplication, QgsProject
app = QgsApplication([], False)
app.initQgis()
from processing.core.Processing import Processing
Processing.initialize()
from qgis_mcp_server.qgis_mcp_plugin.qgis_mcp_plugin import QgisMCPServer
server = QgisMCPServer()
source = sys.argv[1]
output = sys.argv[2]
requests = [
 ('native:reprojectlayer', {'INPUT': source, 'OUTPUT': output}),
 ('native:buffer', {'INPUT': source, 'OUTPUT': output, 'DISTANCE': 'bad', 'SEGMENTS': 5,
                   'END_CAP_STYLE': 0, 'JOIN_STYLE': 0, 'MITER_LIMIT': 2, 'DISSOLVE': False}),
 ('native:clip', {'INPUT': source, 'OUTPUT': output}),
 ('native:dissolve', {'INPUT': source, 'OUTPUT': output, 'SEPARATE_DISJOINT': 'false'}),
 ('qgis:statisticsbycategories', {'INPUT': source, 'OUTPUT': output, 'CATEGORIES_FIELD_NAME': 5}),
 ('native:basicstatisticsforfields', {'INPUT_LAYER': source, 'FIELD_NAME': 9}),
]
replies = [server.execute_command({'type': 'get_qgis_info', 'params': {'processing_requests': [
   {'step_id': i + 1, 'algorithm': algorithm, 'parameters': parameters}]}})
   for i, (algorithm, parameters) in enumerate(requests)]
valid = server.execute_command({'type': 'get_qgis_info', 'params': {'processing_requests': [
   {'step_id': 1, 'algorithm': 'native:reprojectlayer', 'parameters': {
       'INPUT': source, 'OUTPUT': output, 'TARGET_CRS': 'EPSG:3857'}}]}})
print(json.dumps({'replies': replies, 'valid': valid, 'count': len(QgsProject.instance().mapLayers())}))
'''
    source = ROOT / 'tests/fixtures/gis/phase11_points.geojson'
    target = tmp_path / 'not-created.gpkg'
    result = subprocess.run(['/usr/bin/python3', '-c', script, str(source), str(target)],
                            cwd=ROOT, env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'},
                            capture_output=True, text=True, timeout=30, check=True)
    result = json.loads(result.stdout)
    assert len(result['replies']) == 6
    assert all(reply['status'] == 'error' and reply['outcome'] == 'not_started' for reply in result['replies'])
    assert result['valid']['status'] == 'success'
    assert result['valid']['result']['processing_validation'] == [
        {'step_id': 1, 'valid': True, 'source_paths': [str(source)], 'output_paths': [str(target)]}]
    assert result['count'] == 0
    assert not target.exists()
