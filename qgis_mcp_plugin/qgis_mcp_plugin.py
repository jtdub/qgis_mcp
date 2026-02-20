import contextlib
import io
import json
import os
import socket
import sys
import traceback

from qgis.core import (
    NULL,
    Qgis,
    QgsApplication,  # Rendering; Print Layout; Labels
    QgsCategorizedSymbolRenderer,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsExpression,
    QgsFeature,
    QgsFeatureRequest,
    QgsFillSymbol,
    QgsGeometry,
    QgsGraduatedSymbolRenderer,
    QgsLayoutExporter,
    QgsLayoutItemLabel,
    QgsLayoutItemLegend,
    QgsLayoutItemMap,
    QgsLayoutItemMapOverview,
    QgsLayoutItemPicture,
    QgsLayoutItemScaleBar,
    QgsLayoutMeasurement,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsLineSymbol,
    QgsMapLayer,
    QgsMapRendererParallelJob,
    QgsMapSettings,
    QgsMarkerSymbol,
    QgsPalLayerSettings,
    QgsPointXY,
    QgsPrintLayout,
    QgsProject,
    QgsRasterLayer,
    QgsRectangle,
    QgsRendererCategory,
    QgsRendererRange,
    QgsSpatialIndex,
    QgsTextBufferSettings,
    QgsTextFormat,
    QgsUnitTypes,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
    QgsWkbTypes,
)
from qgis.gui import *
from qgis.PyQt.QtCore import QObject, QRectF, QSize, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor, QFont
from qgis.PyQt.QtWidgets import QAction, QDockWidget, QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget
from qgis.utils import active_plugins


