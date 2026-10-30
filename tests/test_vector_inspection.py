"""Read-only GIS metadata CLI, exercised with the QGIS distribution's GDAL."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
COMMAND = ["/usr/bin/python3", str(ROOT / "scripts/inspect_vector.py")]


def inspect(path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(COMMAND + [str(path)], capture_output=True, text=True, timeout=15)


def create_container(path: Path, *, measured: bool = False, no_crs: bool = False) -> None:
    script = """
import sys
from osgeo import ogr, osr
ogr.UseExceptions()
path, measured, no_crs = sys.argv[1:]
ds = ogr.GetDriverByName('GPKG').CreateDataSource(path)
srs = None
if no_crs != 'yes':
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(32650)
for name, wkt, value in [('control_points', 'POINT (500000 0)', 7),
                          ('study_area', 'POLYGON ((499900 -100,500100 -100,500100 100,499900 100,499900 -100))', 9)]:
    geometry = ogr.CreateGeometryFromWkt(wkt)
    if measured == 'yes' and name == 'control_points':
        geometry = ogr.CreateGeometryFromWkt('POINT M (500000 0 42)')
    layer = ds.CreateLayer(name, srs=srs, geom_type=geometry.GetGeometryType())
    layer.CreateField(ogr.FieldDefn('value', ogr.OFTInteger))
    feature = ogr.Feature(layer.GetLayerDefn())
    feature.SetField('value', value)
    feature.SetGeometry(geometry)
    layer.CreateFeature(feature)
ds = None
"""
    subprocess.run(
        ["/usr/bin/python3", "-c", script, str(path), "yes" if measured else "no", "yes" if no_crs else "no"],
        check=True, capture_output=True, text=True, timeout=15,
    )


def test_metadata_matches_known_geojson_without_changing_input(tmp_path):
    source = ROOT / "tests/fixtures/gis/phase11_points.geojson"
    path = tmp_path / "upload.geojson"
    shutil.copyfile(source, path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()

    result = inspect(path)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"layers": [{
        "layer_name": "phase11_points", "crs": "EPSG:4326", "geometry": "Point",
        "feature_count": 3, "fields": ["id", "group", "value"],
        "field_types": {"id": "Integer", "group": "String", "value": "Integer"}, "is_2d": True,
    }]}
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert [item.name for item in tmp_path.iterdir()] == [path.name]


def test_three_dimensional_geojson_is_not_reported_as_two_dimensional(tmp_path):
    path = tmp_path / "three-d.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {},
        "geometry": {"type": "Point", "coordinates": [117, 0, 25]},
    }]}))

    result = inspect(path)

    assert result.returncode == 0, result.stderr
    layer = json.loads(result.stdout)["layers"][0]
    assert layer["feature_count"] == 1
    assert layer["is_2d"] is False


def test_all_geopackage_layers_are_listed_for_explicit_selection(tmp_path):
    path = tmp_path / "two-layers.gpkg"
    create_container(path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()

    result = inspect(path)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"layers": [
        {"layer_name": "control_points", "crs": "EPSG:32650", "geometry": "Point",
         "feature_count": 1, "fields": ["value"], "field_types": {"value": "Integer"}, "is_2d": True},
        {"layer_name": "study_area", "crs": "EPSG:32650", "geometry": "Polygon",
         "feature_count": 1, "fields": ["value"], "field_types": {"value": "Integer"}, "is_2d": True},
    ]}
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert [item.name for item in tmp_path.iterdir()] == [path.name]


def test_measured_geometry_is_outside_two_dimensional_scope(tmp_path):
    path = tmp_path / "measured.gpkg"
    create_container(path, measured=True)

    result = inspect(path)

    assert result.returncode == 0, result.stderr
    layers = json.loads(result.stdout)["layers"]
    assert layers[0]["is_2d"] is False
    assert layers[1]["is_2d"] is True


def test_stored_third_coordinate_is_detected_despite_two_dimensional_layer_header(tmp_path):
    path = tmp_path / "inconsistent-header.gpkg"
    subprocess.run([
        "/usr/bin/python3", "-c", """
import sys
from osgeo import ogr
ogr.UseExceptions()
ds = ogr.GetDriverByName('GPKG').CreateDataSource(sys.argv[1])
layer = ds.CreateLayer('mixed', geom_type=ogr.wkbUnknown)
feature = ogr.Feature(layer.GetLayerDefn())
feature.SetGeometry(ogr.CreateGeometryFromWkt('POINT Z (117 0 25)'))
layer.CreateFeature(feature)
ds = None
""", str(path),
    ], check=True, capture_output=True, text=True, timeout=15)

    result = inspect(path)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["layers"][0]["is_2d"] is False


def test_geopackage_undefined_crs_stays_unknown(tmp_path):
    path = tmp_path / "undefined.gpkg"
    create_container(path, no_crs=True)

    result = inspect(path)

    assert result.returncode == 0, result.stderr
    assert all(layer["crs"] is None for layer in json.loads(result.stdout)["layers"])


@pytest.mark.parametrize("contents", ["invalid JSON", '<OGRVRTDataSource></OGRVRTDataSource>'])
def test_invalid_or_unsupported_format_fails_without_exposing_path(tmp_path, contents):
    path = tmp_path / "private-name.geojson"
    path.write_text(contents)

    result = inspect(path)

    assert result.returncode != 0
    assert json.loads(result.stdout)["error"]
    assert str(tmp_path) not in result.stdout + result.stderr


def test_missing_input_fails_without_exposing_path(tmp_path):
    result = inspect(tmp_path / "private-name.geojson")

    assert result.returncode != 0
    assert json.loads(result.stdout)["error"]
    assert str(tmp_path) not in result.stdout + result.stderr
