"""Regressions at the public QGIS plugin and execution boundaries."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import select
import sqlite3
import subprocess

import pytest

from agent.tools.step_executor import StepExecutor
from agent.tools.tool_contracts import ExecutionTrace, ToolCallPlan, ToolSpec
from agent.tools.tool_registry import ToolRegistry


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.external
def test_gdal_committed_wal_changes_logical_input_without_changing_recorded_main_file(tmp_path):
    """A real GPKG read proves why the main-file checksum cannot cover WAL."""
    from agent.planning.plan_schema import InputSpec, TaskPlan
    from agent.planning.processing_preflight import validate_original_inputs

    source = tmp_path / 'points.gpkg'
    script = '''
import json, sys
from osgeo import ogr, osr
path, mode = sys.argv[1:]
if mode == 'create':
    ds = ogr.GetDriverByName('GPKG').CreateDataSource(path)
    crs = osr.SpatialReference()
    crs.ImportFromEPSG(4326)
    layer = ds.CreateLayer('points', crs, ogr.wkbPoint, options=['SPATIAL_INDEX=NO'])
    layer.CreateField(ogr.FieldDefn('value', ogr.OFTInteger))
    feature = ogr.Feature(layer.GetLayerDefn())
    feature.SetField('value', 1)
    feature.SetGeometry(ogr.CreateGeometryFromWkt('POINT (116.4 39.9)'))
    layer.CreateFeature(feature)
    feature = layer = ds = None
else:
    ds = ogr.Open(path, 0)
    print(json.dumps(ds.GetLayerByName('points').GetNextFeature().GetField('value')))
'''
    subprocess.run(['/usr/bin/python3', '-c', script, str(source), 'create'], check=True,
                   capture_output=True, text=True, timeout=30)
    with sqlite3.connect(source) as writer:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('PRAGMA wal_autocheckpoint=0')
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        writer.execute('UPDATE points SET value=2')
        writer.commit()
        result = subprocess.run(['/usr/bin/python3', '-c', script, str(source), 'read'], check=True,
                                capture_output=True, text=True, timeout=30)
        assert json.loads(result.stdout) == 2
        assert hashlib.sha256(source.read_bytes()).hexdigest() == before
        plan = TaskPlan(task_id='wal-input', task_summary='Load', steps=[], risk_level='low',
                        review_status='approved', required_inputs=[InputSpec(
                            input_id='source', description='GPKG', path=f'{source}|layername=points', sha256=before)])
        with pytest.raises(ValueError, match='SQLite transaction sidecar'):
            validate_original_inputs(plan)
        assert Path(str(source) + '-wal').is_file()
        assert hashlib.sha256(source.read_bytes()).hexdigest() == before


@pytest.mark.external
def test_two_plugin_writers_cannot_reserve_the_same_artifact(tmp_path):
    target = tmp_path / 'shared/outputs/task-1/r1/image/map.png'
    script = '''
import os
os.environ['QGIS_CUSTOM_CONFIG_PATH'] = os.path.join(os.environ['QWA_ROOT'], f'qgis-config-{os.getpid()}')
import json, sys
from qgis.core import QgsApplication
from qgis.PyQt.QtCore import QSize
from qgis.PyQt.QtGui import QImage
app = QgsApplication([], True)
app.initQgis()
assert os.path.commonpath([os.environ['QGIS_CUSTOM_CONFIG_PATH'], QgsApplication.qgisSettingsDirPath()]) == os.environ['QGIS_CUSTOM_CONFIG_PATH']
from qgis_mcp_server.qgis_mcp_plugin.qgis_mcp_plugin import QgisMCPServer
class Canvas:
    def size(self): return QSize(2, 2)
    def resize(self, *args): pass
    def saveAsImage(self, path):
        image = QImage(2, 2, QImage.Format_RGB32)
        image.fill(0)
        image.save(path, 'PNG')
class Iface:
    def mapCanvas(self): return Canvas()
print('ready', flush=True)
sys.stdin.readline()
reply = QgisMCPServer(iface=Iface()).execute_command({
    'type': 'capture_map_canvas', 'params': {'path': sys.argv[1]}})
reply['_profile'] = QgsApplication.qgisSettingsDirPath()
print(json.dumps(reply), flush=True)
'''
    env = {**os.environ, 'QT_QPA_PLATFORM': 'offscreen', 'QWA_ROOT': str(tmp_path)}
    processes = [subprocess.Popen(
        ['/usr/bin/python3', '-c', script, str(target)], cwd=ROOT, env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    ) for _ in range(2)]
    try:
        for process in processes:
            assert select.select([process.stdout], [], [], 30)[0]
            assert process.stdout.readline().strip() == 'ready'
        for process in processes:
            process.stdin.write('\n')
            process.stdin.flush()
        replies = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=30)
            assert process.returncode == 0, stderr
            replies.append(json.loads(stdout))
        assert len({reply.pop('_profile') for reply in replies}) == 2
        assert sorted(reply['status'] for reply in replies) == ['error', 'success']
        assert next(reply for reply in replies if reply['status'] == 'error')['outcome'] == 'not_started'
        assert target.read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate()


@pytest.mark.external
def test_real_qgis_capture_creates_a_readable_png_in_a_new_directory(tmp_path):
    target = tmp_path / 'shared/outputs/task-1/r1/image/map.png'
    script = '''
import os
os.environ['QGIS_CUSTOM_CONFIG_PATH'] = os.path.join(os.environ['QWA_ROOT'], f'qgis-config-{os.getpid()}')
import json, sys
from qgis.core import QgsApplication, QgsVectorLayer, QgsFeature, QgsGeometry, QgsPointXY, QgsRectangle
from qgis.gui import QgsMapCanvas
from qgis.PyQt.QtCore import QEventLoop, QTimer
from qgis.PyQt.QtGui import QImageReader
app = QgsApplication([], True)
app.initQgis()
assert os.path.commonpath([os.environ['QGIS_CUSTOM_CONFIG_PATH'], QgsApplication.qgisSettingsDirPath()]) == os.environ['QGIS_CUSTOM_CONFIG_PATH']
canvas = QgsMapCanvas()
layer = QgsVectorLayer('Point?crs=EPSG:4326', 'synthetic', 'memory')
feature = QgsFeature()
feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(0, 0)))
layer.dataProvider().addFeatures([feature])
canvas.setLayers([layer])
canvas.setExtent(QgsRectangle(-1, -1, 1, 1))
canvas.resize(200, 120)
canvas.show()
canvas.refresh()
loop = QEventLoop()
QTimer.singleShot(1000, loop.quit)
loop.exec_()
class Iface:
    def mapCanvas(self): return canvas
from qgis_mcp_server.qgis_mcp_plugin.qgis_mcp_plugin import QgisMCPServer
reply = QgisMCPServer(iface=Iface()).execute_command({
    'type': 'capture_map_canvas', 'params': {'path': sys.argv[1], 'width': 200, 'height': 120}})
reader = QImageReader(sys.argv[1])
print(json.dumps({'reply': reply, 'png': bytes(reader.format()).decode(),
                  'readable': not reader.read().isNull()}))
'''
    observed = {}

    class LocalQGIS:
        async def call_tool(self, tool_name, arguments):
            result = subprocess.run(
                ['/usr/bin/python3', '-c', script, arguments['path']], cwd=ROOT,
                env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen', 'QWA_ROOT': str(tmp_path)},
                capture_output=True, text=True, timeout=30, check=True,
            )
            observed.update(json.loads(result.stdout))
            return observed['reply']

    call = ToolCallPlan(step_id=1, tool_name='capture_map_canvas', arguments={'path': str(target)})
    registry = ToolRegistry.from_tool_specs([ToolSpec(name='capture_map_canvas')])
    attempt = asyncio.run(StepExecutor(LocalQGIS(), registry, tmp_path).execute(
        call, ExecutionTrace(trace_id='real-capture')))
    assert attempt.status == 'success', attempt.observation.message
    assert attempt.observation.outcome == 'completed'
    assert observed['reply']['result']['path'] == str(target)
    assert observed['png'] == 'png' and observed['readable']
    assert attempt.artifacts[0]['sha256'] == hashlib.sha256(target.read_bytes()).hexdigest()


@pytest.mark.external
@pytest.mark.parametrize('mode', ['missing', 'invalid', 'unreadable'])
def test_capture_reports_unknown_when_canvas_does_not_produce_a_readable_png(tmp_path, mode):
    target = tmp_path / 'shared/outputs/task-1/r1/image/map.png'
    script = '''
import os
os.environ['QGIS_CUSTOM_CONFIG_PATH'] = os.path.join(os.environ['QWA_ROOT'], f'qgis-config-{os.getpid()}')
import json, sys
from qgis.core import QgsApplication
from qgis.PyQt.QtCore import QSize
from qgis.PyQt.QtGui import QImage
app = QgsApplication([], True)
app.initQgis()
assert os.path.commonpath([os.environ['QGIS_CUSTOM_CONFIG_PATH'], QgsApplication.qgisSettingsDirPath()]) == os.environ['QGIS_CUSTOM_CONFIG_PATH']
from qgis_mcp_server.qgis_mcp_plugin.qgis_mcp_plugin import QgisMCPServer
class Canvas:
    def size(self): return QSize(100, 100)
    def resize(self, *args): pass
    def saveAsImage(self, path):
        if sys.argv[2] == 'invalid':
            open(path, 'wb').write(b'not an image')
        elif sys.argv[2] == 'unreadable':
            image = QImage(2, 2, QImage.Format_RGB32)
            image.fill(0)
            image.save(path, 'PNG')
            os.chmod(path, 0)
class Iface:
    def mapCanvas(self): return Canvas()
reply = QgisMCPServer(iface=Iface()).execute_command({
    'type': 'capture_map_canvas', 'params': {'path': sys.argv[1]}})
print(json.dumps(reply))
'''
    result = subprocess.run(
        ['/usr/bin/python3', '-c', script, str(target), mode], cwd=ROOT,
        env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen', 'QWA_ROOT': str(tmp_path)},
        capture_output=True, text=True, timeout=30, check=True,
    )
    reply = json.loads(result.stdout)
    assert reply['status'] == 'error'
    assert reply['outcome'] == 'unknown'
    if mode == 'missing':
        assert not target.exists()


@pytest.mark.external
def test_real_qgis_processing_creates_new_shapefile_and_keeps_input(tmp_path):
    source = tmp_path / 'source.geojson'
    source.write_text(json.dumps({
        'type': 'FeatureCollection',
        'features': [{'type': 'Feature', 'properties': {'value': 1},
                      'geometry': {'type': 'Point', 'coordinates': [1, 2]}}],
    }), encoding='utf-8')
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    relative = 'shared/outputs/task-1/r1/output/result.shp'
    target = tmp_path / relative
    script = '''
import os
os.environ['QGIS_CUSTOM_CONFIG_PATH'] = os.path.join(os.environ['QWA_ROOT'], f'qgis-config-{os.getpid()}')
import json, sys
sys.path.append('/usr/share/qgis/python/plugins')
from qgis.core import QgsApplication
app = QgsApplication([], False)
app.initQgis()
assert os.path.commonpath([os.environ['QGIS_CUSTOM_CONFIG_PATH'], QgsApplication.qgisSettingsDirPath()]) == os.environ['QGIS_CUSTOM_CONFIG_PATH']
from processing.core.Processing import Processing
Processing.initialize()
from qgis_mcp_server.qgis_mcp_plugin.qgis_mcp_plugin import QgisMCPServer
reply = QgisMCPServer().execute_command({'type': 'execute_processing', 'params': {
    'algorithm': 'native:reprojectlayer', 'parameters': {
        'INPUT': sys.argv[1], 'TARGET_CRS': 'EPSG:3857', 'OUTPUT': sys.argv[2]}}})
print(json.dumps(reply))
'''
    observed = {}

    class LocalQGIS:
        async def call_tool(self, tool_name, arguments):
            result = subprocess.run(
                ['/usr/bin/python3', '-c', script, str(source), relative], cwd=ROOT,
                env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen', 'QWA_ROOT': str(tmp_path)},
                capture_output=True, text=True, timeout=30, check=True,
            )
            observed.update(json.loads(result.stdout))
            return observed

    call = ToolCallPlan(step_id=1, tool_name='execute_processing', arguments={
        'algorithm': 'native:reprojectlayer',
        'parameters': {'INPUT': str(source), 'TARGET_CRS': 'EPSG:3857', 'OUTPUT': relative}})
    registry = ToolRegistry.from_tool_specs([ToolSpec(name='execute_processing')])
    attempt = asyncio.run(StepExecutor(LocalQGIS(), registry, tmp_path).execute(
        call, ExecutionTrace(trace_id='real-processing')))
    assert attempt.status == 'success', attempt.observation.message
    assert observed['result']['result']['OUTPUT'] == str(target)
    assert target.is_file() and target.with_suffix('.dbf').is_file() and target.with_suffix('.shx').is_file()
    assert {item['path'] for item in attempt.artifacts[0]['components']} == {
        str(target.with_suffix('.dbf')), str(target.with_suffix('.shx'))}
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


@pytest.mark.external
@pytest.mark.parametrize('mode', ['missing', 'wrong_path', 'unreadable', 'missing_companion'])
def test_plugin_processing_does_not_confirm_an_unverified_output(tmp_path, mode):
    source = tmp_path / 'source.geojson'
    source.write_text(json.dumps({
        'type': 'FeatureCollection',
        'features': [{'type': 'Feature', 'properties': {},
                      'geometry': {'type': 'Point', 'coordinates': [1, 2]}}],
    }), encoding='utf-8')
    target = tmp_path / 'shared/outputs/task-1/r1/output' / (
        'result.shp' if mode == 'missing_companion' else 'result.gpkg')
    script = '''
import os
os.environ['QGIS_CUSTOM_CONFIG_PATH'] = os.path.join(os.environ['QWA_ROOT'], f'qgis-config-{os.getpid()}')
import json, sys
sys.path.append('/usr/share/qgis/python/plugins')
from qgis.core import QgsApplication
app = QgsApplication([], False)
app.initQgis()
assert os.path.commonpath([os.environ['QGIS_CUSTOM_CONFIG_PATH'], QgsApplication.qgisSettingsDirPath()]) == os.environ['QGIS_CUSTOM_CONFIG_PATH']
from processing.core.Processing import Processing
Processing.initialize()
import processing
def fake(algorithm, parameters):
    target = parameters['OUTPUT']
    if sys.argv[3] == 'wrong_path': target += '.other'
    if sys.argv[3] != 'missing':
        with open(target, 'wb') as stream: stream.write(b'synthetic')
        if sys.argv[3] == 'unreadable': os.chmod(target, 0)
    return {'OUTPUT': target}
processing.run = fake
from qgis_mcp_server.qgis_mcp_plugin.qgis_mcp_plugin import QgisMCPServer
reply = QgisMCPServer().execute_command({'type': 'execute_processing', 'params': {
    'algorithm': 'native:reprojectlayer', 'parameters': {
        'INPUT': sys.argv[1], 'TARGET_CRS': 'EPSG:3857', 'OUTPUT': sys.argv[2]}}})
print(json.dumps(reply))
'''
    result = subprocess.run(
        ['/usr/bin/python3', '-c', script, str(source), str(target), mode], cwd=ROOT,
        env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen', 'QWA_ROOT': str(tmp_path)},
        capture_output=True, text=True, timeout=30, check=True,
    )
    reply = json.loads(result.stdout)
    assert reply['status'] == 'error'
    assert reply['outcome'] == 'unknown'


@pytest.mark.external
def test_processing_refuses_existing_shapefile_companion(tmp_path):
    source = tmp_path / 'source.geojson'
    source.write_text(json.dumps({
        'type': 'FeatureCollection',
        'features': [{'type': 'Feature', 'properties': {'value': 1},
                      'geometry': {'type': 'Point', 'coordinates': [1, 2]}}],
    }), encoding='utf-8')
    output = tmp_path / 'shared/outputs/task-1/r1/output/result.shp'
    output.parent.mkdir(parents=True)
    companion = output.with_suffix('.dbf')
    companion.write_bytes(b'pre-existing companion must survive')
    before = hashlib.sha256(companion.read_bytes()).hexdigest()
    script = '''
import os
os.environ['QGIS_CUSTOM_CONFIG_PATH'] = os.path.join(os.environ['QWA_ROOT'], f'qgis-config-{os.getpid()}')
import json, sys
sys.path.append('/usr/share/qgis/python/plugins')
from qgis.core import QgsApplication
app = QgsApplication([], False)
app.initQgis()
assert os.path.commonpath([os.environ['QGIS_CUSTOM_CONFIG_PATH'], QgsApplication.qgisSettingsDirPath()]) == os.environ['QGIS_CUSTOM_CONFIG_PATH']
from processing.core.Processing import Processing
Processing.initialize()
from qgis_mcp_server.qgis_mcp_plugin.qgis_mcp_plugin import QgisMCPServer
reply = QgisMCPServer().execute_command({'type': 'execute_processing', 'params': {
    'algorithm': 'native:reprojectlayer', 'parameters': {
        'INPUT': sys.argv[1], 'TARGET_CRS': 'EPSG:3857', 'OUTPUT': sys.argv[2]}}})
print(json.dumps(reply))
'''
    result = subprocess.run(
        ['/usr/bin/python3', '-c', script, str(source), str(output)],
        cwd=ROOT, env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen', 'QWA_ROOT': str(tmp_path)},
        capture_output=True, text=True, timeout=30, check=True,
    )
    reply = json.loads(result.stdout)
    assert reply['status'] == 'error'
    assert reply['outcome'] == 'not_started'
    assert not output.exists()
    assert hashlib.sha256(companion.read_bytes()).hexdigest() == before
