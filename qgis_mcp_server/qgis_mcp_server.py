#!/usr/bin/env python3
"""
QGIS MCP Client - Simple client to connect to the QGIS MCP server
"""

import logging
import os
from contextlib import asynccontextmanager
import socket
import json
import uvicorn
from urllib.parse import urlparse
from typing import AsyncIterator, Dict, Any
from threading import Lock
from dotenv import load_dotenv
from rag_mcp import (
    search_pyqgis_api as rag_search_pyqgis_api,
    search_gdal_api as rag_search_gdal_api,
    search_qgis_cookbook as rag_search_qgis_cookbook,
)
from mcp.server.fastmcp import FastMCP, Context


logging.basicConfig(level=logging.INFO, 
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("QgisMCPServer")

load_dotenv()


def _get_mcp_http_port(default_port: int = 8000) -> int:
    url = os.getenv("QGIS_MCP_SERVER_URL", "")
    if not url:
        return default_port
    try:
        parsed = urlparse(url)
        return parsed.port or default_port
    except Exception:
        return default_port

class QgisMCPServer:
    def __init__(self, host='localhost', port=9876, timeout: float = 10.0, max_response_bytes: int = 5 * 1024 * 1024):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self.socket = None
        self._lock = Lock()
    
    def connect(self):
        """Connect to the QGIS MCP server"""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.settimeout(self.timeout)
            self.socket.connect((self.host, self.port))
            return True
        except Exception as e:
            print(f"Error connecting to server: {str(e)}")
            self.socket = None
            return False
    
    def disconnect(self):
        """Disconnect from the server"""
        if self.socket:
            try:
                self.socket.close()
            finally:
                self.socket = None

    def _ensure_connected(self) -> bool:
        if self.socket is not None:
            return True
        return self.connect()
    
    def send_command(self, command_type, params=None):
        """Send a command to the server and get the response"""
        # Create command
        command = {
            "type": command_type,
            "params": params or {}
        }

        with self._lock:
            if not self._ensure_connected():
                print("Not connected to server")
                return None

            try:
                # Send the command
                self.socket.sendall(json.dumps(command).encode('utf-8'))

                # Receive the response
                response_data = b''
                while True:
                    chunk = self.socket.recv(4096)
                    if not chunk:
                        break
                    response_data += chunk

                    if len(response_data) > self.max_response_bytes:
                        raise ValueError("Response too large")

                    # Try to decode as JSON to see if it's complete
                    try:
                        json.loads(response_data.decode('utf-8'))
                        break  # Valid JSON, we have the full message
                    except json.JSONDecodeError:
                        continue  # Keep receiving

                # Parse and return the response
                return json.loads(response_data.decode('utf-8'))

            except (socket.timeout, ValueError) as e:
                print(f"Timeout or invalid response: {str(e)}")
                self.disconnect()
                return None
            except Exception as e:
                print(f"Error sending command: {str(e)}")
                self.disconnect()
                return None

_qgis_connection = None

def get_qgis_connection():
    """Get or create a persistent Qgis connection"""
    global _qgis_connection
    
    # If we have an existing connection, check if it's still valid
    if _qgis_connection is not None:
        # Test if the connection is still alive with a simple ping
        try:
            # Just try to send a small message to check if the socket is still connected
            _qgis_connection.socket.sendall(b'')
            return _qgis_connection
        except Exception as e:
            # Connection is dead, close it and create a new one
            logger.warning(f"Existing connection is no longer valid: {str(e)}")
            try:
                _qgis_connection.disconnect()
            except Exception:
                pass
            _qgis_connection = None
    
    # Create a new connection if needed
    if _qgis_connection is None:
        _qgis_connection = QgisMCPServer(host="localhost", port=9876)
        if not _qgis_connection.connect():
            logger.error("Failed to connect to Qgis")
            _qgis_connection = None
            raise Exception("Could not connect to Qgis. Make sure the Qgis plugin is running.")
        logger.info("Created new persistent connection to Qgis")
    
    return _qgis_connection

@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[Dict[str, Any]]:
    """Manage server startup and shutdown lifecycle"""
    # We don't need to create a connection here since we're using the global connection
    # for resources and tools
    
    try:
        # Just log that we're starting up
        logger.info("QgisMCPServer server starting up")
        
        # Try to connect to Qgis on startup to verify it's available
        try:
            # This will initialize the global connection if needed
            qgis = get_qgis_connection()
            logger.info("Successfully connected to Qgis on startup")
        except Exception as e:
            logger.warning(f"Could not connect to Qgis on startup: {str(e)}")
            logger.warning("Make sure the Qgis addon is running before using Qgis resources or tools")
        
        # Return an empty context - we're using the global connection
        yield {}
    finally:
        # Clean up the global connection on shutdown
        global _qgis_connection
        if _qgis_connection:
            logger.info("Disconnecting from Qgis on shutdown")
            _qgis_connection.disconnect()
            _qgis_connection = None
        logger.info("QgisMCPServer server shut down")

mcp = FastMCP(
    "Qgis_mcp",
    lifespan=server_lifespan
)


@mcp.tool()
def ping(ctx: Context) -> str:
    """Check MCP↔QGIS connectivity.

    Sends a lightweight ping request to the QGIS plugin and returns the raw JSON
    response (typically includes a success flag and optional message).
    """
    qgis = get_qgis_connection()
    result = qgis.send_command("ping")
    return json.dumps(result, indent=2)

@mcp.tool()
def get_qgis_info(ctx: Context) -> str:
    """Retrieve QGIS runtime information.

    Returns details such as QGIS version, build info, and environment metadata
    provided by the QGIS plugin.
    """
    qgis = get_qgis_connection()
    result = qgis.send_command("get_qgis_info")
    return json.dumps(result, indent=2)

@mcp.tool()
def load_project(ctx: Context, path: str) -> str:
    """Load a QGIS project from disk.

    Args:
        path: Full path to a .qgs/.qgz project file.

    Returns:
        JSON string indicating success/failure and any message from QGIS.
    """
    qgis = get_qgis_connection()
    result = qgis.send_command("load_project", {"path": path})
    return json.dumps(result, indent=2)

@mcp.tool()
def create_new_project(ctx: Context, path: str) -> str:
    """Create a new empty project and save it to disk.

    Args:
        path: Target file path for the new project (.qgs/.qgz).

    Returns:
        JSON string with operation result.
    """
    qgis = get_qgis_connection()
    result = qgis.send_command("create_new_project", {"path": path})
    return json.dumps(result, indent=2)

@mcp.tool()
def get_project_info(ctx: Context) -> str:
    """Get metadata for the currently loaded project.

    Returns project name, path, CRS, layer count, and other info exposed
    by the QGIS plugin.
    """
    qgis = get_qgis_connection()
    result = qgis.send_command("get_project_info")
    return json.dumps(result, indent=2)

@mcp.tool()
def add_layer(
    ctx: Context,
    path: str,
    provider: str | None = None,
    name: str | None = None,
    layer_type: str | None = None
) -> str:
    """Add a data layer to the current project.

    The layer type is inferred from the file extension unless `layer_type` is
    explicitly provided.

    Args:
        path: Data source path (file, database, or service URL).
        provider: Optional provider override (e.g., "gdal" for raster, "ogr" for vector).
        name: Optional layer name to display in the legend.
        layer_type: Explicit type: "raster" or "vector".

    Returns:
        JSON string with the added layer ID and status.
    """
    raster_exts = {
        ".tif", ".tiff", ".img", ".vrt", ".asc", ".bil", ".hdr", ".nc",
        ".grd", ".adf", ".jp2"
    }
    vector_exts = {
        ".shp", ".geojson", ".json", ".gpkg", ".kml", ".kmz", ".gpx",
        ".csv", ".dxf", ".tab", ".mif", ".mid"
    }

    ext = path.lower().rsplit(".", 1)
    ext = f".{ext[1]}" if len(ext) == 2 else ""

    if layer_type is None:
        if ext in raster_exts:
            layer_type = "raster"
        elif ext in vector_exts:
            layer_type = "vector"
        else:
            return json.dumps(
                {"error": "Unknown layer type. Please specify layer_type as 'raster' or 'vector'."},
                indent=2
            )

    qgis = get_qgis_connection()
    if layer_type == "raster":
        params = {"path": path, "provider": provider or "gdal"}
        if name:
            params["name"] = name
        result = qgis.send_command("add_raster_layer", params)
    elif layer_type == "vector":
        params = {"path": path, "provider": provider or "ogr"}
        if name:
            params["name"] = name
        result = qgis.send_command("add_vector_layer", params)
    else:
        return json.dumps(
            {"error": "Invalid layer_type. Use 'raster' or 'vector'."},
            indent=2
        )

    return json.dumps(result, indent=2)

# @mcp.tool()
# def get_layers(ctx: Context) -> str:
#     """Retrieve all layers in the current project."""
#     qgis = get_qgis_connection()
#     result = qgis.send_command("get_layers")
#     return json.dumps(result, indent=2)

@mcp.tool()
def remove_layer(ctx: Context, layer_id: str) -> str:
    """Remove a layer by its QGIS layer ID.

    Args:
        layer_id: The unique layer ID as returned by QGIS.

    Returns:
        JSON string indicating whether the layer was removed.
    """
    qgis = get_qgis_connection()
    result = qgis.send_command("remove_layer", {"layer_id": layer_id})
    return json.dumps(result, indent=2)

@mcp.tool()
def zoom_to_layer(ctx: Context, layer_id: str) -> str:
    """Zoom the map canvas to a layer's extent.

    Args:
        layer_id: The target layer ID.

    Returns:
        JSON string with the operation result.
    """
    qgis = get_qgis_connection()
    result = qgis.send_command("zoom_to_layer", {"layer_id": layer_id})
    return json.dumps(result, indent=2)

@mcp.tool()
def get_layer_features(ctx: Context, layer_id: str, limit: int = 10) -> str:
    """Fetch features from a vector layer.

    Args:
        layer_id: The vector layer ID to read from.

    Returns:
        JSON string containing feature attributes and geometries (if available).
    """
    qgis = get_qgis_connection()
    result = qgis.send_command("get_layer_features", {"layer_id": layer_id, "limit": limit})
    return json.dumps(result, indent=2)

@mcp.tool()
def execute_processing(ctx: Context, algorithm: str, parameters: dict) -> str:
    """Run a PyQGIS or GDAL Processing algorithm.

    Returns:
        JSON string with outputs and execution status.
    """
    qgis = get_qgis_connection()
    result = qgis.send_command("execute_processing", {"algorithm": algorithm, "parameters": parameters})
    return json.dumps(result, indent=2)


@mcp.tool()
def save_project(ctx: Context, path: str = None) -> str:
    """Save the current project.

    Args:
        path: Optional new path to save as. If omitted, saves to the current project path.

    Returns:
        JSON string indicating success/failure.
    """
    qgis = get_qgis_connection()
    params = {}
    if path:
        params["path"] = path
    result = qgis.send_command("save_project", params)
    return json.dumps(result, indent=2)


# @mcp.tool()
# def render_map(ctx: Context, path: str, width: int = 800, height: int = 600) -> str:
#     """Render the current map view to an image file with the specified dimensions."""
#     qgis = get_qgis_connection()
#     result = qgis.send_command("render_map", {"path": path, "width": width, "height": height})
#     return json.dumps(result, indent=2)


@mcp.tool()
def execute_code(ctx: Context, code: str) -> str:
    """Execute PyQGIS code on the QGIS side.

    Args:
        code: Python code string to run within the QGIS environment.

    Returns:
        JSON string with execution result or error details.
    """
    qgis = get_qgis_connection()
    result = qgis.send_command("execute_code", {"code": code})
    return json.dumps(result, indent=2)


@mcp.tool()
def capture_map_canvas(ctx: Context, path: str, width: int = 800, height: int = 600) -> str:
    """Capture the current QGIS map canvas to an image file.

    Args:
        path: Full output file path (PNG recommended), e.g.
            "C:/shared/screenshots/session_id/map_canvas_1234567890.png".
        width: Image width in pixels (default 800).
        height: Image height in pixels (default 600).

    Returns:
        JSON string with success status and the saved file path.
    """
    qgis = get_qgis_connection()
    result = qgis.send_command("capture_map_canvas", {
        "path": path,
        "width": width,
        "height": height
    })
    return json.dumps(result, indent=2)


@mcp.tool()
def search_pyqgis_api(ctx: Context, api_names: list[str], output_file: str | None = None) -> str:
    """Retrieve PyQGIS API documentation snippets for multiple symbols.

    Args:
        api_names: List of API symbols, e.g.,
            ["QgsRasterBlock.noDataValue", "QgsProject"].
        output_file: Optional JSON output file path for saving results.

    Returns:
        JSON string with matched documentation entries.
    """
    return rag_search_pyqgis_api(ctx, api_names, output_file=output_file)


@mcp.tool()
def search_gdal_api(ctx: Context, api_names: list[str]) -> str:
    """Search GDAL API documentation by symbol names.

    Args:
        api_names: List of GDAL symbols, e.g.,
            ["osgeo.gdal.Warp", "osgeo.ogr.Layer.CreateField"].

    Returns:
        JSON string with matched GDAL documentation entries.
    """
    return rag_search_gdal_api(ctx, api_names)


@mcp.tool()
def search_qgis_cookbook(ctx: Context, user_intent: str, similarity_threshold: float = 0.8, top_k: int = 3) -> str:
    """Find similar examples in the QGIS Cookbook.

    Args:
        user_intent: Natural-language description of the task you want to achieve.
        similarity_threshold: Similarity threshold (0.0–1.0), default 0.8.
        top_k: Maximum number of results to return (default 3).

    Returns:
        JSON string with the most similar cookbook examples.
    """
    return rag_search_qgis_cookbook(ctx, user_intent, similarity_threshold, top_k)





def main():
    """Run the MCP server"""
    port = _get_mcp_http_port()
    uvicorn.run(mcp.streamable_http_app, host="0.0.0.0", port=port)

if __name__ == "__main__":
    main()
