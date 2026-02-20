#!/usr/bin/env python3
"""
QGIS MCP Client - Simple client to connect to the QGIS MCP server
"""

import json
import logging
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

from mcp.server.fastmcp import Context, FastMCP

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("QgisMCPServer")


class QgisMCPServer:
    """Socket client for communicating with the QGIS MCP plugin."""

    DEFAULT_TIMEOUT = 120  # seconds — generous for large operations like tracing
    RECV_BUFFER_SIZE = 65536

    def __init__(self, host="localhost", port=9876):
        self.host = host
        self.port = port
        self.socket: socket.socket | None = None

    def connect(self):
        """Connect to the QGIS MCP server"""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.settimeout(self.DEFAULT_TIMEOUT)
            self.socket.connect((self.host, self.port))
            logger.info(f"Connected to QGIS plugin at {self.host}:{self.port}")
            return True
        except Exception as e:
            logger.error(f"Error connecting to server: {str(e)}")
            self.socket = None
            return False

    def disconnect(self):
        """Disconnect from the server"""
        if self.socket:
            with suppress(Exception):
                self.socket.close()
            self.socket = None

    def _is_connected(self):
        """Check if the socket is still alive."""
        if not self.socket:
            return False
        try:
            # Use SO_ERROR to check for socket errors without sending data
            error = self.socket.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
            return error == 0
        except Exception:
            return False

    def _reconnect(self):
        """Attempt to reconnect to the QGIS plugin."""
        logger.info("Attempting to reconnect to QGIS plugin...")
        self.disconnect()
        return self.connect()

    def send_command(self, command_type, params=None, timeout=None):
        """Send a command to the server and get the response.

        Includes automatic reconnection on connection failure.
        """
        # Reconnect if socket is dead
        if not self._is_connected() and not self._reconnect():
            raise Exception(
                "Could not connect to QGIS. Make sure the QGIS MCP plugin is running and the server is started."
            )

        command = {"type": command_type, "params": params or {}}
        if self.socket is None:
            raise ConnectionError("Socket is unexpectedly None after connection check")

        if timeout is not None:
            self.socket.settimeout(timeout)

        max_response_bytes = 50 * 1024 * 1024  # 50 MB safety limit

        try:
            # Send the command
            payload = json.dumps(command).encode("utf-8")
            self.socket.sendall(payload)

            # Receive the response — accumulate until valid JSON
            response_data = b""
            while True:
                chunk = self.socket.recv(self.RECV_BUFFER_SIZE)
                if not chunk:
                    # Connection closed unexpectedly
                    self.disconnect()
                    raise Exception(f"Connection closed by QGIS while waiting for response to '{command_type}'")
                response_data += chunk

                if len(response_data) > max_response_bytes:
                    raise Exception(f"Response for '{command_type}' exceeded {max_response_bytes} bytes")

                try:
                    result = json.loads(response_data.decode("utf-8"))
                    return result
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue  # Keep receiving — response is not yet complete

        except TimeoutError:
            raise Exception(
                f"Timeout waiting for response to '{command_type}'. The operation may still be running in QGIS."
            )
        except (ConnectionError, BrokenPipeError, OSError) as e:
            # Connection died mid-command — try one reconnect
            logger.warning(f"Connection error during '{command_type}': {e}")
            self.disconnect()
            if not self._reconnect():
                raise Exception(f"Lost connection to QGIS during '{command_type}' and could not reconnect.")
            # Retry once after reconnect
            try:
                payload = json.dumps(command).encode("utf-8")
                self.socket.sendall(payload)
                response_data = b""
                while True:
                    chunk = self.socket.recv(self.RECV_BUFFER_SIZE)
                    if not chunk:
                        raise Exception("Connection closed during retry")
                    response_data += chunk

                    if len(response_data) > max_response_bytes:
                        raise Exception(f"Response for '{command_type}' exceeded {max_response_bytes} bytes")

                    try:
                        return json.loads(response_data.decode("utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
            except Exception as retry_err:
                raise Exception(f"Failed to execute '{command_type}' after reconnect: {retry_err}")
        finally:
            # Reset timeout to default
            if timeout is not None and self.socket:
                self.socket.settimeout(self.DEFAULT_TIMEOUT)


_qgis_connection = None


def get_qgis_connection():
    """Get or create a persistent QGIS connection."""
    global _qgis_connection

    if _qgis_connection is not None and _qgis_connection._is_connected():
        return _qgis_connection

    # Connection is dead or doesn't exist — create new one
    if _qgis_connection is not None:
        logger.warning("Existing connection is no longer valid, reconnecting...")
        _qgis_connection.disconnect()
        _qgis_connection = None

    _qgis_connection = QgisMCPServer(host="localhost", port=9876)
    if not _qgis_connection.connect():
        _qgis_connection = None
        raise Exception(
            "Could not connect to QGIS. Make sure the QGIS MCP plugin is running "
            "and the server is started on port 9876."
        )
    return _qgis_connection


@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """Manage server startup and shutdown lifecycle"""
    # We don't need to create a connection here since we're using the global connection
    # for resources and tools

    try:
        # Just log that we're starting up
        logger.info("QgisMCPServer server starting up")

        # Try to connect to Qgis on startup to verify it's available
        try:
            # This will initialize the global connection if needed
            get_qgis_connection()
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


mcp = FastMCP("Qgis_mcp", description="Qgis integration through the Model Context Protocol", lifespan=server_lifespan)


@mcp.tool()
def ping(ctx: Context) -> str:
    """Simple ping command to check server connectivity"""
    qgis = get_qgis_connection()
    result = qgis.send_command("ping")
    return json.dumps(result, indent=2)


@mcp.tool()
def get_qgis_info(ctx: Context) -> str:
    """Get QGIS information"""
    qgis = get_qgis_connection()
    result = qgis.send_command("get_qgis_info")
    return json.dumps(result, indent=2)


@mcp.tool()
def load_project(ctx: Context, path: str) -> str:
    """Load a QGIS project from the specified path."""
    qgis = get_qgis_connection()
    result = qgis.send_command("load_project", {"path": path})
    return json.dumps(result, indent=2)


@mcp.tool()
def create_new_project(ctx: Context, path: str) -> str:
    """Create a new project and save it."""
    qgis = get_qgis_connection()
    result = qgis.send_command("create_new_project", {"path": path})
    return json.dumps(result, indent=2)


@mcp.tool()
def get_project_info(ctx: Context) -> str:
    """Get current project information"""
    qgis = get_qgis_connection()
    result = qgis.send_command("get_project_info")
    return json.dumps(result, indent=2)


@mcp.tool()
def add_vector_layer(ctx: Context, path: str, provider: str = "ogr", name: str | None = None) -> str:
    """Add a vector layer to the project."""
    qgis = get_qgis_connection()
    params = {"path": path, "provider": provider}
    if name:
        params["name"] = name
    result = qgis.send_command("add_vector_layer", params)
    return json.dumps(result, indent=2)


@mcp.tool()
def add_raster_layer(ctx: Context, path: str, provider: str = "gdal", name: str | None = None) -> str:
    """Add a raster layer to the project."""
    qgis = get_qgis_connection()
    params = {"path": path, "provider": provider}
    if name:
        params["name"] = name
    result = qgis.send_command("add_raster_layer", params)
    return json.dumps(result, indent=2)


@mcp.tool()
def get_layers(ctx: Context) -> str:
    """Retrieve all layers in the current project (legacy, prefer list_layers)."""
    qgis = get_qgis_connection()
    result = qgis.send_command("get_layers")
    return json.dumps(result, indent=2)


@mcp.tool()
def list_layers(ctx: Context) -> str:
    """List all layers with rich metadata including CRS, fields, geometry type, and feature count.

    Returns an array of layer objects. Vector layers include field definitions.
    Raster layers include band count, dimensions, and pixel size.
    """
    qgis = get_qgis_connection()
    result = qgis.send_command("list_layers")
    return json.dumps(result, indent=2)


@mcp.tool()
def remove_layer(ctx: Context, layer_id: str) -> str:
    """Remove a layer from the project by its ID."""
    qgis = get_qgis_connection()
    result = qgis.send_command("remove_layer", {"layer_id": layer_id})
    return json.dumps(result, indent=2)


@mcp.tool()
def zoom_to_layer(ctx: Context, layer_id: str) -> str:
    """Zoom to the extent of a specified layer."""
    qgis = get_qgis_connection()
    result = qgis.send_command("zoom_to_layer", {"layer_id": layer_id})
    return json.dumps(result, indent=2)


@mcp.tool()
def get_layer_features(ctx: Context, layer_id: str, limit: int = 10) -> str:
    """Retrieve features from a vector layer with an optional limit."""
    qgis = get_qgis_connection()
    result = qgis.send_command("get_layer_features", {"layer_id": layer_id, "limit": limit})
    return json.dumps(result, indent=2)


@mcp.tool()
def execute_processing(ctx: Context, algorithm: str, parameters: dict) -> str:
    """Execute a processing algorithm with the given parameters."""
    qgis = get_qgis_connection()
    result = qgis.send_command("execute_processing", {"algorithm": algorithm, "parameters": parameters})
    return json.dumps(result, indent=2)


@mcp.tool()
def save_project(ctx: Context, path: str | None = None) -> str:
    """Save the current project to the given path, or to the current project path if not specified."""
    qgis = get_qgis_connection()
    params = {}
    if path:
        params["path"] = path
    result = qgis.send_command("save_project", params)
    return json.dumps(result, indent=2)


@mcp.tool()
def render_map(ctx: Context, path: str, width: int = 800, height: int = 600) -> str:
    """Render the current map view to an image file with the specified dimensions."""
    qgis = get_qgis_connection()
    result = qgis.send_command("render_map", {"path": path, "width": width, "height": height})
    return json.dumps(result, indent=2)


@mcp.tool()
def execute_code(ctx: Context, code: str) -> str:
    """Execute arbitrary PyQGIS code provided as a string."""
    qgis = get_qgis_connection()
    result = qgis.send_command("execute_code", {"code": code})
    return json.dumps(result, indent=2)


# Phase 1: Introspection Tools


@mcp.tool()
def get_layer_fields(ctx: Context, layer_name: str) -> str:
    """Get detailed field information for a vector layer.

    Returns field name, type, length, precision, and comment for each field.
    """
    qgis = get_qgis_connection()
    result = qgis.send_command("get_layer_fields", {"layer_name": layer_name})
    return json.dumps(result, indent=2)


@mcp.tool()
def get_unique_values(ctx: Context, layer_name: str, field_name: str, limit: int = 50) -> str:
    """Get unique values for a specific field in a vector layer.

    Useful for understanding categorical data and building filter expressions.
    Values are returned sorted with a configurable limit.
    """
    qgis = get_qgis_connection()
    result = qgis.send_command(
        "get_unique_values",
        {
            "layer_name": layer_name,
            "field_name": field_name,
            "limit": limit,
        },
    )
    return json.dumps(result, indent=2)


@mcp.tool()
def sample_features(ctx: Context, layer_name: str, count: int = 5, expression: str | None = None) -> str:
    """Sample features from a vector layer with optional expression filter.

    Returns feature attributes and truncated WKT geometry in WGS84.
    Use expression parameter to filter (e.g., \"name\" = 'Vilcanota').
    """
    qgis = get_qgis_connection()
    params = {"layer_name": layer_name, "count": count}
    if expression:
        params["expression"] = expression
    result = qgis.send_command("sample_features", params)
    return json.dumps(result, indent=2)


@mcp.tool()
def get_layer_extent(ctx: Context, layer_name: str) -> str:
    """Get a layer's bounding box in WGS84 coordinates.

    Returns xmin, ymin, xmax, ymax of the layer extent.
    """
    qgis = get_qgis_connection()
    result = qgis.send_command("get_layer_extent", {"layer_name": layer_name})
    return json.dumps(result, indent=2)


# Phase 2: Filtering & Spatial Operations


@mcp.tool()
def filter_layer(ctx: Context, layer_name: str, expression: str, output_name: str) -> str:
    """Create a new memory layer from features matching a QGIS expression.

    Examples: "name" IN ('Vilcanota', 'Urubamba'), "population" > 10000
    The output layer is created in WGS84 and added to the project.
    """
    qgis = get_qgis_connection()
    result = qgis.send_command(
        "filter_layer",
        {
            "layer_name": layer_name,
            "expression": expression,
            "output_name": output_name,
        },
    )
    return json.dumps(result, indent=2)


@mcp.tool()
def trace_downstream(
    ctx: Context,
    layer_name: str,
    start_lon: float,
    start_lat: float,
    id_field: str = "HYRIV_ID",
    next_down_field: str = "NEXT_DOWN",
    output_name: str = "traced_river",
) -> str:
    """Trace a river network downstream from a WGS84 coordinate.

    Follows the network topology using id_field and next_down_field pointers.
    Compatible with HydroSHEDS/HydroRIVERS data. Creates an output memory layer.
    """
    qgis = get_qgis_connection()
    result = qgis.send_command(
        "trace_downstream",
        {
            "layer_name": layer_name,
            "start_lon": start_lon,
            "start_lat": start_lat,
            "id_field": id_field,
            "next_down_field": next_down_field,
            "output_name": output_name,
        },
    )
    return json.dumps(result, indent=2)


@mcp.tool()
def set_layer_visibility(ctx: Context, layer_name: str, visible: bool) -> str:
    """Toggle layer visibility in the layer tree."""
    qgis = get_qgis_connection()
    result = qgis.send_command(
        "set_layer_visibility",
        {
            "layer_name": layer_name,
            "visible": visible,
        },
    )
    return json.dumps(result, indent=2)


@mcp.tool()
def set_canvas_extent(ctx: Context, xmin: float, ymin: float, xmax: float, ymax: float) -> str:
    """Set the map canvas extent using WGS84 coordinates.

    Automatically reprojects to the project CRS.
    """
    qgis = get_qgis_connection()
    result = qgis.send_command(
        "set_canvas_extent",
        {
            "xmin": xmin,
            "ymin": ymin,
            "xmax": xmax,
            "ymax": ymax,
        },
    )
    return json.dumps(result, indent=2)


# Phase 3: Styling Tools


@mcp.tool()
def style_line_graduated(
    ctx: Context,
    layer_name: str,
    width_field: str,
    color: str = "#1a5276",
    min_width: float = 0.3,
    max_width: float = 3.5,
    num_classes: int = 0,
) -> str:
    """Apply graduated line width styling based on a numeric field.

    Creates classes with interpolated widths. Set num_classes=0 for auto-detection.
    """
    qgis = get_qgis_connection()
    result = qgis.send_command(
        "style_line_graduated",
        {
            "layer_name": layer_name,
            "width_field": width_field,
            "color": color,
            "min_width": min_width,
            "max_width": max_width,
            "num_classes": num_classes,
        },
    )
    return json.dumps(result, indent=2)


@mcp.tool()
def style_simple(
    ctx: Context,
    layer_name: str,
    color: str = "#333333",
    outline_color: str = "#000000",
    width: float = 0.5,
    opacity: float = 1.0,
) -> str:
    """Apply simple single-symbol styling to a vector layer.

    Automatically detects geometry type (point/line/polygon) and creates
    the appropriate symbol.
    """
    qgis = get_qgis_connection()
    result = qgis.send_command(
        "style_simple",
        {
            "layer_name": layer_name,
            "color": color,
            "outline_color": outline_color,
            "width": width,
            "opacity": opacity,
        },
    )
    return json.dumps(result, indent=2)


@mcp.tool()
def style_categorized(
    ctx: Context, layer_name: str, field_name: str, color_ramp: str = "Spectral", width: float = 1.0
) -> str:
    """Apply categorized styling using unique field values and a color ramp.

    Each unique value gets a distinct color from the ramp.
    """
    qgis = get_qgis_connection()
    result = qgis.send_command(
        "style_categorized",
        {
            "layer_name": layer_name,
            "field_name": field_name,
            "color_ramp": color_ramp,
            "width": width,
        },
    )
    return json.dumps(result, indent=2)


@mcp.tool()
def add_labels(
    ctx: Context,
    layer_name: str,
    field_name: str,
    font_size: float = 10,
    color: str = "#1a1a1a",
    follow_line: bool = True,
    buffer_size: float = 1.0,
    font_family: str = "Noto Sans",
) -> str:
    """Add labels to a vector layer.

    Supports curved labels that follow line geometry. Includes a white
    buffer/halo for readability.
    """
    qgis = get_qgis_connection()
    result = qgis.send_command(
        "add_labels",
        {
            "layer_name": layer_name,
            "field_name": field_name,
            "font_size": font_size,
            "color": color,
            "follow_line": follow_line,
            "buffer_size": buffer_size,
            "font_family": font_family,
        },
    )
    return json.dumps(result, indent=2)


# Phase 4: Print Layout & Cartography


@mcp.tool()
def create_print_layout(
    ctx: Context, name: str, page_size: str = "A3", orientation: str = "landscape", title: str | None = None
) -> str:
    """Create a print layout with a map item, scale bar, and north arrow.

    Supports page sizes: A3, A4, letter, tabloid.
    The main map item is set to the current canvas extent.
    """
    qgis = get_qgis_connection()
    params = {
        "name": name,
        "page_size": page_size,
        "orientation": orientation,
    }
    if title:
        params["title"] = title
    result = qgis.send_command("create_print_layout", params)
    return json.dumps(result, indent=2)


@mcp.tool()
def add_legend(
    ctx: Context,
    layout_name: str,
    title: str = "Legend",
    position: list[Any] | None = None,
    width: float = 45,
    layers: list[Any] | None = None,
    background: bool = True,
) -> str:
    """Add a legend to a print layout.

    Optionally filter to specific layer names. Position is [x, y] in mm.
    """
    qgis = get_qgis_connection()
    params = {
        "layout_name": layout_name,
        "title": title,
        "width": width,
        "background": background,
    }
    if position:
        params["position"] = position
    if layers:
        params["layers"] = layers
    result = qgis.send_command("add_legend", params)
    return json.dumps(result, indent=2)


@mcp.tool()
def add_inset_map(
    ctx: Context,
    layout_name: str,
    extent: list[Any],
    position: list[Any] | None = None,
    size: list[Any] | None = None,
    layers: list[Any] | None = None,
    show_extent_indicator: bool = True,
) -> str:
    """Add an inset/overview map to a print layout.

    extent is [xmin, ymin, xmax, ymax] in WGS84.
    Shows a red rectangle indicating the main map's extent.
    """
    qgis = get_qgis_connection()
    params = {
        "layout_name": layout_name,
        "extent": extent,
        "show_extent_indicator": show_extent_indicator,
    }
    if position:
        params["position"] = position
    if size:
        params["size"] = size
    if layers:
        params["layers"] = layers
    result = qgis.send_command("add_inset_map", params)
    return json.dumps(result, indent=2)


@mcp.tool()
def export_layout(ctx: Context, layout_name: str, output_path: str, dpi: int = 300) -> str:
    """Export a print layout to PDF or image.

    File extension determines format: .pdf for PDF, .png/.jpg for images.
    """
    qgis = get_qgis_connection()
    result = qgis.send_command(
        "export_layout",
        {
            "layout_name": layout_name,
            "output_path": output_path,
            "dpi": dpi,
        },
    )
    return json.dumps(result, indent=2)


def main():
    """Run the MCP server"""
    mcp.run()


if __name__ == "__main__":
    main()