class QgisMCPServer(QObject):
    """Server class to handle socket connections and execute QGIS commands"""

    def __init__(self, host="localhost", port=9876, iface=None):
        super().__init__()
        self.host = host
        self.port = port
        self.iface = iface
        self.running = False
        self.socket = None
        self.client = None
        self.buffer = b""
        self.timer = None

    def start(self):
        """Start the server"""
        self.running = True
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            self.socket.bind((self.host, self.port))
            self.socket.listen(1)
            self.socket.setblocking(False)

            # Create a timer to process server operations
            self.timer = QTimer()
            self.timer.timeout.connect(self.process_server)
            self.timer.start(100)  # 100ms interval

            QgsMessageLog.logMessage(f"QGIS MCP server started on {self.host}:{self.port}", "QGIS MCP")
            return True
        except Exception as e:
            QgsMessageLog.logMessage(f"Failed to start server: {str(e)}", "QGIS MCP", Qgis.Critical)
            self.stop()
            return False

    def stop(self):
        """Stop the server"""
        self.running = False

        if self.timer:
            self.timer.stop()
            self.timer = None

        if self.socket:
            self.socket.close()
        if self.client:
            self.client.close()

        self.socket = None
        self.client = None
        QgsMessageLog.logMessage("QGIS MCP server stopped", "QGIS MCP")

    def process_server(self):
        """Process server operations (called by timer)"""
        if not self.running:
            return

        try:
            # Accept new connections
            if not self.client and self.socket:
                try:
                    self.client, address = self.socket.accept()
                    self.client.setblocking(False)
                    QgsMessageLog.logMessage(f"Connected to client: {address}", "QGIS MCP")
                except BlockingIOError:
                    pass  # No connection waiting
                except Exception as e:
                    QgsMessageLog.logMessage(f"Error accepting connection: {str(e)}", "QGIS MCP", Qgis.Warning)

            # Process existing connection
            if self.client:
                try:
                    # Try to receive data
                    try:
                        data = self.client.recv(8192)
                        if data:
                            self.buffer += data
                            # Try to process complete messages
                            try:
                                # Attempt to parse the buffer as JSON
                                command = json.loads(self.buffer.decode("utf-8"))
                                # If successful, clear the buffer and process command
                                self.buffer = b""
                                response = self.execute_command(command)
                                response_json = json.dumps(response)
                                self.client.sendall(response_json.encode("utf-8"))
                            except json.JSONDecodeError:
                                # Incomplete data, keep in buffer
                                pass
                        else:
                            # Connection closed by client
                            QgsMessageLog.logMessage("Client disconnected", "QGIS MCP")
                            self.client.close()
                            self.client = None
                            self.buffer = b""
                    except BlockingIOError:
                        pass  # No data available
                    except Exception as e:
                        QgsMessageLog.logMessage(f"Error receiving data: {str(e)}", "QGIS MCP", Qgis.Warning)
                        self.client.close()
                        self.client = None
                        self.buffer = b""

                except Exception as e:
                    QgsMessageLog.logMessage(f"Error with client: {str(e)}", "QGIS MCP", Qgis.Warning)
                    if self.client:
                        self.client.close()
                        self.client = None
                    self.buffer = b""

        except Exception as e:
            QgsMessageLog.logMessage(f"Server error: {str(e)}", "QGIS MCP", Qgis.Critical)

    def execute_command(self, command):
        """Execute a command"""
        try:
            cmd_type = command.get("type")
            params = command.get("params", {})

            handlers = {
                "ping": self.ping,
                "get_qgis_info": self.get_qgis_info,
                "load_project": self.load_project,
                "get_project_info": self.get_project_info,
                "execute_code": self.execute_code,
                "add_vector_layer": self.add_vector_layer,
                "add_raster_layer": self.add_raster_layer,
                "get_layers": self.get_layers,
                "list_layers": self.list_layers,
                "remove_layer": self.remove_layer,
                "zoom_to_layer": self.zoom_to_layer,
                "get_layer_features": self.get_layer_features,
                "execute_processing": self.execute_processing,
                "save_project": self.save_project,
                "render_map": self.render_map,
                "create_new_project": self.create_new_project,
                # Phase 1: Introspection
                "get_layer_fields": self.get_layer_fields,
                "get_unique_values": self.get_unique_values,
                "sample_features": self.sample_features,
                "get_layer_extent": self.get_layer_extent,
                # Phase 2: Filtering & Spatial Operations
                "filter_layer": self.filter_layer,
                "trace_downstream": self.trace_downstream,
                "set_layer_visibility": self.set_layer_visibility,
                "set_canvas_extent": self.set_canvas_extent,
                # Phase 3: Styling
                "style_line_graduated": self.style_line_graduated,
                "style_simple": self.style_simple,
                "style_categorized": self.style_categorized,
                "add_labels": self.add_labels,
                # Phase 4: Print Layout & Cartography
                "create_print_layout": self.create_print_layout,
                "add_legend": self.add_legend,
                "add_inset_map": self.add_inset_map,
                "export_layout": self.export_layout,
            }

            handler = handlers.get(cmd_type)
            if handler:
                try:
                    QgsMessageLog.logMessage(f"Executing handler for {cmd_type}", "QGIS MCP")
                    result = handler(**params)
                    QgsMessageLog.logMessage("Handler execution complete", "QGIS MCP")
                    return {"status": "success", "result": result}
                except Exception as e:
                    QgsMessageLog.logMessage(f"Error in handler: {str(e)}", "QGIS MCP", Qgis.Critical)
                    traceback.print_exc()
                    return {"status": "error", "message": str(e)}
            else:
                return {"status": "error", "message": f"Unknown command type: {cmd_type}"}

        except Exception as e:
            QgsMessageLog.logMessage(f"Error executing command: {str(e)}", "QGIS MCP", Qgis.Critical)
            traceback.print_exc()
            return {"status": "error", "message": str(e)}

    # Helpers
    def _find_layer_by_name(self, layer_name):
        """Find a layer by name. Raises Exception if not found."""
        project = QgsProject.instance()
        for layer in project.mapLayers().values():
            if layer.name() == layer_name:
                return layer
        available = [lyr.name() for lyr in project.mapLayers().values()]
        raise Exception(f"Layer '{layer_name}' not found. Available layers: {available}")

    def _wgs84_crs(self):
        """Return the WGS84 CRS."""
        return QgsCoordinateReferenceSystem("EPSG:4326")

    def _transform_to_wgs84(self, geom, source_crs):
        """Transform a geometry to WGS84. Returns a new geometry."""
        wgs84 = self._wgs84_crs()
        if source_crs == wgs84:
            return QgsGeometry(geom)
        xform = QgsCoordinateTransform(source_crs, wgs84, QgsProject.instance())
        g = QgsGeometry(geom)
        g.transform(xform)
        return g

    def _transform_from_wgs84(self, geom, target_crs):
        """Transform a geometry from WGS84 to target CRS."""
        wgs84 = self._wgs84_crs()
        if target_crs == wgs84:
            return QgsGeometry(geom)
        xform = QgsCoordinateTransform(wgs84, target_crs, QgsProject.instance())
        g = QgsGeometry(geom)
        g.transform(xform)
        return g

    def _geometry_type_name(self, layer):
        """Get human-readable geometry type name for a vector layer."""
        geom_type = layer.geometryType()
        wkb_type = layer.wkbType()
        type_map = {
            QgsWkbTypes.PointGeometry: "Point",
            QgsWkbTypes.LineGeometry: "LineString",
            QgsWkbTypes.PolygonGeometry: "Polygon",
        }
        base = type_map.get(geom_type, "Unknown")
        if QgsWkbTypes.isMultiType(wkb_type):
            base = "Multi" + base
        return base

    # Command handlers
    def ping(self, **kwargs):
        """Simple ping command"""
        return {"pong": True}

    def get_qgis_info(self, **kwargs):
        """Get basic QGIS information"""
        return {
            "qgis_version": Qgis.version(),
            "profile_folder": QgsApplication.qgisSettingsDirPath(),
            "plugins_count": len(active_plugins),
        }

    def get_project_info(self, **kwargs):
        """Get information about the current QGIS project"""
        project = QgsProject.instance()

        # Get basic project information
        info = {
            "filename": project.fileName(),
            "title": project.title(),
            "layer_count": len(project.mapLayers()),
            "crs": project.crs().authid(),
            "layers": [],
        }

        # Add basic layer information (limit to 10 layers for performance)
        layers = list(project.mapLayers().values())
        for i, layer in enumerate(layers):
            if i >= 10:  # Limit to 10 layers
                break

            layer_info = {
                "id": layer.id(),
                "name": layer.name(),
                "type": self._get_layer_type(layer),
                "visible": layer.isValid() and project.layerTreeRoot().findLayer(layer.id()).isVisible(),
            }
            info["layers"].append(layer_info)

        return info

    def _get_layer_type(self, layer):
        """Helper to get layer type as string"""
        if layer.type() == QgsMapLayer.VectorLayer:
            return f"vector_{layer.geometryType()}"
        elif layer.type() == QgsMapLayer.RasterLayer:
            return "raster"
        else:
            return str(layer.type())

    def execute_code(self, code, **kwargs):
        """Execute arbitrary PyQGIS code"""

        # Capture stdout and stderr
        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()

        # Store original stdout and stderr
        original_stdout = sys.stdout
        original_stderr = sys.stderr

        try:
            # Redirect stdout and stderr
            sys.stdout = stdout_capture
            sys.stderr = stderr_capture

            # Create a local namespace for execution
            namespace = {
                "qgis": Qgis,
                "QgsProject": QgsProject,
                "iface": self.iface,
                "QgsApplication": QgsApplication,
                "QgsVectorLayer": QgsVectorLayer,
                "QgsRasterLayer": QgsRasterLayer,
                "QgsCoordinateReferenceSystem": QgsCoordinateReferenceSystem,
            }

            # Execute the code
            exec(code, namespace)

            # Restore stdout and stderr
            sys.stdout = original_stdout
            sys.stderr = original_stderr

            return {"executed": True, "stdout": stdout_capture.getvalue(), "stderr": stderr_capture.getvalue()}
        except Exception as e:
            # Generate full traceback
            error_traceback = traceback.format_exc()

            # Restore stdout and stderr in case of exception
            sys.stdout = original_stdout
            sys.stderr = original_stderr

            return {
                "executed": False,
                "error": str(e),
                "traceback": error_traceback,
                "stdout": stdout_capture.getvalue(),
                "stderr": stderr_capture.getvalue(),
            }

    def add_vector_layer(self, path, name=None, provider="ogr", **kwargs):
        """Add a vector layer to the project"""
        if not name:
            name = os.path.basename(path)

        # Create the layer
        layer = QgsVectorLayer(path, name, provider)

        if not layer.isValid():
            raise Exception(f"Layer is not valid: {path}")

        # Add to project
        QgsProject.instance().addMapLayer(layer)

        return {
            "id": layer.id(),
            "name": layer.name(),
            "type": self._get_layer_type(layer),
            "feature_count": layer.featureCount(),
        }

    def add_raster_layer(self, path, name=None, provider="gdal", **kwargs):
        """Add a raster layer to the project"""
        if not name:
            name = os.path.basename(path)

        # Create the layer
        layer = QgsRasterLayer(path, name, provider)

        if not layer.isValid():
            raise Exception(f"Layer is not valid: {path}")

        # Add to project
        QgsProject.instance().addMapLayer(layer)

        return {
            "id": layer.id(),
            "name": layer.name(),
            "type": "raster",
            "width": layer.width(),
            "height": layer.height(),
        }

    def get_layers(self, **kwargs):
        """Get all layers in the project (legacy, calls list_layers)"""
        return self.list_layers(**kwargs)

    def list_layers(self, **kwargs):
        """Get all layers with rich metadata"""
        project = QgsProject.instance()
        layers = []

        for layer_id, layer in project.mapLayers().items():
            tree_node = project.layerTreeRoot().findLayer(layer_id)
            layer_info = {
                "id": layer_id,
                "name": layer.name(),
                "type": "vector"
                if layer.type() == QgsMapLayer.VectorLayer
                else ("raster" if layer.type() == QgsMapLayer.RasterLayer else str(layer.type())),
                "visible": tree_node.isVisible() if tree_node else False,
                "crs": layer.crs().authid() if layer.crs().isValid() else "Unknown",
            }

            if layer.type() == QgsMapLayer.VectorLayer:
                layer_info.update(
                    {
                        "geometry_type": self._geometry_type_name(layer),
                        "feature_count": layer.featureCount(),
                        "fields": [{"name": f.name(), "type": f.typeName()} for f in layer.fields()],
                    }
                )
            elif layer.type() == QgsMapLayer.RasterLayer:
                layer_info.update(
                    {
                        "band_count": layer.bandCount(),
                        "width": layer.width(),
                        "height": layer.height(),
                        "pixel_size": {
                            "x": layer.rasterUnitsPerPixelX(),
                            "y": layer.rasterUnitsPerPixelY(),
                        },
                    }
                )

            layers.append(layer_info)

        return layers

    def get_layer_fields(self, layer_name, **kwargs):
        """Get detailed field information for a vector layer"""
        layer = self._find_layer_by_name(layer_name)
        if layer.type() != QgsMapLayer.VectorLayer:
            raise Exception(f"Layer '{layer_name}' is not a vector layer")

        fields = []
        for f in layer.fields():
            fields.append(
                {
                    "name": f.name(),
                    "type": f.typeName(),
                    "length": f.length(),
                    "precision": f.precision(),
                    "comment": f.comment() or "",
                }
            )
        return {"layer_name": layer_name, "fields": fields}

    def get_unique_values(self, layer_name, field_name, limit=50, **kwargs):
        """Get unique values for a specific field"""
        layer = self._find_layer_by_name(layer_name)
        if layer.type() != QgsMapLayer.VectorLayer:
            raise Exception(f"Layer '{layer_name}' is not a vector layer")

        field_idx = layer.fields().indexOf(field_name)
        if field_idx < 0:
            available = [f.name() for f in layer.fields()]
            raise Exception(f"Field '{field_name}' not found in layer '{layer_name}'. Available fields: {available}")

        values = set()
        for feature in layer.getFeatures():
            val = feature.attribute(field_idx)
            if val is not None and val != NULL:
                values.add(val)
            if len(values) >= limit:
                break

        sorted_values = sorted(values, key=lambda x: (isinstance(x, str), x))
        return {
            "layer_name": layer_name,
            "field_name": field_name,
            "count": len(sorted_values),
            "values": sorted_values,
        }

    def sample_features(self, layer_name, count=5, expression=None, **kwargs):
        """Sample features from a layer with optional expression filter"""
        layer = self._find_layer_by_name(layer_name)
        if layer.type() != QgsMapLayer.VectorLayer:
            raise Exception(f"Layer '{layer_name}' is not a vector layer")

        request = QgsFeatureRequest().setLimit(count)
        if expression:
            expr = QgsExpression(expression)
            if expr.hasParserError():
                raise Exception(f"Invalid expression: {expr.parserErrorString()}")
            request.setFilterExpression(expression)

        features = []
        for feature in layer.getFeatures(request):
            attrs = {}
            for field in layer.fields():
                val = feature.attribute(field.name())
                if val == NULL:
                    val = None
                attrs[field.name()] = val

            geom_wkt = None
            if feature.hasGeometry():
                g = self._transform_to_wgs84(feature.geometry(), layer.crs())
                wkt = g.asWkt(precision=6)
                geom_wkt = wkt[:200] + "..." if len(wkt) > 200 else wkt

            features.append(
                {
                    "id": feature.id(),
                    "attributes": attrs,
                    "geometry_wkt": geom_wkt,
                }
            )

        return {
            "layer_name": layer_name,
            "total_count": layer.featureCount(),
            "returned_count": len(features),
            "features": features,
        }

    def get_layer_extent(self, layer_name, **kwargs):
        """Get layer bounding box in WGS84"""
        layer = self._find_layer_by_name(layer_name)
        extent = layer.extent()

        # Reproject to WGS84
        wgs84 = self._wgs84_crs()
        if layer.crs() != wgs84:
            xform = QgsCoordinateTransform(layer.crs(), wgs84, QgsProject.instance())
            extent = xform.transformBoundingBox(extent)

        return {
            "layer_name": layer_name,
            "xmin": extent.xMinimum(),
            "ymin": extent.yMinimum(),
            "xmax": extent.xMaximum(),
            "ymax": extent.yMaximum(),
        }

    # Phase 2: Filtering & Spatial Operations

    def filter_layer(self, layer_name, expression, output_name, **kwargs):
        """Create a new memory layer from features matching an expression"""
        layer = self._find_layer_by_name(layer_name)
        if layer.type() != QgsMapLayer.VectorLayer:
            raise Exception(f"Layer '{layer_name}' is not a vector layer")

        # Validate expression
        expr = QgsExpression(expression)
        if expr.hasParserError():
            raise Exception(f"Invalid expression: {expr.parserErrorString()}")

        # Create memory layer with WGS84 CRS
        geom_type = self._geometry_type_name(layer)
        mem_layer = QgsVectorLayer(f"{geom_type}?crs=EPSG:4326", output_name, "memory")
        mem_provider = mem_layer.dataProvider()

        # Copy field definitions
        mem_provider.addAttributes(layer.fields().toList())
        mem_layer.updateFields()

        # Copy matching features, reprojecting geometry to WGS84
        request = QgsFeatureRequest().setFilterExpression(expression)
        features_out = []
        for feature in layer.getFeatures(request):
            new_feat = QgsFeature(mem_layer.fields())
            new_feat.setAttributes(feature.attributes())
            if feature.hasGeometry():
                new_feat.setGeometry(self._transform_to_wgs84(feature.geometry(), layer.crs()))
            features_out.append(new_feat)

        mem_provider.addFeatures(features_out)
        mem_layer.updateExtents()
        QgsProject.instance().addMapLayer(mem_layer)

        return {
            "output_name": output_name,
            "feature_count": len(features_out),
            "source_layer": layer_name,
            "expression": expression,
        }

    def trace_downstream(
        self,
        layer_name,
        start_lon,
        start_lat,
        id_field="HYRIV_ID",
        next_down_field="NEXT_DOWN",
        output_name="traced_river",
        **kwargs,
    ):
        """Trace a river network downstream from a point"""
        layer = self._find_layer_by_name(layer_name)
        if layer.type() != QgsMapLayer.VectorLayer:
            raise Exception(f"Layer '{layer_name}' is not a vector layer")

        # Validate fields exist
        field_names = [f.name() for f in layer.fields()]
        if id_field not in field_names:
            raise Exception(f"Field '{id_field}' not found. Available: {field_names}")
        if next_down_field not in field_names:
            raise Exception(f"Field '{next_down_field}' not found. Available: {field_names}")

        # Transform start point from WGS84 to layer CRS
        start_point_wgs = QgsPointXY(start_lon, start_lat)
        wgs84 = self._wgs84_crs()
        if layer.crs() != wgs84:
            xform = QgsCoordinateTransform(wgs84, layer.crs(), QgsProject.instance())
            start_point = xform.transform(start_point_wgs)
        else:
            start_point = start_point_wgs

        # Build spatial index and lookup dicts
        spatial_index = QgsSpatialIndex()
        features_by_fid = {}
        id_to_feature = {}
        id_to_next = {}

        for feature in layer.getFeatures():
            spatial_index.addFeature(feature)
            features_by_fid[feature.id()] = feature
            seg_id = feature.attribute(id_field)
            next_down = feature.attribute(next_down_field)
            id_to_feature[seg_id] = feature
            id_to_next[seg_id] = next_down

        # Find nearest segment to start point
        nearest_ids = spatial_index.nearestNeighbor(start_point, 1)
        if not nearest_ids:
            raise Exception("No features found near the start point")

        start_feature = features_by_fid[nearest_ids[0]]
        current_id = start_feature.attribute(id_field)

        # Walk downstream
        traced_ids = []
        visited = set()
        while current_id and current_id not in visited:
            if current_id not in id_to_feature:
                break
            visited.add(current_id)
            traced_ids.append(current_id)
            next_id = id_to_next.get(current_id)
            if next_id == 0 or next_id is None or next_id == NULL:
                break
            current_id = next_id

        # Create output memory layer in WGS84
        geom_type = self._geometry_type_name(layer)
        mem_layer = QgsVectorLayer(f"{geom_type}?crs=EPSG:4326", output_name, "memory")
        mem_provider = mem_layer.dataProvider()
        mem_provider.addAttributes(layer.fields().toList())
        mem_layer.updateFields()

        features_out = []
        for seg_id in traced_ids:
            src_feature = id_to_feature[seg_id]
            new_feat = QgsFeature(mem_layer.fields())
            new_feat.setAttributes(src_feature.attributes())
            if src_feature.hasGeometry():
                new_feat.setGeometry(self._transform_to_wgs84(src_feature.geometry(), layer.crs()))
            features_out.append(new_feat)

        mem_provider.addFeatures(features_out)
        mem_layer.updateExtents()
        QgsProject.instance().addMapLayer(mem_layer)

        return {
            "output_name": output_name,
            "segments_traced": len(traced_ids),
            "start_segment_id": traced_ids[0] if traced_ids else None,
            "end_segment_id": traced_ids[-1] if traced_ids else None,
        }

    def set_layer_visibility(self, layer_name, visible, **kwargs):
        """Set layer visibility"""
        layer = self._find_layer_by_name(layer_name)
        project = QgsProject.instance()
        tree_node = project.layerTreeRoot().findLayer(layer.id())
        if tree_node is None:
            raise Exception(f"Layer '{layer_name}' not found in layer tree")
        tree_node.setItemVisibilityChecked(visible)
        self.iface.mapCanvas().refresh()
        return {
            "layer_name": layer_name,
            "visible": visible,
        }

    def set_canvas_extent(self, xmin, ymin, xmax, ymax, **kwargs):
        """Set the map canvas extent from WGS84 coordinates"""
        wgs84 = self._wgs84_crs()
        project_crs = QgsProject.instance().crs()
        rect = QgsRectangle(xmin, ymin, xmax, ymax)

        if project_crs != wgs84:
            xform = QgsCoordinateTransform(wgs84, project_crs, QgsProject.instance())
            rect = xform.transformBoundingBox(rect)

        self.iface.mapCanvas().setExtent(rect)
        self.iface.mapCanvas().refresh()
        return {
            "extent": {"xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax},
        }

    # Phase 3: Styling Tools

    def style_line_graduated(
        self, layer_name, width_field, color="#1a5276", min_width=0.3, max_width=3.5, num_classes=0, **kwargs
    ):
        """Apply graduated line width styling based on a numeric field"""
        layer = self._find_layer_by_name(layer_name)
        if layer.type() != QgsMapLayer.VectorLayer:
            raise Exception(f"Layer '{layer_name}' is not a vector layer")

        field_idx = layer.fields().indexOf(width_field)
        if field_idx < 0:
            available = [f.name() for f in layer.fields()]
            raise Exception(f"Field '{width_field}' not found. Available: {available}")

        # Get min/max values
        min_val = None
        max_val = None
        for feature in layer.getFeatures():
            val = feature.attribute(field_idx)
            if val is not None and val != NULL:
                try:
                    val = float(val)
                    if min_val is None or val < min_val:
                        min_val = val
                    if max_val is None or val > max_val:
                        max_val = val
                except (ValueError, TypeError):
                    continue

        if min_val is None or max_val is None:
            raise Exception(f"No numeric values found in field '{width_field}'")

        # Determine number of classes
        if num_classes <= 0:
            value_range = max_val - min_val
            num_classes = min(max(int(value_range), 3), 10)

        # Create ranges with interpolated widths
        ranges = []
        step = (max_val - min_val) / num_classes
        width_step = (max_width - min_width) / num_classes

        for i in range(num_classes):
            lower = min_val + (step * i)
            upper = min_val + (step * (i + 1))
            width = min_width + (width_step * (i + 0.5))
            label = f"{lower:.1f} - {upper:.1f}"

            symbol = QgsLineSymbol.createSimple(
                {
                    "color": color,
                    "width": str(width),
                    "capstyle": "round",
                    "joinstyle": "round",
                }
            )
            rng = QgsRendererRange(lower, upper, symbol, label)
            ranges.append(rng)

        renderer = QgsGraduatedSymbolRenderer(width_field, ranges)
        layer.setRenderer(renderer)
        layer.triggerRepaint()

        return {
            "layer_name": layer_name,
            "field": width_field,
            "classes": num_classes,
            "value_range": {"min": min_val, "max": max_val},
            "width_range": {"min": min_width, "max": max_width},
        }

    def style_simple(self, layer_name, color="#333333", outline_color="#000000", width=0.5, opacity=1.0, **kwargs):
        """Apply simple single-symbol styling"""
        layer = self._find_layer_by_name(layer_name)
        if layer.type() != QgsMapLayer.VectorLayer:
            raise Exception(f"Layer '{layer_name}' is not a vector layer")

        geom_type = layer.geometryType()

        if geom_type == QgsWkbTypes.LineGeometry:
            symbol = QgsLineSymbol.createSimple(
                {
                    "color": color,
                    "width": str(width),
                    "capstyle": "round",
                    "joinstyle": "round",
                }
            )
        elif geom_type == QgsWkbTypes.PolygonGeometry:
            symbol = QgsFillSymbol.createSimple(
                {
                    "color": color,
                    "outline_color": outline_color,
                    "outline_width": str(width),
                }
            )
        elif geom_type == QgsWkbTypes.PointGeometry:
            symbol = QgsMarkerSymbol.createSimple(
                {
                    "color": color,
                    "outline_color": outline_color,
                    "outline_width": str(width),
                    "size": "3",
                }
            )
        else:
            raise Exception("Unsupported geometry type for styling")

        from qgis.core import QgsSingleSymbolRenderer

        renderer = QgsSingleSymbolRenderer(symbol)
        layer.setRenderer(renderer)
        layer.setOpacity(opacity)
        layer.triggerRepaint()

        return {
            "layer_name": layer_name,
            "color": color,
            "opacity": opacity,
        }

    def style_categorized(self, layer_name, field_name, color_ramp="Spectral", width=1.0, **kwargs):
        """Apply categorized styling using a color ramp"""
        layer = self._find_layer_by_name(layer_name)
        if layer.type() != QgsMapLayer.VectorLayer:
            raise Exception(f"Layer '{layer_name}' is not a vector layer")

        field_idx = layer.fields().indexOf(field_name)
        if field_idx < 0:
            available = [f.name() for f in layer.fields()]
            raise Exception(f"Field '{field_name}' not found. Available: {available}")

        # Get unique values
        unique_values = set()
        for feature in layer.getFeatures():
            val = feature.attribute(field_idx)
            if val is not None and val != NULL:
                unique_values.add(val)

        unique_values = sorted(unique_values, key=lambda x: (isinstance(x, str), x))

        # Get color ramp from style
        style = QgsApplication.instance().styleManager() if hasattr(QgsApplication, "styleManager") else None
        ramp = None
        if style:
            ramp = style.colorRamp(color_ramp)
        if ramp is None:
            from qgis.core import QgsGradientColorRamp

            ramp = QgsGradientColorRamp(QColor("#d73027"), QColor("#1a9850"))

        # Create categories
        categories = []
        num_values = len(unique_values)
        geom_type = layer.geometryType()

        for i, val in enumerate(unique_values):
            ratio = i / max(num_values - 1, 1)
            cat_color = ramp.color(ratio)

            if geom_type == QgsWkbTypes.LineGeometry:
                symbol = QgsLineSymbol.createSimple(
                    {
                        "color": cat_color.name(),
                        "width": str(width),
                    }
                )
            elif geom_type == QgsWkbTypes.PolygonGeometry:
                symbol = QgsFillSymbol.createSimple(
                    {
                        "color": cat_color.name(),
                        "outline_color": "#333333",
                        "outline_width": "0.26",
                    }
                )
            else:
                symbol = QgsMarkerSymbol.createSimple(
                    {
                        "color": cat_color.name(),
                        "size": "3",
                    }
                )

            category = QgsRendererCategory(val, symbol, str(val))
            categories.append(category)

        renderer = QgsCategorizedSymbolRenderer(field_name, categories)
        layer.setRenderer(renderer)
        layer.triggerRepaint()

        return {
            "layer_name": layer_name,
            "field": field_name,
            "categories": num_values,
            "color_ramp": color_ramp,
        }

    def add_labels(
        self,
        layer_name,
        field_name,
        font_size=10,
        color="#1a1a1a",
        follow_line=True,
        buffer_size=1.0,
        font_family="Noto Sans",
        **kwargs,
    ):
        """Add labels to a layer"""
        layer = self._find_layer_by_name(layer_name)
        if layer.type() != QgsMapLayer.VectorLayer:
            raise Exception(f"Layer '{layer_name}' is not a vector layer")

        field_idx = layer.fields().indexOf(field_name)
        if field_idx < 0:
            available = [f.name() for f in layer.fields()]
            raise Exception(f"Field '{field_name}' not found. Available: {available}")

        # Configure text format
        text_format = QgsTextFormat()
        text_format.setFont(QFont(font_family))
        text_format.setSize(font_size)
        text_format.setColor(QColor(color))

        # Buffer/halo
        buffer_settings = QgsTextBufferSettings()
        buffer_settings.setEnabled(True)
        buffer_settings.setSize(buffer_size)
        buffer_settings.setColor(QColor(255, 255, 255))
        text_format.setBuffer(buffer_settings)

        # Label settings
        label_settings = QgsPalLayerSettings()
        label_settings.fieldName = field_name
        label_settings.setFormat(text_format)

        # Line following
        if follow_line and layer.geometryType() == QgsWkbTypes.LineGeometry:
            label_settings.placement = QgsPalLayerSettings.Curved

        # Apply labeling
        labeling = QgsVectorLayerSimpleLabeling(label_settings)
        layer.setLabeling(labeling)
        layer.setLabelsEnabled(True)
        layer.triggerRepaint()

        return {
            "layer_name": layer_name,
            "field": field_name,
            "font_size": font_size,
            "follow_line": follow_line,
        }

    # Phase 4: Print Layout & Cartography

    def _get_page_dimensions(self, page_size, orientation):
        """Return (width_mm, height_mm) for a given page size and orientation."""
        sizes = {
            "A3": (420, 297),
            "A4": (297, 210),
            "letter": (279.4, 215.9),
            "tabloid": (431.8, 279.4),
        }
        w, h = sizes.get(page_size, sizes["A3"])
        if orientation == "portrait":
            w, h = h, w
        return w, h

    def create_print_layout(self, name, page_size="A3", orientation="landscape", title=None, **kwargs):
        """Create a print layout with map, scale bar, and north arrow"""
        project = QgsProject.instance()
        manager = project.layoutManager()

        # Check if layout already exists
        existing = manager.layoutByName(name)
        if existing:
            manager.removeLayout(existing)

        layout = QgsPrintLayout(project)
        layout.initializeDefaults()
        layout.setName(name)

        # Set page size
        page_w, page_h = self._get_page_dimensions(page_size, orientation)
        page = layout.pageCollection().page(0)
        page.setPageSize(QgsLayoutSize(page_w, page_h, QgsUnitTypes.LayoutMillimeters))

        # Margins
        margin = 15  # mm

        # Calculate map area
        map_y = margin
        map_h = page_h - (2 * margin)
        if title:
            map_y = margin + 15  # leave room for title
            map_h = page_h - (2 * margin) - 15

        # Add map item
        map_item = QgsLayoutItemMap(layout)
        map_item.setRect(QRectF(0, 0, page_w - 2 * margin, map_h))
        map_item.attemptMove(QgsLayoutPoint(margin, map_y, QgsUnitTypes.LayoutMillimeters))
        map_item.attemptResize(QgsLayoutSize(page_w - 2 * margin, map_h, QgsUnitTypes.LayoutMillimeters))
        map_item.setExtent(self.iface.mapCanvas().extent())
        map_item.setId("main")
        layout.addLayoutItem(map_item)

        # Add title if provided
        if title:
            title_item = QgsLayoutItemLabel(layout)
            title_item.setText(title)
            title_font = QFont("Noto Sans", 18)
            title_font.setBold(True)
            title_item.setFont(title_font)
            title_item.setHAlign(Qt.AlignHCenter)
            title_item.attemptMove(QgsLayoutPoint(margin, margin, QgsUnitTypes.LayoutMillimeters))
            title_item.attemptResize(QgsLayoutSize(page_w - 2 * margin, 12, QgsUnitTypes.LayoutMillimeters))
            layout.addLayoutItem(title_item)

        # Add scale bar
        scale_bar = QgsLayoutItemScaleBar(layout)
        scale_bar.setLinkedMap(map_item)
        scale_bar.setStyle("Single Box")
        scale_bar.setNumberOfSegments(4)
        scale_bar.setNumberOfSegmentsLeft(0)
        scale_bar.setUnitsPerSegment(50)
        scale_bar.applyDefaultSize()
        scale_bar.attemptMove(QgsLayoutPoint(margin, page_h - margin - 8, QgsUnitTypes.LayoutMillimeters))
        layout.addLayoutItem(scale_bar)

        # Add north arrow
        north_arrow = QgsLayoutItemPicture(layout)
        svg_paths = QgsApplication.svgPaths()
        arrow_path = None
        for svg_dir in svg_paths:
            candidate = os.path.join(svg_dir, "arrows", "NorthArrow_02.svg")
            if os.path.exists(candidate):
                arrow_path = candidate
                break
        if arrow_path:
            north_arrow.setPicturePath(arrow_path)
        north_arrow.attemptResize(QgsLayoutSize(15, 15, QgsUnitTypes.LayoutMillimeters))
        north_arrow.attemptMove(
            QgsLayoutPoint(page_w - margin - 15, page_h - margin - 15, QgsUnitTypes.LayoutMillimeters)
        )
        layout.addLayoutItem(north_arrow)

        manager.addLayout(layout)

        return {
            "name": name,
            "page_size": page_size,
            "orientation": orientation,
            "dimensions_mm": {"width": page_w, "height": page_h},
            "has_title": title is not None,
        }

    def add_legend(self, layout_name, title="Legend", position=None, width=45, layers=None, background=True, **kwargs):
        """Add a legend to a print layout"""
        if position is None:
            position = [15, 30]

        project = QgsProject.instance()
        manager = project.layoutManager()
        layout = manager.layoutByName(layout_name)
        if not layout:
            raise Exception(f"Layout '{layout_name}' not found")

        # Find the main map
        map_item = layout.itemById("main")
        if not map_item:
            raise Exception("No map item with ID 'main' found in layout")

        legend = QgsLayoutItemLegend(layout)
        legend.setLinkedMap(map_item)
        legend.setTitle(title)

        # Filter to specific layers if requested
        if layers:
            legend.setAutoUpdateModel(False)
            model = legend.model()
            root = model.rootGroup()
            # Remove layers not in the filter list
            for tree_layer in root.findLayers():
                if tree_layer.name() not in layers:
                    root.removeChildNode(tree_layer)

        # Background
        if background:
            legend.setBackgroundEnabled(True)
            legend.setBackgroundColor(QColor(255, 255, 255, 200))
            legend.setFrameEnabled(True)
            legend.setFrameStrokeColor(QColor(200, 200, 200))

        legend.attemptMove(QgsLayoutPoint(position[0], position[1], QgsUnitTypes.LayoutMillimeters))
        legend.attemptResize(QgsLayoutSize(width, 100, QgsUnitTypes.LayoutMillimeters))
        layout.addLayoutItem(legend)

        return {
            "layout_name": layout_name,
            "title": title,
            "position": position,
        }

    def add_inset_map(
        self, layout_name, extent, position=None, size=None, layers=None, show_extent_indicator=True, **kwargs
    ):
        """Add an inset/overview map to a print layout"""
        if position is None:
            position = [320, 15]
        if size is None:
            size = [80, 80]

        project = QgsProject.instance()
        manager = project.layoutManager()
        layout = manager.layoutByName(layout_name)
        if not layout:
            raise Exception(f"Layout '{layout_name}' not found")

        # Find main map
        map_item = layout.itemById("main")
        if not map_item:
            raise Exception("No map item with ID 'main' found in layout")

        # Create inset map
        inset = QgsLayoutItemMap(layout)
        inset.setId("inset")
        inset.attemptMove(QgsLayoutPoint(position[0], position[1], QgsUnitTypes.LayoutMillimeters))
        inset.attemptResize(QgsLayoutSize(size[0], size[1], QgsUnitTypes.LayoutMillimeters))

        # Set extent (reproject from WGS84 if needed)
        inset_rect = QgsRectangle(extent[0], extent[1], extent[2], extent[3])
        wgs84 = self._wgs84_crs()
        project_crs = project.crs()
        if project_crs != wgs84:
            xform = QgsCoordinateTransform(wgs84, project_crs, project)
            inset_rect = xform.transformBoundingBox(inset_rect)
        inset.setExtent(inset_rect)

        # Filter layers if specified
        if layers:
            layer_objects = []
            for lname in layers:
                with contextlib.suppress(Exception):
                    layer_objects.append(self._find_layer_by_name(lname))
            if layer_objects:
                inset.setLayers(layer_objects)
                inset.setKeepLayerSet(True)

        # Frame styling
        inset.setFrameEnabled(True)
        inset.setFrameStrokeColor(QColor(0, 0, 0))
        inset.setFrameStrokeWidth(QgsLayoutMeasurement(0.5, QgsUnitTypes.LayoutMillimeters))

        layout.addLayoutItem(inset)

        # Add extent indicator (overview) showing main map extent on inset
        if show_extent_indicator:
            overview = inset.overviews()
            overview.addOverview(QgsLayoutItemMapOverview("Main extent", inset))
            ov = overview.overview(0)
            ov.setLinkedMap(map_item)
            ov.setFrameSymbol(
                QgsFillSymbol.createSimple(
                    {
                        "color": "255,0,0,40",
                        "outline_color": "255,0,0",
                        "outline_width": "0.5",
                    }
                )
            )
            ov.setEnabled(True)

        return {
            "layout_name": layout_name,
            "position": position,
            "size": size,
            "extent": extent,
            "show_extent_indicator": show_extent_indicator,
        }

    def export_layout(self, layout_name, output_path, dpi=300, **kwargs):
        """Export a print layout to PDF or image"""
        project = QgsProject.instance()
        manager = project.layoutManager()
        layout = manager.layoutByName(layout_name)
        if not layout:
            raise Exception(f"Layout '{layout_name}' not found")

        exporter = QgsLayoutExporter(layout)
        ext = os.path.splitext(output_path)[1].lower()

        if ext == ".pdf":
            settings = QgsLayoutExporter.PdfExportSettings()
            settings.dpi = dpi
            result = exporter.exportToPdf(output_path, settings)
        else:
            settings = QgsLayoutExporter.ImageExportSettings()
            settings.dpi = dpi
            result = exporter.exportToImage(output_path, settings)

        if result != QgsLayoutExporter.Success:
            error_map = {
                QgsLayoutExporter.FileError: "File error",
                QgsLayoutExporter.MemoryError: "Memory error",
                QgsLayoutExporter.SvgLayerError: "SVG layer error",
                QgsLayoutExporter.PrintError: "Print error",
            }
            error_msg = error_map.get(result, f"Unknown error (code {result})")
            raise Exception(f"Export failed: {error_msg}")

        file_size = os.path.getsize(output_path) if os.path.exists(output_path) else 0

        return {
            "output_path": output_path,
            "format": ext.lstrip("."),
            "dpi": dpi,
            "file_size_bytes": file_size,
        }

    def remove_layer(self, layer_id, **kwargs):
        """Remove a layer from the project"""
        project = QgsProject.instance()

        if layer_id in project.mapLayers():
            project.removeMapLayer(layer_id)
            return {"removed": layer_id}
        else:
            raise Exception(f"Layer not found: {layer_id}")

    def zoom_to_layer(self, layer_id, **kwargs):
        """Zoom to a layer's extent"""
        project = QgsProject.instance()

        if layer_id in project.mapLayers():
            layer = project.mapLayer(layer_id)
            self.iface.setActiveLayer(layer)
            self.iface.zoomToActiveLayer()
            return {"zoomed_to": layer_id}
        else:
            raise Exception(f"Layer not found: {layer_id}")

    def get_layer_features(self, layer_id, limit=10, **kwargs):
        """Get features from a vector layer"""
        project = QgsProject.instance()

        if layer_id in project.mapLayers():
            layer = project.mapLayer(layer_id)

            if layer.type() != QgsMapLayer.VectorLayer:
                raise Exception(f"Layer is not a vector layer: {layer_id}")

            features = []
            for i, feature in enumerate(layer.getFeatures()):
                if i >= limit:
                    break

                # Extract attributes
                attrs = {}
                for field in layer.fields():
                    attrs[field.name()] = feature.attribute(field.name())

                # Extract geometry if available
                geom = None
                if feature.hasGeometry():
                    geom = {"type": feature.geometry().type(), "wkt": feature.geometry().asWkt(precision=4)}

                features.append({"id": feature.id(), "attributes": attrs, "geometry": geom})

            return {
                "layer_id": layer_id,
                "feature_count": layer.featureCount(),
                "features": features,
                "fields": [field.name() for field in layer.fields()],
            }
        else:
            raise Exception(f"Layer not found: {layer_id}")

    def execute_processing(self, algorithm, parameters, **kwargs):
        """Execute a processing algorithm"""
        try:
            import processing

            result = processing.run(algorithm, parameters)
            return {
                "algorithm": algorithm,
                "result": {k: str(v) for k, v in result.items()},  # Convert values to strings for JSON
            }
        except Exception as e:
            raise Exception(f"Processing error: {str(e)}")

    def save_project(self, path=None, **kwargs):
        """Save the current project"""
        project = QgsProject.instance()

        if not path and not project.fileName():
            raise Exception("No project path specified and no current project path")

        save_path = path if path else project.fileName()
        if project.write(save_path):
            return {"saved": save_path}
        else:
            raise Exception(f"Failed to save project to {save_path}")

    def load_project(self, path, **kwargs):
        """Load a project"""
        project = QgsProject.instance()

        if project.read(path):
            self.iface.mapCanvas().refresh()
            return {"loaded": path, "layer_count": len(project.mapLayers())}
        else:
            raise Exception(f"Failed to load project from {path}")

    def create_new_project(self, path, **kwargs):
        """
        Creates a new QGIS project and saves it at the specified path.
        If a project is already loaded, it clears it before creating the new one.

        :param path: Full path where the project will be saved
                     (e.g., 'C:/path/to/project.qgz')
        """
        project = QgsProject.instance()

        if project.fileName():
            project.clear()

        project.setFileName(path)
        self.iface.mapCanvas().refresh()

        # Save the project
        if project.write():
            return {
                "created": f"Project created and saved successfully at: {path}",
                "layer_count": len(project.mapLayers()),
            }
        else:
            raise Exception(f"Failed to save project to {path}")

    def render_map(self, path, width=800, height=600, **kwargs):
        """Render the current map view to an image"""
        try:
            # Create map settings
            ms = QgsMapSettings()

            # Set layers to render
            layers = list(QgsProject.instance().mapLayers().values())
            ms.setLayers(layers)

            # Set map canvas properties
            rect = self.iface.mapCanvas().extent()
            ms.setExtent(rect)
            ms.setOutputSize(QSize(width, height))
            ms.setBackgroundColor(QColor(255, 255, 255))
            ms.setOutputDpi(96)

            # Create the render
            render = QgsMapRendererParallelJob(ms)

            # Start rendering
            render.start()
            render.waitForFinished()

            # Get the image and save
            img = render.renderedImage()
            if img.save(path):
                return {"rendered": True, "path": path, "width": width, "height": height}
            else:
                raise Exception(f"Failed to save rendered image to {path}")

        except Exception as e:
            raise Exception(f"Render error: {str(e)}")


class QgisMCPDockWidget(QDockWidget):
    """Dock widget for the QGIS MCP plugin"""

    closed = pyqtSignal()

    def __init__(self, iface):
        super().__init__("QGIS MCP")
        self.iface = iface
        self.server = None
        self.setup_ui()

    def setup_ui(self):
        """Set up the dock widget UI"""
        # Create widget and layout
        widget = QWidget()
        layout = QVBoxLayout()
        widget.setLayout(layout)

        # Add port selection
        layout.addWidget(QLabel("Server Port:"))
        self.port_spin = QSpinBox()
        self.port_spin.setMinimum(1024)
        self.port_spin.setMaximum(65535)
        self.port_spin.setValue(9876)
        layout.addWidget(self.port_spin)

        # Add server control buttons
        self.start_button = QPushButton("Start Server")
        self.start_button.clicked.connect(self.start_server)
        layout.addWidget(self.start_button)

        self.stop_button = QPushButton("Stop Server")
        self.stop_button.clicked.connect(self.stop_server)
        self.stop_button.setEnabled(False)
        layout.addWidget(self.stop_button)

        # Add status label
        self.status_label = QLabel("Server: Stopped")
        layout.addWidget(self.status_label)

        # Add to dock widget
        self.setWidget(widget)

    def start_server(self):
        """Start the server"""
        if not self.server:
            port = self.port_spin.value()
            self.server = QgisMCPServer(port=port, iface=self.iface)

        if self.server.start():
            self.status_label.setText(f"Server: Running on port {self.server.port}")
            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(True)
            self.port_spin.setEnabled(False)

    def stop_server(self):
        """Stop the server"""
        if self.server:
            self.server.stop()
            self.server = None

        self.status_label.setText("Server: Stopped")
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.port_spin.setEnabled(True)

    def closeEvent(self, event):
        """Stop server on dock close"""
        self.stop_server()
        self.closed.emit()
        super().closeEvent(event)


class QgisMCPPlugin:
    """Main plugin class for QGIS MCP"""

    def __init__(self, iface):
        self.iface = iface
        self.dock_widget = None
        self.action = None

    def initGui(self):
        """Initialize GUI"""
        # Create action
        self.action = QAction("QGIS MCP", self.iface.mainWindow())
        self.action.setCheckable(True)
        self.action.triggered.connect(self.toggle_dock)

        # Add to plugins menu and toolbar
        self.iface.addPluginToMenu("QGIS MCP", self.action)
        self.iface.addToolBarIcon(self.action)

    def toggle_dock(self, checked):
        """Toggle the dock widget"""
        if checked:
            # Create dock widget if it doesn't exist
            if not self.dock_widget:
                self.dock_widget = QgisMCPDockWidget(self.iface)
                self.iface.addDockWidget(Qt.RightDockWidgetArea, self.dock_widget)
                # Connect close event
                self.dock_widget.closed.connect(self.dock_closed)
            else:
                # Show existing dock widget
                self.dock_widget.show()
        else:
            # Hide dock widget
            if self.dock_widget:
                self.dock_widget.hide()

    def dock_closed(self):
        """Handle dock widget closed"""
        self.action.setChecked(False)

    def unload(self):
        """Unload plugin"""
        # Stop server if running
        if self.dock_widget:
            self.dock_widget.stop_server()
            self.iface.removeDockWidget(self.dock_widget)
            self.dock_widget = None

        # Remove plugin menu item and toolbar icon
        self.iface.removePluginMenu("QGIS MCP", self.action)
        self.iface.removeToolBarIcon(self.action)


# Plugin entry point
def classFactory(iface):
    return QgisMCPPlugin(iface)
