"""Tests for QgisMCPServer socket client class."""

import json
from unittest.mock import MagicMock, patch

import pytest

from qgis_mcp.qgis_mcp_server import QgisMCPServer


class TestInit:
    def test_defaults(self):
        server = QgisMCPServer()
        assert server.host == "localhost"
        assert server.port == 9876
        assert server.socket is None

    def test_custom_host_port(self):
        server = QgisMCPServer(host="192.168.1.1", port=1234)
        assert server.host == "192.168.1.1"
        assert server.port == 1234


class TestConnect:
    @patch("qgis_mcp.qgis_mcp_server.socket.socket")
    def test_connect_success(self, mock_socket_class):
        server = QgisMCPServer()
        result = server.connect()
        assert result is True
        assert server.socket is not None
        mock_socket_class.return_value.connect.assert_called_once_with(("localhost", 9876))
        mock_socket_class.return_value.settimeout.assert_called_once_with(120)

    @patch("qgis_mcp.qgis_mcp_server.socket.socket")
    def test_connect_failure(self, mock_socket_class):
        mock_socket_class.return_value.connect.side_effect = ConnectionRefusedError()
        server = QgisMCPServer()
        result = server.connect()
        assert result is False
        assert server.socket is None


class TestDisconnect:
    def test_disconnect_with_socket(self, mock_qgis_server):
        mock_sock = mock_qgis_server.socket
        mock_qgis_server.disconnect()
        mock_sock.close.assert_called_once()
        assert mock_qgis_server.socket is None

    def test_disconnect_no_socket(self):
        server = QgisMCPServer()
        server.disconnect()  # should not raise
        assert server.socket is None


class TestIsConnected:
    def test_connected(self, mock_qgis_server):
        assert mock_qgis_server._is_connected() is True

    def test_no_socket(self):
        server = QgisMCPServer()
        assert server._is_connected() is False

    def test_socket_error(self, mock_qgis_server):
        mock_qgis_server.socket.getsockopt.side_effect = OSError("socket error")
        assert mock_qgis_server._is_connected() is False

    def test_nonzero_so_error(self, mock_qgis_server):
        mock_qgis_server.socket.getsockopt.return_value = 111  # Connection refused
        assert mock_qgis_server._is_connected() is False


class TestReconnect:
    @patch("qgis_mcp.qgis_mcp_server.socket.socket")
    def test_reconnect_success(self, mock_socket_class, mock_qgis_server):
        result = mock_qgis_server._reconnect()
        assert result is True
        assert mock_qgis_server.socket is not None

    @patch("qgis_mcp.qgis_mcp_server.socket.socket")
    def test_reconnect_failure(self, mock_socket_class, mock_qgis_server):
        mock_socket_class.return_value.connect.side_effect = ConnectionRefusedError()
        result = mock_qgis_server._reconnect()
        assert result is False


class TestSendCommand:
    def test_success(self, mock_qgis_server, make_recv_response):
        response = {"status": "success", "result": {"pong": True}}
        make_recv_response(mock_qgis_server.socket, response)

        result = mock_qgis_server.send_command("ping")

        assert result == response
        sent_data = mock_qgis_server.socket.sendall.call_args[0][0]
        sent_cmd = json.loads(sent_data.decode("utf-8"))
        assert sent_cmd == {"type": "ping", "params": {}}

    def test_with_params(self, mock_qgis_server, make_recv_response):
        response = {"status": "success", "result": {}}
        make_recv_response(mock_qgis_server.socket, response)

        mock_qgis_server.send_command("load_project", {"path": "/tmp/test.qgz"})

        sent_data = mock_qgis_server.socket.sendall.call_args[0][0]
        sent_cmd = json.loads(sent_data.decode("utf-8"))
        assert sent_cmd == {"type": "load_project", "params": {"path": "/tmp/test.qgz"}}

    def test_custom_timeout(self, mock_qgis_server, make_recv_response):
        response = {"status": "success", "result": {}}
        make_recv_response(mock_qgis_server.socket, response)

        mock_qgis_server.send_command("trace_downstream", timeout=300)

        mock_qgis_server.socket.settimeout.assert_any_call(300)
        # Should reset timeout after
        mock_qgis_server.socket.settimeout.assert_called_with(QgisMCPServer.DEFAULT_TIMEOUT)

    def test_timeout_raises(self, mock_qgis_server):
        mock_qgis_server.socket.recv.side_effect = TimeoutError()

        with pytest.raises(Exception, match="Timeout"):
            mock_qgis_server.send_command("ping")

    def test_connection_closed_raises(self, mock_qgis_server):
        mock_qgis_server.socket.recv.return_value = b""

        with pytest.raises(Exception, match="Connection closed"):
            mock_qgis_server.send_command("ping")

    def test_chunked_response(self, mock_qgis_server):
        response = {"status": "success", "result": {"data": "x" * 1000}}
        full_bytes = json.dumps(response).encode("utf-8")
        chunk1 = full_bytes[:50]
        chunk2 = full_bytes[50:]

        mock_qgis_server.socket.recv.side_effect = [chunk1, chunk2]

        result = mock_qgis_server.send_command("ping")
        assert result == response

    @patch("qgis_mcp.qgis_mcp_server.socket.socket")
    def test_reconnects_when_disconnected(self, mock_socket_class):
        server = QgisMCPServer()
        # socket is None, so _is_connected returns False
        # _reconnect should create a new socket
        response = {"status": "success", "result": {}}
        mock_socket_class.return_value.recv.return_value = json.dumps(response).encode("utf-8")
        mock_socket_class.return_value.getsockopt.return_value = 0

        result = server.send_command("ping")
        assert result == response

    def test_raises_when_reconnect_fails(self):
        server = QgisMCPServer()
        # socket is None and connect will fail
        with patch("qgis_mcp.qgis_mcp_server.socket.socket") as mock_cls:
            mock_cls.return_value.connect.side_effect = ConnectionRefusedError()
            with pytest.raises(Exception, match="Could not connect"):
                server.send_command("ping")

    @patch("qgis_mcp.qgis_mcp_server.socket.socket")
    def test_connection_error_retries(self, mock_socket_class, mock_qgis_server):
        response = {"status": "success", "result": {}}
        # First sendall raises, then after reconnect it works
        mock_qgis_server.socket.sendall.side_effect = BrokenPipeError()

        new_sock = MagicMock()
        new_sock.getsockopt.return_value = 0
        new_sock.recv.return_value = json.dumps(response).encode("utf-8")
        mock_socket_class.return_value = new_sock

        result = mock_qgis_server.send_command("ping")
        assert result == response
