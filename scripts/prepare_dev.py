"""Prepare disposable development files without overwriting input data."""

from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]


def prepare() -> None:
    for directory in ("shared/uploads", "shared/outputs", "shared/skills", ".target", ".runtime"):
        (ROOT / directory).mkdir(parents=True, exist_ok=True)
    source = ROOT / "tests/fixtures/gis/phase11_points.geojson"
    target = ROOT / "shared/uploads/phase11_points.geojson"
    if target.exists() and target.read_bytes() != source.read_bytes():
        raise SystemExit("Existing sample differs; refusing to overwrite shared/uploads input.")
    if not target.exists():
        shutil.copyfile(source, target)
    plugins = ROOT / ".runtime/qgis/profiles/qwa-v1/python/plugins"
    plugins.mkdir(parents=True, exist_ok=True)
    link = plugins / "qgis_mcp_plugin"
    plugin = ROOT / "qgis_mcp_server/qgis_mcp_plugin"
    if link.is_symlink() and link.resolve() == plugin:
        pass
    elif link.exists() or link.is_symlink():
        raise SystemExit("Existing QGIS plugin path differs; refusing to overwrite it.")
    else:
        link.symlink_to(plugin, target_is_directory=True)
    print("Prepared synthetic input and isolated QGIS profile/plugin link.")


if __name__ == "__main__":
    prepare()
