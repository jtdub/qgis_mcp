"""Tests for get_qgis_connection() module-level connection manager."""

from unittest.mock import MagicMock, patch

import pytest

import qgis_mcp.qgis_mcp_server as mod
from qgis_mcp.qgis_mcp_server import get_qgis_connection


class TestGetQgisConnection:
    @patch("qgis_mcp.qgis_mcp_server.QgisMCPServer")
    def test_creates_new_connection(self, mock_cls):
        mock_server = MagicMock()
        mock_server.connect.return_value = True
        mock_server._is_connected.return_value = True
        mock_cls.return_value = mock_server

        conn = get_qgis_connection()

        mock_cls.assert_called_once_with(host="localhost", port=9876)
        mock_server.connect.assert_called_once()
        assert conn is mock_server

    def test_returns_existing_valid_connection(self):
        mock_server = MagicMock()
        mock_server._is_connected.return_value = True
        mod._qgis_connection = mock_server

        conn = get_qgis_connection()

        assert conn is mock_server

    @patch("qgis_mcp.qgis_mcp_server.QgisMCPServer")
    def test_replaces_dead_connection(self, mock_cls):
        old_server = MagicMock()
        old_server._is_connected.return_value = False
        mod._qgis_connection = old_server

        new_server = MagicMock()
        new_server.connect.return_value = True
        new_server._is_connected.return_value = True
        mock_cls.return_value = new_server

        conn = get_qgis_connection()

        old_server.disconnect.assert_called_once()
        assert conn is new_server

    @patch("qgis_mcp.qgis_mcp_server.QgisMCPServer")
    def test_raises_on_connect_failure(self, mock_cls):
        mock_server = MagicMock()
        mock_server.connect.return_value = False
        mock_cls.return_value = mock_server

        with pytest.raises(Exception, match="Could not connect to QGIS"):
            get_qgis_connection()

        assert mod._qgis_connection is None

    @patch("qgis_mcp.qgis_mcp_server.QgisMCPServer")
    def test_uses_correct_host_port(self, mock_cls):
        mock_server = MagicMock()
        mock_server.connect.return_value = True
        mock_server._is_connected.return_value = True
        mock_cls.return_value = mock_server

        get_qgis_connection()

        mock_cls.assert_called_with(host="localhost", port=9876)
