"""Tests for plugin helper functions that don't require a live QGIS instance.

Uses sys.modules mocking to import the plugin module without QGIS.
The key trick is making QObject a real class so that inheritance
and super().__init__() work normally, while everything else returns MagicMock.
"""

import sys
import types
from unittest.mock import MagicMock

import pytest


class _MockModule(types.ModuleType):
    """A module whose missing attributes resolve to MagicMock."""

    def __init__(self, name, attrs=None):
        super().__init__(name)
        for k, v in (attrs or {}).items():
            setattr(self, k, v)

    def __getattr__(self, name):
        # Avoid recursing on dunder attributes
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        mock = MagicMock(name=f"{self.__name__}.{name}")
        setattr(self, name, mock)
        return mock


class _FakeQObject:
    """Real base class standing in for QObject."""

    pass


def _install_qgis_mocks():
    """Populate sys.modules with fake qgis packages."""
    mods = {
        "qgis": _MockModule("qgis"),
        "qgis.core": _MockModule("qgis.core"),
        "qgis.gui": _MockModule(
            "qgis.gui",
            {
                "__all__": ["QgsMessageLog"],
            },
        ),
        "qgis.utils": _MockModule("qgis.utils"),
        "qgis.PyQt": _MockModule("qgis.PyQt"),
        "qgis.PyQt.QtCore": _MockModule(
            "qgis.PyQt.QtCore",
            {
                "QObject": _FakeQObject,
                "pyqtSignal": MagicMock(return_value=MagicMock()),
            },
        ),
        "qgis.PyQt.QtWidgets": _MockModule("qgis.PyQt.QtWidgets"),
        "qgis.PyQt.QtGui": _MockModule("qgis.PyQt.QtGui"),
    }
    # Wire sub-module attributes
    mods["qgis"].core = mods["qgis.core"]
    mods["qgis"].gui = mods["qgis.gui"]
    mods["qgis"].utils = mods["qgis.utils"]
    mods["qgis"].PyQt = mods["qgis.PyQt"]
    mods["qgis.PyQt"].QtCore = mods["qgis.PyQt.QtCore"]
    mods["qgis.PyQt"].QtWidgets = mods["qgis.PyQt.QtWidgets"]
    mods["qgis.PyQt"].QtGui = mods["qgis.PyQt.QtGui"]

    sys.modules.update(mods)


_install_qgis_mocks()

# Now import the plugin — QObject is a real class, everything else is MagicMock
from qgis_mcp_plugin.qgis_mcp_plugin import QgisMCPServer as PluginServer


@pytest.fixture
def plugin_server():
    """Create a PluginServer instance with mocked iface."""
    return PluginServer(iface=MagicMock())


class TestGetPageDimensions:
    def test_a3_landscape(self, plugin_server):
        w, h = plugin_server._get_page_dimensions("A3", "landscape")
        assert w == 420
        assert h == 297

    def test_a3_portrait(self, plugin_server):
        w, h = plugin_server._get_page_dimensions("A3", "portrait")
        assert w == 297
        assert h == 420

    def test_a4_landscape(self, plugin_server):
        w, h = plugin_server._get_page_dimensions("A4", "landscape")
        assert w == 297
        assert h == 210

    def test_a4_portrait(self, plugin_server):
        w, h = plugin_server._get_page_dimensions("A4", "portrait")
        assert w == 210
        assert h == 297

    def test_letter_landscape(self, plugin_server):
        w, h = plugin_server._get_page_dimensions("letter", "landscape")
        assert w == 279.4
        assert h == 215.9

    def test_letter_portrait(self, plugin_server):
        w, h = plugin_server._get_page_dimensions("letter", "portrait")
        assert w == 215.9
        assert h == 279.4

    def test_tabloid_landscape(self, plugin_server):
        w, h = plugin_server._get_page_dimensions("tabloid", "landscape")
        assert w == 431.8
        assert h == 279.4

    def test_tabloid_portrait(self, plugin_server):
        w, h = plugin_server._get_page_dimensions("tabloid", "portrait")
        assert w == 279.4
        assert h == 431.8

    def test_unknown_defaults_to_a3(self, plugin_server):
        w, h = plugin_server._get_page_dimensions("legal", "landscape")
        assert w == 420
        assert h == 297


class TestExecuteCommandDispatch:
    def test_known_command_dispatches(self, plugin_server):
        """Verify execute_command routes to handler and wraps in success."""
        result = plugin_server.execute_command({"type": "ping", "params": {}})
        assert result["status"] == "success"
        assert result["result"]["pong"] is True

    def test_unknown_command_returns_error(self, plugin_server):
        result = plugin_server.execute_command({"type": "nonexistent_command", "params": {}})
        assert result["status"] == "error"
        assert "Unknown command type" in result["message"]
