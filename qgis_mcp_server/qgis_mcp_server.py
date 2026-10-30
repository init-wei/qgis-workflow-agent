#!/usr/bin/env python3
"""
QGIS MCP Client - Simple client to connect to the QGIS MCP server
"""

import logging
import os
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
import socket
import json
import uvicorn
from urllib.parse import urlparse
from typing import AsyncIterator, Dict, Any, Literal
from threading import Lock
from dotenv import load_dotenv
try:
    from .qgis_mcp_plugin.processing_policy import validate_processing_request
    from .qgis_mcp_plugin.artifact_paths import call_output_paths, new_output_path
except ImportError:  # Support the documented `python qgis_mcp_server/qgis_mcp_server.py` entry.
    from qgis_mcp_plugin.processing_policy import validate_processing_request
    from qgis_mcp_plugin.artifact_paths import call_output_paths, new_output_path
from mcp.server.fastmcp import FastMCP, Context


logging.basicConfig(level=logging.INFO, 
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("QgisMCPServer")

load_dotenv()

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _get_mcp_http_port(default_port: int = 8000) -> int:
    url = os.getenv("QGIS_MCP_SERVER_URL", "")
    if not url:
        return default_port
    try:
        parsed = urlparse(url)
        return parsed.port or default_port
    except Exception:
        return default_port


def _resolve_local_path(path: str | None) -> str | None:
    """Resolve project-relative local paths before forwarding to QGIS."""
    if not path or "://" in path:
        return path

    local_path = Path(path)
    if local_path.is_absolute():
        return str(local_path)
    return str((_PROJECT_ROOT / local_path).resolve())


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
        """Exchange one raw JSON request and response with the QGIS plugin."""
        command = {
            "type": command_type,
            "params": params or {}
        }

        with self._lock:
            if not self._ensure_connected():
                print("Not connected to server")
                return None

            try:
                request = json.dumps(command).encode("utf-8")
                self.socket.sendall(request)
                return self._receive_raw_json_response()
            except (socket.timeout, ValueError) as e:
                logger.debug("QGIS raw JSON exchange failed: %s", e)
                self.disconnect()
                return None
            except Exception as e:
                print(f"Error sending command: {str(e)}")
                self.disconnect()
                return None

    def _receive_raw_json_response(self):
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
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue  # Keep receiving

        return json.loads(response_data.decode('utf-8'))

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
def get_qgis_info(ctx: Context, processing_requests: list[dict] | None = None) -> str:
    """Retrieve QGIS runtime information.

    Returns details such as QGIS version, build info, and environment metadata
    provided by the QGIS plugin.
    """
    qgis = get_qgis_connection()
    result = qgis.send_command("get_qgis_info", {"processing_requests": processing_requests} if processing_requests is not None else None)
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
    layer_type: Literal["raster", "vector"],
    provider: str | None = None,
    name: str | None = None,
) -> str:
    """Add a data layer to the current project.

    The caller must select the layer type explicitly. Provider and display name
    are optional; QGIS uses its standard provider for the selected layer type.

    Args:
        path: Data source path (file, database, or service URL).
        provider: Optional provider override (e.g., "gdal" for raster, "ogr" for vector).
        name: Optional layer name to display in the legend.
        layer_type: Required explicit type: "raster" or "vector".

    Returns:
        JSON string with the added layer ID and status.
    """
    if not path:
        raise ValueError("path is required")
    if layer_type not in {"raster", "vector"}:
        raise ValueError("layer_type must be 'raster' or 'vector'")
    path = _resolve_local_path(path)

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

@mcp.tool()
def get_layers(ctx: Context) -> str:
    """List layers in the currently loaded QGIS project."""
    qgis = get_qgis_connection()
    result = qgis.send_command("get_layers")
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
    """Run one explicitly supported QGIS Processing algorithm.

    Returns:
        JSON string with outputs and execution status.
    """
    validate_processing_request(algorithm, parameters)
    for destination in call_output_paths("execute_processing", {"parameters": parameters}):
        new_output_path(destination)
    qgis = get_qgis_connection()
    result = qgis.send_command("execute_processing", {"algorithm": algorithm, "parameters": parameters})
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
    path = str(new_output_path(path))
    result = qgis.send_command("capture_map_canvas", {
        "path": path,
        "width": width,
        "height": height
    })
    return json.dumps(result, indent=2)


@lru_cache(maxsize=8)
def tool_argument_model(tool_name: str):
    """Return the same SDK argument contract used by the paired MCP server."""
    from mcp.server.fastmcp.utilities.func_metadata import func_metadata

    functions = {fn.__name__: fn for fn in (
        ping, get_qgis_info, get_project_info, get_layers, add_layer,
        get_layer_features, execute_processing, capture_map_canvas,
    )}
    function = functions.get(tool_name)
    if function is None:
        raise ValueError(f"Unsupported MCP tool: {tool_name}")
    return func_metadata(function, skip_names=("ctx",)).arg_model


def validate_tool_arguments(tool_name: str, arguments: dict) -> None:
    """Validate every argument, including extra keys, before any plan call runs."""
    tool_argument_model(tool_name).model_validate(arguments, strict=True, extra="forbid")


def main():
    """Run the MCP server"""
    port = _get_mcp_http_port()
    uvicorn.run(mcp.streamable_http_app, host="0.0.0.0", port=port)

if __name__ == "__main__":
    main()
