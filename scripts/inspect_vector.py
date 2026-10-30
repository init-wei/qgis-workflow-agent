"""Inspect local vectors read-only with the QGIS distribution's system GDAL.

Invoke with /usr/bin/python3. The Agent virtual environment does not import GDAL.
The caller owns controlled paths, upload limits, layer selection, and timeouts.
"""

import json
from pathlib import Path
import sys


def inspect_vector(path: str) -> dict:
    from osgeo import gdal, ogr

    gdal.UseExceptions()
    gdal.SetConfigOption("GDAL_PAM_ENABLED", "NO")
    gdal.PushErrorHandler("CPLQuietErrorHandler")
    try:
        if not Path(path).is_file():
            raise ValueError("Input is not a local file")
        dataset = gdal.OpenEx(
            path, gdal.OF_VECTOR | gdal.OF_READONLY,
            allowed_drivers=["GeoJSON", "GPKG"],
        )
        if dataset is None or dataset.GetLayerCount() == 0:
            raise ValueError("Input has no supported vector layers")
        try:
            layers = []
            for index in range(dataset.GetLayerCount()):
                layer = dataset.GetLayerByIndex(index)
                spatial_reference = layer.GetSpatialRef()
                crs = None
                if spatial_reference is not None:
                    authority = spatial_reference.GetAuthorityName(None)
                    code = spatial_reference.GetAuthorityCode(None)
                    crs = f"{authority}:{code}" if authority and code else spatial_reference.ExportToWkt()
                geometry_type = layer.GetGeomType()
                definition = layer.GetLayerDefn()
                is_2d = not bool(ogr.GT_HasZ(geometry_type) or ogr.GT_HasM(geometry_type))
                if is_2d:
                    # A container header may disagree with the stored geometries.
                    for feature in layer:
                        geometry = feature.GetGeometryRef()
                        if geometry is not None and (
                            ogr.GT_HasZ(geometry.GetGeometryType())
                            or ogr.GT_HasM(geometry.GetGeometryType())
                        ):
                            is_2d = False
                            break
                layers.append({
                    "layer_name": layer.GetName(),
                    "crs": crs,
                    "geometry": ogr.GeometryTypeToName(ogr.GT_Flatten(geometry_type)),
                    "feature_count": layer.GetFeatureCount(),
                    "fields": [definition.GetFieldDefn(i).GetName() for i in range(definition.GetFieldCount())],
                    "is_2d": is_2d,
                })
            return {"layers": layers}
        finally:
            dataset.Close()
    finally:
        gdal.PopErrorHandler()


def main() -> int:
    try:
        if len(sys.argv) != 2:
            raise ValueError("Expected one local input")
        result = inspect_vector(sys.argv[1])
    except Exception:
        # GDAL errors may include private paths. The API gets a stable safe error.
        print(json.dumps({"error": "Could not inspect a local GeoJSON or GeoPackage input."}))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
