"""Tests for QgisMCPClient standalone socket client."""

import json
from unittest.mock import MagicMock, patch

import pytest

from qgis_mcp.qgis_socket_client import QgisMCPClient


@pytest.fixture
def mock_client():
    """Client with a pre-connected mock socket."""
    client = QgisMCPClient()
    client.socket = MagicMock()
    return client


@pytest.fixture
def make_client_recv():
    """Configure mock socket recv to return JSON."""

    def _make(client, response):
        client.socket.recv.return_value = json.dumps(response).encode("utf-8")

    return _make


class TestInit:
    def test_defaults(self):
        client = QgisMCPClient()
        assert client.host == "localhost"
        assert client.port == 9876
        assert client.socket is None


class TestConnect:
    @patch("qgis_mcp.qgis_socket_client.socket.socket")
    def test_success(self, mock_socket_class):
        client = QgisMCPClient()
        assert client.connect() is True
        assert client.socket is not None

    @patch("qgis_mcp.qgis_socket_client.socket.socket")
    def test_failure(self, mock_socket_class):
        mock_socket_class.return_value.connect.side_effect = ConnectionRefusedError()
        client = QgisMCPClient()
        assert client.connect() is False


class TestDisconnect:
    def test_with_socket(self, mock_client):
        sock = mock_client.socket
        mock_client.disconnect()
        sock.close.assert_called_once()
        assert mock_client.socket is None

    def test_no_socket(self):
        client = QgisMCPClient()
        client.disconnect()  # should not raise


class TestSendCommand:
    def test_success(self, mock_client, make_client_recv):
        response = {"status": "success", "result": {"pong": True}}
        make_client_recv(mock_client, response)

        result = mock_client.send_command("ping")

        assert result == response
        sent_data = mock_client.socket.sendall.call_args[0][0]
        sent_cmd = json.loads(sent_data.decode("utf-8"))
        assert sent_cmd == {"type": "ping", "params": {}}

    def test_not_connected(self):
        client = QgisMCPClient()
        result = client.send_command("ping")
        assert result is None

    def test_send_error(self, mock_client):
        mock_client.socket.sendall.side_effect = ConnectionError()
        result = mock_client.send_command("ping")
        assert result is None


class TestConvenienceMethods:
    def test_ping(self, mock_client, make_client_recv):
        make_client_recv(mock_client, {"status": "success"})
        mock_client.ping()
        sent = json.loads(mock_client.socket.sendall.call_args[0][0])
        assert sent["type"] == "ping"

    def test_get_qgis_info(self, mock_client, make_client_recv):
        make_client_recv(mock_client, {"status": "success"})
        mock_client.get_qgis_info()
        sent = json.loads(mock_client.socket.sendall.call_args[0][0])
        assert sent["type"] == "get_qgis_info"

    def test_get_project_info(self, mock_client, make_client_recv):
        make_client_recv(mock_client, {"status": "success"})
        mock_client.get_project_info()
        sent = json.loads(mock_client.socket.sendall.call_args[0][0])
        assert sent["type"] == "get_project_info"

    def test_execute_code(self, mock_client, make_client_recv):
        make_client_recv(mock_client, {"status": "success"})
        mock_client.execute_code("print('hi')")
        sent = json.loads(mock_client.socket.sendall.call_args[0][0])
        assert sent["type"] == "execute_code"
        assert sent["params"]["code"] == "print('hi')"

    def test_add_vector_layer_with_name(self, mock_client, make_client_recv):
        make_client_recv(mock_client, {"status": "success"})
        mock_client.add_vector_layer("/data/test.shp", name="test")
        sent = json.loads(mock_client.socket.sendall.call_args[0][0])
        assert sent["params"]["name"] == "test"

    def test_add_vector_layer_without_name(self, mock_client, make_client_recv):
        make_client_recv(mock_client, {"status": "success"})
        mock_client.add_vector_layer("/data/test.shp")
        sent = json.loads(mock_client.socket.sendall.call_args[0][0])
        assert "name" not in sent["params"]

    def test_save_project_with_path(self, mock_client, make_client_recv):
        make_client_recv(mock_client, {"status": "success"})
        mock_client.save_project("/tmp/save.qgz")
        sent = json.loads(mock_client.socket.sendall.call_args[0][0])
        assert sent["params"]["path"] == "/tmp/save.qgz"

    def test_save_project_without_path(self, mock_client, make_client_recv):
        make_client_recv(mock_client, {"status": "success"})
        mock_client.save_project()
        sent = json.loads(mock_client.socket.sendall.call_args[0][0])
        assert sent["params"] == {}

    def test_render_map(self, mock_client, make_client_recv):
        make_client_recv(mock_client, {"status": "success"})
        mock_client.render_map("/tmp/map.png", width=1024, height=768)
        sent = json.loads(mock_client.socket.sendall.call_args[0][0])
        assert sent["type"] == "render_map"
        assert sent["params"]["width"] == 1024
