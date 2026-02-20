"""Tests for server_lifespan async context manager."""

from unittest.mock import MagicMock, patch

import pytest

import qgis_mcp.qgis_mcp_server as mod
from qgis_mcp.qgis_mcp_server import server_lifespan


@pytest.mark.asyncio
async def test_lifespan_connects_on_startup():
    mock_server = MagicMock()
    with patch.object(mod, "get_qgis_connection") as mock_get:
        async with server_lifespan(mock_server) as ctx:
            mock_get.assert_called_once()
            assert ctx == {}


@pytest.mark.asyncio
async def test_lifespan_warns_on_connection_failure():
    """Lifespan should not crash if QGIS is unavailable."""
    mock_server = MagicMock()
    with patch.object(mod, "get_qgis_connection", side_effect=Exception("no connection")):
        async with server_lifespan(mock_server) as ctx:
            assert ctx == {}


@pytest.mark.asyncio
async def test_lifespan_yields_empty_dict():
    mock_server = MagicMock()
    with patch.object(mod, "get_qgis_connection"):
        async with server_lifespan(mock_server) as ctx:
            assert ctx == {}


@pytest.mark.asyncio
async def test_lifespan_disconnects_on_shutdown():
    mock_conn = MagicMock()
    mock_server = MagicMock()

    with patch.object(mod, "get_qgis_connection", return_value=mock_conn):
        mod._qgis_connection = mock_conn
        async with server_lifespan(mock_server):
            pass

    mock_conn.disconnect.assert_called_once()
    assert mod._qgis_connection is None
