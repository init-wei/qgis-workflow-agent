"""Materialize the separate T06 development inputs with distribution GDAL."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
DEFINITION = ROOT / 'tests/fixtures/gis/t06_reuse.json'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    arguments = parser.parse_args()
    os.environ['GDAL_PAM_ENABLED'] = 'NO'
    from osgeo import gdal, ogr, osr

    gdal.UseExceptions()
    ogr.UseExceptions()
    original = DEFINITION.read_bytes()
    definition = json.loads(original)
    target = arguments.output_dir.absolute()
    target.mkdir(mode=0o700)
    files = {}
    for name, item in definition['datasets'].items():
        filename = item['filename']
        if Path(filename).name != filename or Path(filename).suffix not in {'.geojson', '.gpkg'}:
            raise ValueError('Fixture filename must be a single supported file name')
        path = target / filename
        if path.suffix == '.geojson':
            collection = {'type': 'FeatureCollection', 'features': [
                {'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': row['coordinates']},
                 'properties': row['properties']} for row in item['features']
            ]}
            with path.open('x', encoding='utf-8') as stream:
                json.dump(collection, stream, ensure_ascii=False)
        else:
            if path.exists():
                raise FileExistsError(path)
            dataset = ogr.GetDriverByName('GPKG').CreateDataSource(str(path))
            crs = osr.SpatialReference()
            crs.SetFromUserInput(item['crs'])
            crs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
            layer = dataset.CreateLayer(item['layer'], crs, ogr.wkbPolygon)
            types = {'Integer': ogr.OFTInteger, 'String': ogr.OFTString, 'Real': ogr.OFTReal}
            for field, field_type in item['fields'].items():
                layer.CreateField(ogr.FieldDefn(field, types[field_type]))
            for row in item['features']:
                xmin, ymin, xmax, ymax = row['bounds']
                ring = ogr.Geometry(ogr.wkbLinearRing)
                for x, y in [(xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax), (xmin, ymin)]:
                    ring.AddPoint_2D(x, y)
                geometry = ogr.Geometry(ogr.wkbPolygon)
                geometry.AddGeometry(ring)
                feature = ogr.Feature(layer.GetLayerDefn())
                for field, value in row['properties'].items():
                    if value is not None:
                        feature.SetField(field, value)
                feature.SetGeometry(geometry)
                layer.CreateFeature(feature)
            feature = geometry = ring = layer = None
            dataset.Destroy()
        for suffix in ('-wal', '-shm', '-journal'):
            if Path(str(path) + suffix).exists():
                raise RuntimeError('Fixture has a transaction sidecar')
        files[name] = {'filename': filename, 'layer': item.get('layer'), 'crs': item['crs'],
                       'feature_count': len(item['features']),
                       'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    if DEFINITION.read_bytes() != original:
        raise RuntimeError('Fixture definition changed during materialization')
    manifest = {'definition_sha256': hashlib.sha256(original).hexdigest(),
                'generator_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'gdal_version': gdal.VersionInfo('RELEASE_NAME'), 'datasets': files,
                'command': [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]]}
    with (target / 'manifest.json').open('x', encoding='utf-8') as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == '__main__':
    main()
