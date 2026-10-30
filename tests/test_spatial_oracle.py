"""Check the independent oracle against a published calculation and its input."""

import pytest

from scripts.build_spatial_oracle import (
    INPUT_PATH,
    ROOT,
    build_oracle,
    transverse_mercator,
)


def test_projection_matches_snyder_published_utm_example():
    # Snyder, USGS Professional Paper 1395, Appendix A, p. 270: Clarke 1866,
    # longitude 73.5 W, latitude 40.5 N, central meridian 75 W. The printed
    # x/y are rounded to 0.1 m and exclude the 500,000 m false easting.
    result = transverse_mercator(
        -73.5,
        40.5,
        semi_major_m=6378206.4,
        eccentricity_squared=0.00676866,
        central_meridian_deg=-75.0,
        scale=0.9996,
        false_easting_m=0.0,
        false_northing_m=0.0,
    )
    assert result == pytest.approx((127106.5, 4484124.4), abs=0.1)


def test_oracle_refuses_a_changed_fixture():
    original = (ROOT / INPUT_PATH).read_bytes()
    with pytest.raises(ValueError, match="input SHA-256 mismatch"):
        build_oracle(original + b" ")
