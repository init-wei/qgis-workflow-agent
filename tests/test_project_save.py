"""Controlled project saving across the public executor and QGIS plugin seam."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
from zipfile import ZIP_STORED, ZipFile

import pytest

from agent.tools.step_executor import StepExecutor
from agent.tools.tool_contracts import ExecutionTrace, ToolCallPlan, ToolSpec
from agent.tools.tool_registry import ToolRegistry


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.external
def test_plugin_rejects_memory_layer_before_reserving_project_directory(tmp_path):
    target = tmp_path / 'shared/outputs/task-1/r1/project/result.qgz'
    script = '''
import json, os, sys
os.environ['QGIS_CUSTOM_CONFIG_PATH'] = os.path.join(os.environ['QWA_ROOT'], 'qgis-config')
from qgis.core import QgsApplication, QgsProject, QgsVectorLayer, QgsFeature, QgsGeometry, QgsPointXY
app = QgsApplication([], False)
app.initQgis()
assert os.path.commonpath([os.environ['QGIS_CUSTOM_CONFIG_PATH'], QgsApplication.qgisSettingsDirPath()]) == os.environ['QGIS_CUSTOM_CONFIG_PATH']
project = QgsProject.instance()
layer = QgsVectorLayer('Point?crs=EPSG:4326', 'temporary points', 'memory')
feature = QgsFeature()
feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(1, 2)))
assert layer.dataProvider().addFeatures([feature])[0]
project.addMapLayer(layer)
before = (project.fileName(), project.isDirty(), layer.featureCount())
from qgis_mcp_server.qgis_mcp_plugin.qgis_mcp_plugin import QgisMCPServer
reply = QgisMCPServer().execute_command({'type': 'save_project', 'params': {'path': sys.argv[1]}})
print(json.dumps({'reply': reply, 'before': before,
                  'after': (project.fileName(), project.isDirty(), layer.featureCount())}))
'''
    result = subprocess.run(
        ['/usr/bin/python3', '-c', script, str(target)], cwd=ROOT,
        env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen', 'QWA_ROOT': str(tmp_path)},
        capture_output=True, text=True, timeout=30, check=True,
    )
    result = json.loads(result.stdout)
    assert result['reply']['status'] == 'error'
    assert result['reply']['outcome'] == 'not_started'
    assert 'memory' in result['reply']['message'].lower()
    assert result['before'] == result['after']
    assert result['after'][2] == 1
    assert not target.parent.exists()


@pytest.mark.external
def test_plugin_rejects_ogr_vsimem_source_before_reserving_project_directory(tmp_path):
    target = tmp_path / 'shared/outputs/task-1/r1/project/result.qgz'
    script = '''
import json, os, sys
os.environ['QGIS_CUSTOM_CONFIG_PATH'] = os.path.join(os.environ['QWA_ROOT'], 'qgis-config')
from osgeo import gdal
from qgis.core import QgsApplication, QgsProject, QgsVectorLayer
app = QgsApplication([], False)
app.initQgis()
assert os.path.commonpath([os.environ['QGIS_CUSTOM_CONFIG_PATH'], QgsApplication.qgisSettingsDirPath()]) == os.environ['QGIS_CUSTOM_CONFIG_PATH']
gdal.FileFromMemBuffer('/vsimem/temporary.geojson', open(sys.argv[1], 'rb').read())
layer = QgsVectorLayer('/vsimem/temporary.geojson', 'temporary file', 'ogr')
assert layer.isValid() and layer.featureCount() == 3
QgsProject.instance().addMapLayer(layer)
from qgis_mcp_server.qgis_mcp_plugin.qgis_mcp_plugin import QgisMCPServer
reply = QgisMCPServer().execute_command({'type': 'save_project', 'params': {'path': sys.argv[2]}})
print(json.dumps({'reply': reply, 'source': layer.source(), 'count': layer.featureCount()}))
'''
    result = subprocess.run(
        ['/usr/bin/python3', '-c', script, str(ROOT / 'tests/fixtures/gis/phase11_points.geojson'), str(target)],
        cwd=ROOT, env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen', 'QWA_ROOT': str(tmp_path)},
        capture_output=True, text=True, timeout=30, check=True,
    )
    result = json.loads(result.stdout)
    assert result['reply']['status'] == 'error'
    assert result['reply']['outcome'] == 'not_started'
    assert 'non-persistent' in result['reply']['message'].lower()
    assert result['source'] == '/vsimem/temporary.geojson' and result['count'] == 3
    assert not target.parent.exists()


@pytest.mark.external
def test_plugin_rejects_uncommitted_vector_edits_without_changing_source(tmp_path):
    source = tmp_path / 'points.geojson'
    shutil.copyfile(ROOT / 'tests/fixtures/gis/phase11_points.geojson', source)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    target = tmp_path / 'shared/outputs/task-1/r1/project/result.qgz'
    script = '''
import json, os, sys
os.environ['QGIS_CUSTOM_CONFIG_PATH'] = os.path.join(os.environ['QWA_ROOT'], 'qgis-config')
from qgis.core import QgsApplication, QgsProject, QgsVectorLayer
app = QgsApplication([], False)
app.initQgis()
assert os.path.commonpath([os.environ['QGIS_CUSTOM_CONFIG_PATH'], QgsApplication.qgisSettingsDirPath()]) == os.environ['QGIS_CUSTOM_CONFIG_PATH']
project = QgsProject.instance()
layer = QgsVectorLayer(sys.argv[1], 'points with pending edits', 'ogr')
assert layer.isValid()
project.addMapLayer(layer)
assert layer.startEditing()
feature = next(layer.getFeatures())
assert layer.changeAttributeValue(feature.id(), layer.fields().indexFromName('value'), 99)
assert layer.isModified()
def state():
    return {'filename': project.fileName(), 'dirty': project.isDirty(),
            'editable': layer.isEditable(), 'modified': layer.isModified(),
            'values': sorted(f['value'] for f in layer.getFeatures())}
before = state()
from qgis_mcp_server.qgis_mcp_plugin.qgis_mcp_plugin import QgisMCPServer
reply = QgisMCPServer().execute_command({'type': 'save_project', 'params': {'path': sys.argv[2]}})
print(json.dumps({'reply': reply, 'before': before, 'after': state()}))
'''
    result = subprocess.run(
        ['/usr/bin/python3', '-c', script, str(source), str(target)], cwd=ROOT,
        env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen', 'QWA_ROOT': str(tmp_path)},
        capture_output=True, text=True, timeout=30, check=True,
    )
    result = json.loads(result.stdout)
    assert result['reply']['status'] == 'error'
    assert result['reply']['outcome'] == 'not_started'
    assert 'uncommitted' in result['reply']['message'].lower()
    assert result['before'] == result['after']
    assert result['after']['editable'] and result['after']['modified']
    assert result['after']['values'] == [20, 30, 99]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
    assert not target.parent.exists()


@pytest.mark.external
def test_plugin_saves_project_with_persisted_geopackage_layer_selector(tmp_path):
    source = tmp_path / 'points.geojson'
    shutil.copyfile(ROOT / 'tests/fixtures/gis/phase11_points.geojson', source)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    geopackage = tmp_path / 'points.gpkg'
    target = tmp_path / 'shared/outputs/task-1/r1/project/result.qgz'
    script = '''
import hashlib, json, os, sys
os.environ['QGIS_CUSTOM_CONFIG_PATH'] = os.path.join(os.environ['QWA_ROOT'], 'qgis-config')
from qgis.core import QgsApplication, QgsProject, QgsVectorLayer, QgsVectorFileWriter
app = QgsApplication([], False)
app.initQgis()
assert os.path.commonpath([os.environ['QGIS_CUSTOM_CONFIG_PATH'], QgsApplication.qgisSettingsDirPath()]) == os.environ['QGIS_CUSTOM_CONFIG_PATH']
project = QgsProject.instance()
source_layer = QgsVectorLayer(sys.argv[1], 'source', 'ogr')
assert source_layer.isValid()
options = QgsVectorFileWriter.SaveVectorOptions()
options.driverName = 'GPKG'
options.layerName = 'known_points'
written = QgsVectorFileWriter.writeAsVectorFormatV3(source_layer, sys.argv[2], project.transformContext(), options)
assert written[0] == QgsVectorFileWriter.NoError, written
layer = QgsVectorLayer(sys.argv[2] + '|layername=known_points', 'persisted', 'ogr')
assert layer.isValid() and layer.featureCount() == 3
project.addMapLayer(layer)
def sha(path): return hashlib.sha256(open(path, 'rb').read()).hexdigest()
before = sha(sys.argv[2])
from qgis_mcp_server.qgis_mcp_plugin.qgis_mcp_plugin import QgisMCPServer
reply = QgisMCPServer().execute_command({'type': 'save_project', 'params': {'path': sys.argv[3]}})
saved = QgsProject()
readable = saved.read(sys.argv[3]) if reply['status'] == 'success' else False
layers = list(saved.mapLayers().values())
print(json.dumps({'reply': reply, 'readable': readable, 'before': before, 'after': sha(sys.argv[2]),
                  'source': layers[0].source() if layers else None,
                  'count': layers[0].featureCount() if layers else None}))
'''
    result = subprocess.run(
        ['/usr/bin/python3', '-c', script, str(source), str(geopackage), str(target)],
        cwd=ROOT, env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen', 'QWA_ROOT': str(tmp_path)},
        capture_output=True, text=True, timeout=30, check=True,
    )
    result = json.loads(result.stdout)
    assert result['reply']['status'] == 'success', result['reply']
    assert result['reply']['result'] == {'success': True, 'path': str(target), 'layer_count': 1}
    assert result['readable'] and result['source'] == f'{geopackage}|layername=known_points'
    assert result['count'] == 3
    assert result['before'] == result['after']
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash


@pytest.mark.external
def test_qgis_info_identifies_the_plugin_instance(tmp_path):
    script = '''
import json, os
os.environ['QGIS_CUSTOM_CONFIG_PATH'] = os.path.join(os.environ['QWA_ROOT'], f'qgis-config-{os.getpid()}')
from qgis.core import QgsApplication
app = QgsApplication([], False, os.path.join(os.environ['QWA_ROOT'], f'qgis-profile-{os.getpid()}'))
app.initQgis()
from qgis_mcp_server.qgis_mcp_plugin.qgis_mcp_plugin import QgisMCPServer
first = QgisMCPServer(port=0)
second = QgisMCPServer()
def info(server): return server.execute_command({'type': 'get_qgis_info'})['result']
first_before_start = info(first)
assert first.start()
started = info(first)
first.stop()
assert first.start()
restarted = info(first)
first.stop()
print(json.dumps({'pid': os.getpid(), 'first': first_before_start,
                  'again': info(second), 'second': info(second),
                  'started': started, 'restarted': restarted}))
'''
    result = subprocess.run(
        ['/usr/bin/python3', '-c', script], cwd=ROOT,
        env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen', 'QWA_ROOT': str(tmp_path)},
        capture_output=True, text=True, timeout=30, check=True,
    )
    info = json.loads(result.stdout)
    assert info['first']['process_id'] == info['again']['process_id'] == info['pid']
    assert info['again']['instance_id'] == info['second']['instance_id']
    assert info['first']['instance_id'] != info['second']['instance_id']
    assert info['started']['instance_id'] != info['restarted']['instance_id']
    assert Path(info['first']['profile_folder']).is_relative_to(tmp_path)


@pytest.mark.parametrize(('risk_level', 'requires_review', 'approved'), [
    ('low', False, False), ('low', False, True),
    ('high', True, False), ('high', False, True),
])
def test_save_cannot_bypass_review(tmp_path, risk_level, requires_review, approved):
    class Client:
        def __init__(self):
            self.calls = []

        async def call_tool(self, name, arguments):
            self.calls.append((name, arguments))
            return None

    client = Client()
    call = ToolCallPlan(step_id=1, tool_name='save_project', arguments={
        'path': str(tmp_path / 'shared/outputs/task-1/r1/project/result.qgz')},
        risk_level=risk_level, requires_review=requires_review)
    registry = ToolRegistry.from_tool_specs([ToolSpec(name='save_project')])
    attempt = asyncio.run(StepExecutor(client, registry, tmp_path).execute(
        call, ExecutionTrace(trace_id='unreviewed-save'), approved=approved))

    assert attempt.status == 'blocked'
    assert attempt.observation.outcome == 'not_started'
    assert client.calls == []


@pytest.mark.parametrize('mode', [
    'missing', 'wrong_path', 'unreadable', 'garbage', 'no_project', 'empty_project', 'bad_crc',
])
def test_executor_does_not_confirm_unverified_project_file(tmp_path, mode):
    requested = tmp_path / 'shared/outputs/task-1/r1/project/result.qgz'
    reported = requested.with_name('other.qgz') if mode == 'wrong_path' else requested

    class Client:
        async def call_tool(self, tool_name, arguments):
            if mode != 'missing':
                reported.parent.mkdir(parents=True)
                if mode in {'garbage', 'unreadable'}:
                    reported.write_bytes(b'not a project')
                    if mode == 'unreadable':
                        reported.chmod(0)
                else:
                    with ZipFile(reported, 'w', compression=ZIP_STORED) as archive:
                        if mode == 'no_project':
                            archive.writestr('other.txt', 'no project here')
                        else:
                            archive.writestr('project.qgs', b'' if mode == 'empty_project' else b'<qgis/>')
                    if mode == 'bad_crc':
                        reported.write_bytes(reported.read_bytes().replace(b'<qgis/>', b'<QGIS/>', 1))
            return {'status': 'success', 'result': {
                'success': True, 'path': str(reported), 'layer_count': 0}}

    call = ToolCallPlan(step_id=1, tool_name='save_project', arguments={'path': str(requested)},
                        risk_level='high', requires_review=True)
    registry = ToolRegistry.from_tool_specs([ToolSpec(name='save_project')])
    attempt = asyncio.run(StepExecutor(Client(), registry, tmp_path).execute(
        call, ExecutionTrace(trace_id='invalid-save'), approved=True))
    assert attempt.status == 'failed'
    assert attempt.observation.outcome == 'unknown'
    assert attempt.observation.retryable is False


@pytest.mark.external
@pytest.mark.parametrize('mode', [
    'existing_file', 'existing_directory', 'outside', 'symlink_inside', 'symlink_outside',
    'suffix', 'original_input',
])
def test_plugin_rejects_unsafe_project_destination_before_writing(tmp_path, mode):
    source = tmp_path / 'points.geojson'
    shutil.copyfile(ROOT / 'tests/fixtures/gis/phase11_points.geojson', source)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    target = tmp_path / 'shared/outputs/task-1/r1/project/result.qgz'
    existing = None
    if mode == 'existing_file':
        target.parent.mkdir(parents=True)
        target.write_bytes(b'existing project')
        existing = target.read_bytes()
    elif mode == 'existing_directory':
        target.parent.mkdir(parents=True)
        (target.parent / 'sidecar.qgd').write_bytes(b'existing sidecar')
    elif mode == 'outside':
        target = tmp_path / 'outside/result.qgz'
    elif mode in {'symlink_inside', 'symlink_outside'}:
        target.parent.parent.mkdir(parents=True)
        link_target = (target.parent.parent / 'other') if mode == 'symlink_inside' else (tmp_path / 'outside')
        link_target.mkdir()
        target.parent.symlink_to(link_target, target_is_directory=True)
    elif mode == 'suffix':
        target = target.with_suffix('.qgs')
    elif mode == 'original_input':
        target = source
    script = '''
import json, os, sys
os.environ['QGIS_CUSTOM_CONFIG_PATH'] = os.path.join(os.environ['QWA_ROOT'], f'qgis-config-{os.getpid()}')
from qgis.core import QgsApplication, QgsProject, QgsVectorLayer
app = QgsApplication([], False, os.path.join(os.environ['QWA_ROOT'], f'qgis-profile-{os.getpid()}'))
app.initQgis()
project = QgsProject.instance()
layer = QgsVectorLayer(sys.argv[1], 'known points', 'ogr')
assert layer.isValid()
project.addMapLayer(layer)
from qgis_mcp_server.qgis_mcp_plugin.qgis_mcp_plugin import QgisMCPServer
reply = QgisMCPServer().execute_command({'type': 'save_project', 'params': {'path': sys.argv[2]}})
print(json.dumps(reply))
'''
    result = subprocess.run(
        ['/usr/bin/python3', '-c', script, str(source), str(target)], cwd=ROOT,
        env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen', 'QWA_ROOT': str(tmp_path)},
        capture_output=True, text=True, timeout=30, check=True,
    )
    reply = json.loads(result.stdout)
    assert reply['status'] == 'error'
    assert reply['outcome'] == 'not_started'
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
    if existing is not None:
        assert target.read_bytes() == existing
    if mode == 'existing_directory':
        assert (target.parent / 'sidecar.qgd').read_bytes() == b'existing sidecar'
        assert not target.exists()


@pytest.mark.external
@pytest.mark.parametrize('mode', ['false', 'missing', 'unreadable', 'corrupt', 'bad_crc'])
def test_plugin_does_not_confirm_failed_or_unreadable_project_write(tmp_path, mode):
    target = tmp_path / 'shared/outputs/task-1/r1/project/result.qgz'
    script = '''
import json, os, sys, zipfile
os.environ['QGIS_CUSTOM_CONFIG_PATH'] = os.path.join(os.environ['QWA_ROOT'], f'qgis-config-{os.getpid()}')
from qgis.core import QgsApplication, QgsProject
app = QgsApplication([], False, os.path.join(os.environ['QWA_ROOT'], f'qgis-profile-{os.getpid()}'))
app.initQgis()
real_project = QgsProject
class Project:
    def fileName(self): return '/original/project.qgz'
    def isDirty(self): return True
    def mapLayers(self): return {}
    def setFileName(self, value): self.filename = value
    def setDirty(self, value): self.dirty = value
    def write(self, path):
        if sys.argv[2] == 'false':
            open(path, 'wb').write(b'partial')
            return False
        if sys.argv[2] == 'missing': return True
        if sys.argv[2] in {'unreadable', 'corrupt'}:
            open(path, 'wb').write(b'not a QGIS project')
            if sys.argv[2] == 'unreadable': os.chmod(path, 0)
            return True
        assert real_project().write(path)
        with zipfile.ZipFile(path, 'a', compression=zipfile.ZIP_STORED) as archive:
            archive.writestr('other.txt', b'untouched-data')
        data = open(path, 'rb').read().replace(b'untouched-data', b'UNTOUCHED-DATA', 1)
        open(path, 'wb').write(data)
        return True
project = Project()
class Factory:
    @staticmethod
    def instance(): return project
    def __new__(cls): return real_project()
import qgis_mcp_server.qgis_mcp_plugin.qgis_mcp_plugin as plugin
plugin.QgsProject = Factory
reply = plugin.QgisMCPServer().execute_command({'type': 'save_project', 'params': {'path': sys.argv[1]}})
print(json.dumps({'reply': reply, 'filename': project.filename, 'dirty': project.dirty}))
'''
    result = subprocess.run(
        ['/usr/bin/python3', '-c', script, str(target), mode], cwd=ROOT,
        env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen', 'QWA_ROOT': str(tmp_path)},
        capture_output=True, text=True, timeout=30, check=True,
    )
    reply = json.loads(result.stdout)
    assert reply['reply']['status'] == 'error'
    assert reply['reply']['outcome'] == 'unknown'
    assert reply['filename'] == '/original/project.qgz'
    assert reply['dirty'] is True


@pytest.mark.external
def test_two_qgis_writers_cannot_claim_the_same_project_directory(tmp_path):
    source = tmp_path / 'points.geojson'
    shutil.copyfile(ROOT / 'tests/fixtures/gis/phase11_points.geojson', source)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    target = tmp_path / 'shared/outputs/task-1/r1/project/result.qgz'
    script = '''
import json, os, sys
os.environ['QGIS_CUSTOM_CONFIG_PATH'] = os.path.join(os.environ['QWA_ROOT'], f'qgis-config-{os.getpid()}')
from qgis.core import QgsApplication, QgsProject, QgsVectorLayer
app = QgsApplication([], False, os.path.join(os.environ['QWA_ROOT'], f'qgis-profile-{os.getpid()}'))
app.initQgis()
project = QgsProject.instance()
layer = QgsVectorLayer(sys.argv[1], 'known points', 'ogr')
assert layer.isValid()
project.addMapLayer(layer)
from qgis_mcp_server.qgis_mcp_plugin.qgis_mcp_plugin import QgisMCPServer
print('ready', flush=True)
sys.stdin.readline()
reply = QgisMCPServer().execute_command({'type': 'save_project', 'params': {'path': sys.argv[2]}})
print(json.dumps(reply), flush=True)
'''
    env = {**os.environ, 'QT_QPA_PLATFORM': 'offscreen', 'QWA_ROOT': str(tmp_path)}
    processes = [subprocess.Popen(
        ['/usr/bin/python3', '-c', script, str(source), str(target)], cwd=ROOT, env=env,
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
        assert sorted(reply['status'] for reply in replies) == ['error', 'success']
        assert next(reply for reply in replies if reply['status'] == 'error')['outcome'] == 'not_started'
        with ZipFile(target) as archive:
            assert archive.testzip() is None
        assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate()


@pytest.mark.external
def test_approved_save_reopens_real_qgis_project_without_changing_inputs(tmp_path):
    source = tmp_path / 'points.geojson'
    shutil.copyfile(ROOT / 'tests/fixtures/gis/phase11_points.geojson', source)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    original = tmp_path / 'original.qgz'
    target = tmp_path / 'shared/outputs/task-1/r1/project/result.qgz'
    script = '''
import hashlib, json, os, sys
os.environ['QGIS_CUSTOM_CONFIG_PATH'] = os.path.join(os.environ['QWA_ROOT'], f'qgis-config-{os.getpid()}')
from qgis.core import QgsApplication, QgsProject, QgsVectorLayer
app = QgsApplication([], False, os.path.join(os.environ['QWA_ROOT'], f'qgis-profile-{os.getpid()}'))
app.initQgis()
project = QgsProject.instance()
layer = QgsVectorLayer(sys.argv[1], 'known points', 'ogr')
assert layer.isValid()
project.addMapLayer(layer)
assert project.write(sys.argv[2])
project.setTitle('edited project')
original_hash = hashlib.sha256(open(sys.argv[2], 'rb').read()).hexdigest()
original_dirty = project.isDirty()
from qgis_mcp_server.qgis_mcp_plugin.qgis_mcp_plugin import QgisMCPServer
reply = QgisMCPServer().execute_command({'type': 'save_project', 'params': {'path': sys.argv[3]}})
reopened = QgsProject()
readable = reopened.read(sys.argv[3]) if reply['status'] == 'success' else False
layers = list(reopened.mapLayers().values())
features = sorted([[f['id'], f['group'], f['value'], f.geometry().asPoint().x(),
                    f.geometry().asPoint().y()] for f in layers[0].getFeatures()]) if layers else []
print(json.dumps({'reply': reply, 'readable': readable,
    'layers': [{'source': l.source(), 'features': l.featureCount()} for l in layers],
    'features': features,
    'title': reopened.title(), 'current_filename': project.fileName(),
    'current_dirty': project.isDirty(), 'original_dirty': original_dirty,
    'original_before': original_hash,
    'original_after': hashlib.sha256(open(sys.argv[2], 'rb').read()).hexdigest()}))
'''
    observed = {}
    calls = []

    class LocalQGIS:
        async def call_tool(self, tool_name, arguments):
            assert tool_name == 'save_project'
            calls.append((tool_name, arguments))
            result = subprocess.run(
                ['/usr/bin/python3', '-c', script, str(source), str(original), arguments['path']],
                cwd=ROOT, env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen', 'QWA_ROOT': str(tmp_path)},
                capture_output=True, text=True, timeout=30, check=True,
            )
            observed.update(json.loads(result.stdout))
            return observed['reply']

    call = ToolCallPlan(step_id=1, tool_name='save_project', arguments={'path': str(target)},
                        risk_level='high', requires_review=True)
    registry = ToolRegistry.from_tool_specs([ToolSpec(name='save_project')])
    attempt = asyncio.run(StepExecutor(LocalQGIS(), registry, tmp_path).execute(
        call, ExecutionTrace(trace_id='real-save'), approved=True))

    assert attempt.status == 'success', attempt.observation.message
    assert calls == [('save_project', {'path': str(target)})]
    assert attempt.observation.outcome == 'completed'
    assert observed['reply']['result'] == {'success': True, 'path': str(target), 'layer_count': 1}
    assert observed['readable'] and observed['layers'] == [{'source': str(source), 'features': 3}]
    assert [feature[:3] for feature in observed['features']] == [
        [1, 'a', 10], [2, 'a', 20], [3, 'b', 30]]
    for actual, expected in zip(observed['features'], [(116.38, 39.9), (116.39, 39.91), (116.4, 39.92)]):
        assert actual[3:] == pytest.approx(expected)
    assert observed['title'] == 'edited project'
    assert observed['current_filename'] == str(original)
    assert observed['current_dirty'] == observed['original_dirty'] is True
    assert observed['original_before'] == observed['original_after']
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
    assert attempt.artifacts[0]['size_bytes'] == target.stat().st_size
    assert attempt.artifacts[0]['sha256'] == hashlib.sha256(target.read_bytes()).hexdigest()
