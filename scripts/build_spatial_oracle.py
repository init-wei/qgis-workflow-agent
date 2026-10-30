#!/usr/bin/env python3
"""Rebuild the fixed T03 oracle with stdlib mathematics, without GIS libraries.

This deliberately handles only the checked-in point fixture and the parameters
documented alongside it. It is not a general-purpose projection implementation.
"""

import argparse
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INPUT_PATH = "tests/fixtures/gis/phase11_points.geojson"
INPUT_SHA256 = "bee3424d61d00f88e25ce8a123ec0062abc65a7d8dc9189823f4e975327c8b5d"
SNYDER_SOURCE = "https://pubs.usgs.gov/pp/1395/report.pdf"
QGIS_SOURCE = (
    "https://docs.qgis.org/3.40/en/docs/user_manual/processing_algs/"
    "qgis/vectorgeometry.html#buffer"
)
QGIS_IMPLEMENTATION_SOURCE = (
    "https://github.com/qgis/QGIS/blob/release-3_40/"
    "src/analysis/processing/qgsalgorithmbuffer.cpp"
)


def transverse_mercator(
    longitude_deg: float,
    latitude_deg: float,
    *,
    semi_major_m: float,
    eccentricity_squared: float,
    central_meridian_deg: float,
    scale: float,
    false_easting_m: float,
    false_northing_m: float,
) -> tuple[float, float]:
    """Snyder equations 8-9, 8-10, and 3-21, for zero latitude of origin."""
    phi = math.radians(latitude_deg)
    longitude_difference = math.radians(longitude_deg - central_meridian_deg)
    e2 = eccentricity_squared
    ep2 = e2 / (1 - e2)
    cos_phi = math.cos(phi)
    n = semi_major_m / math.sqrt(1 - e2 * math.sin(phi) ** 2)
    t = math.tan(phi) ** 2
    c = ep2 * cos_phi**2
    a = longitude_difference * cos_phi
    meridional_arc = semi_major_m * (
        (1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256) * phi
        - (3 * e2 / 8 + 3 * e2**2 / 32 + 45 * e2**3 / 1024) * math.sin(2 * phi)
        + (15 * e2**2 / 256 + 45 * e2**3 / 1024) * math.sin(4 * phi)
        - (35 * e2**3 / 3072) * math.sin(6 * phi)
    )
    easting = false_easting_m + scale * n * (
        a
        + (1 - t + c) * a**3 / 6
        + (5 - 18 * t + t**2 + 72 * c - 58 * ep2) * a**5 / 120
    )
    northing = false_northing_m + scale * (
        meridional_arc
        + n
        * math.tan(phi)
        * (
            a**2 / 2
            + (5 - t + 9 * c + 4 * c**2) * a**4 / 24
            + (61 - 58 * t + t**2 + 600 * c - 330 * ep2) * a**6 / 720
        )
    )
    return easting, northing


def build_oracle(input_bytes: bytes) -> dict:
    actual_sha256 = hashlib.sha256(input_bytes).hexdigest()
    if actual_sha256 != INPUT_SHA256:
        raise ValueError(f"input SHA-256 mismatch: {actual_sha256} != {INPUT_SHA256}")
    fixture = json.loads(input_bytes)
    semi_major_m = 6378137.0
    inverse_flattening = 298.257223563
    flattening = 1 / inverse_flattening
    eccentricity_squared = flattening * (2 - flattening)
    points = []
    for feature in fixture["features"]:
        longitude, latitude = feature["geometry"]["coordinates"]
        easting, northing = transverse_mercator(
            longitude,
            latitude,
            semi_major_m=semi_major_m,
            eccentricity_squared=eccentricity_squared,
            central_meridian_deg=117.0,
            scale=0.9996,
            false_easting_m=500000.0,
            false_northing_m=0.0,
        )
        points.append(
            {
                "attributes": feature["properties"],
                "input_coordinates_deg": [longitude, latitude],
                "expected_coordinates_m": [round(easting, 3), round(northing, 3)],
            }
        )

    distance = 100.0
    segments_per_quadrant = 8
    sides = 4 * segments_per_quadrant
    polygon_area = 0.5 * sides * distance**2 * math.sin(2 * math.pi / sides)
    edge_length = 2 * distance * math.sin(math.pi / sides)
    return {
        "schema_version": 1,
        "input": {
            "path": INPUT_PATH,
            "sha256": INPUT_SHA256,
            "crs": "EPSG:4326",
            "coordinate_order": "longitude, latitude",
            "geometry_type": "Point",
            "feature_count": len(points),
        },
        "reprojection": {
            "target_crs": "EPSG:32650",
            "coordinate_order": "easting, northing",
            "unit": "m",
            "projection_parameters": {
                "semi_major_axis_m": semi_major_m,
                "inverse_flattening": inverse_flattening,
                "latitude_of_origin_deg": 0.0,
                "central_meridian_deg": 117.0,
                "scale_factor": 0.9996,
                "false_easting_m": 500000.0,
                "false_northing_m": 0.0,
            },
            "tolerance_m_per_axis": 0.02,
            "features": points,
        },
        "buffer": {
            "input": "reprojection output",
            "crs": "EPSG:32650",
            "parameters": {
                "DISTANCE": distance,
                "SEGMENTS": segments_per_quadrant,
                "END_CAP_STYLE": 0,
                "JOIN_STYLE": 0,
                "MITER_LIMIT": 2.0,
                "DISSOLVE": False,
                "SEPARATE_DISJOINT": False,
            },
            "expected_per_feature": {
                "geometry_types": ["Polygon", "MultiPolygon"],
                "native_buffer_sink_type": "MultiPolygon",
                "polygon_part_count": 1,
                "ring_count_per_polygon": 1,
                "edges_per_ring": sides,
                "coordinates_in_closed_ring": sides + 1,
                "vertex_radius_m": distance,
                "edge_length_m": round(edge_length, 6),
                "area_m2": round(polygon_area, 6),
                "perimeter_m": round(sides * edge_length, 6),
                "circle_area_upper_bound_m2": round(math.pi * distance**2, 6),
            },
            "expected_feature_count": len(points),
            "attributes_and_centers": "match reprojection features by attributes",
            "absolute_tolerances": {
                "vertex_radius_from_frozen_center_m": 0.03,
                "vertex_radius_from_actual_reprojected_center_m": 0.001,
                "edge_length_m": 0.001,
                "area_m2": 0.05,
                "perimeter_m": 0.005,
            },
        },
        "method": {
            "reprojection": "Snyder 1987 equations 8-9, 8-10, 3-21; latitude of origin zero; standard-library math",
            "reprojection_source": SNYDER_SOURCE,
            "buffer": "inscribed regular 32-gon: area=N*r^2*sin(2*pi/N)/2; edge=2*r*sin(pi/N); perimeter=N*edge",
            "qgis_parameter_source": QGIS_SOURCE,
            "qgis_buffer_implementation_source": QGIS_IMPLEMENTATION_SOURCE,
            "scope": "this fixed point fixture and these parameters only",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / INPUT_PATH)
    parser.add_argument(
        "--output", type=Path, help="new file only; default writes to stdout"
    )
    args = parser.parse_args()
    try:
        result = build_oracle(args.input.read_bytes())
        output = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.output is None:
            print(output, end="")
        else:
            with args.output.open("x", encoding="utf-8") as destination:
                destination.write(output)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"{parser.prog}: {exc}\n")


if __name__ == "__main__":
    main()
