"""Phase 4 CLI client tests."""
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


class TestParseCommand:
    def test_slash_command(self):
        from kora_v2.cli.app import parse_command
        cmd, args = parse_command("/status")
        assert cmd == "status"
        assert args == ""

    def test_slash_command_with_args(self):
        from kora_v2.cli.app import parse_command
        cmd, args = parse_command("/memory search cats")
        assert cmd == "memory"
        assert args == "search cats"

    def test_regular_message(self):
        from kora_v2.cli.app import parse_command
        cmd, args = parse_command("Hello Kora!")
        assert cmd is None
        assert args == "Hello Kora!"

    def test_empty_slash(self):
        from kora_v2.cli.app import parse_command
        cmd, args = parse_command("/")
        assert cmd == ""
        assert args == ""

    def test_command_case_insensitive(self):
        from kora_v2.cli.app import parse_command
        cmd, args = parse_command("/STATUS")
        assert cmd == "status"

    def test_whitespace_handling(self):
        from kora_v2.cli.app import parse_command
        cmd, args = parse_command("  /quit  ")
        assert cmd == "quit"

    def test_empty_string(self):
        from kora_v2.cli.app import parse_command
        cmd, args = parse_command("")
        assert cmd is None
        assert args == ""

    def test_just_whitespace(self):
        from kora_v2.cli.app import parse_command
        cmd, args = parse_command("   ")
        assert cmd is None
        assert args == ""

    def test_message_starting_with_slash_in_middle(self):
        from kora_v2.cli.app import parse_command
        cmd, args = parse_command("go to /home")
        assert cmd is None
        assert args == "go to /home"


class TestBackoffCalculation:
    def test_exponential_backoff(self):
        from kora_v2.cli.app import calculate_backoff
        assert calculate_backoff(0) == 1
        assert calculate_backoff(1) == 2
        assert calculate_backoff(2) == 4
        assert calculate_backoff(3) == 8
        assert calculate_backoff(4) == 16

    def test_backoff_is_power_of_two(self):
        from kora_v2.cli.app import calculate_backoff
        for i in range(5):
            assert calculate_backoff(i) == 2 ** i


class TestConstants:
    def test_max_attempts(self):
        from kora_v2.cli.app import MAX_RECONNECT_ATTEMPTS
        assert MAX_RECONNECT_ATTEMPTS == 5

    def test_heartbeat_interval(self):
        from kora_v2.cli.app import HEARTBEAT_INTERVAL
        assert HEARTBEAT_INTERVAL == 30


class TestFormatToken:
    def test_passthrough(self):
        from kora_v2.cli.app import format_streaming_token
        assert format_streaming_token("hello") == "hello"

    def test_empty(self):
        from kora_v2.cli.app import format_streaming_token
        assert format_streaming_token("") == ""

    def test_special_chars(self):
        from kora_v2.cli.app import format_streaming_token
        assert format_streaming_token("**bold**") == "**bold**"

    def test_newline(self):
        from kora_v2.cli.app import format_streaming_token
        assert format_streaming_token("\n") == "\n"


class TestKoraCLIInit:
    def test_init_defaults(self):
        from kora_v2.cli.app import KoraCLI
        cli = KoraCLI()
        assert cli.host == "127.0.0.1"
        assert cli.port is None
        assert cli.token is None
        assert cli._ws is None
        assert cli._running is False

    def test_init_custom(self):
        from kora_v2.cli.app import KoraCLI
        cli = KoraCLI(host="localhost", port=8080, token="test123")
        assert cli.host == "localhost"
        assert cli.port == 8080
        assert cli.token == "test123"

    def test_response_buffer_empty_on_init(self):
        from kora_v2.cli.app import KoraCLI
        cli = KoraCLI()
        assert cli._response_buffer == ""

    def test_session_id_none_on_init(self):
        from kora_v2.cli.app import KoraCLI
        cli = KoraCLI()
        assert cli._session_id is None


class TestDiscoverPort:
    def test_discover_port_no_lockfile(self, tmp_path):
        from kora_v2.cli.app import KoraCLI
        cli = KoraCLI()
        # Point to non-existent lockfile
        with patch.object(cli, '_lockfile_path', tmp_path / "nonexistent.json"):
            port = cli._discover_port()
        assert port is None or isinstance(port, int)

    def test_discover_port_with_api_port(self, tmp_path):
        from kora_v2.cli.app import KoraCLI
        lockfile = tmp_path / ".lockfile"
        lockfile.write_text(json.dumps({"pid": 1234, "api_port": 9999, "state": "ready"}))
        cli = KoraCLI()
        cli._lockfile_path = lockfile
        port = cli._discover_port()
        assert port == 9999

    def test_discover_port_with_port_key(self, tmp_path):
        from kora_v2.cli.app import KoraCLI
        lockfile = tmp_path / ".lockfile"
        lockfile.write_text(json.dumps({"pid": 1234, "port": 8888}))
        cli = KoraCLI()
        cli._lockfile_path = lockfile
        port = cli._discover_port()
        assert port == 8888

    def test_discover_port_invalid_json(self, tmp_path):
        from kora_v2.cli.app import KoraCLI
        lockfile = tmp_path / ".lockfile"
        lockfile.write_text("not-valid-json{{{")
        cli = KoraCLI()
        cli._lockfile_path = lockfile
        port = cli._discover_port()
        assert port is None

    def test_discover_port_prefers_api_port_over_port(self, tmp_path):
        from kora_v2.cli.app import KoraCLI
        lockfile = tmp_path / ".lockfile"
        lockfile.write_text(json.dumps({"api_port": 7777, "port": 8888}))
        cli = KoraCLI()
        cli._lockfile_path = lockfile
        port = cli._discover_port()
        assert port == 7777


class TestReadToken:
    def test_read_token_no_file(self, tmp_path):
        from kora_v2.cli.app import KoraCLI
        cli = KoraCLI()
        cli._token_path = tmp_path / "nonexistent"
        token = cli._read_token()
        assert token is None

    def test_read_token_with_file(self, tmp_path):
        from kora_v2.cli.app import KoraCLI
        token_file = tmp_path / ".api_token"
        token_file.write_text("my-secret-token\n")
        cli = KoraCLI()
        cli._token_path = token_file
        token = cli._read_token()
        assert token == "my-secret-token"

    def test_read_token_strips_whitespace(self, tmp_path):
        from kora_v2.cli.app import KoraCLI
        token_file = tmp_path / ".api_token"
        token_file.write_text("  abc123  \n")
        cli = KoraCLI()
        cli._token_path = token_file
        token = cli._read_token()
        assert token == "abc123"


class TestHandleCommand:
    @pytest.mark.asyncio
    async def test_handle_command_quit(self):
        from kora_v2.cli.app import KoraCLI
        cli = KoraCLI()
        result = await cli._handle_command("quit", "")
        assert result is False

    @pytest.mark.asyncio
    async def test_handle_command_exit(self):
        from kora_v2.cli.app import KoraCLI
        cli = KoraCLI()
        result = await cli._handle_command("exit", "")
        assert result is False

    @pytest.mark.asyncio
    async def test_handle_command_help(self):
        from kora_v2.cli.app import KoraCLI
        cli = KoraCLI()
        result = await cli._handle_command("help", "")
        assert result is True

    @pytest.mark.asyncio
    async def test_handle_command_status(self):
        from kora_v2.cli.app import KoraCLI
        cli = KoraCLI()
        result = await cli._handle_command("status", "")
        assert result is True

    @pytest.mark.asyncio
    async def test_handle_command_stop(self):
        from kora_v2.cli.app import KoraCLI
        cli = KoraCLI()
        result = await cli._handle_command("stop", "")
        assert result is False

    @pytest.mark.asyncio
    async def test_handle_command_memory(self):
        from kora_v2.cli.app import KoraCLI
        cli = KoraCLI()
        result = await cli._handle_command("memory", "")
        assert result is True

    @pytest.mark.asyncio
    async def test_handle_command_plan(self):
        from kora_v2.cli.app import KoraCLI
        cli = KoraCLI()
        result = await cli._handle_command("plan", "")
        assert result is True

    @pytest.mark.asyncio
    async def test_handle_command_compact(self):
        from kora_v2.cli.app import KoraCLI
        cli = KoraCLI()
        result = await cli._handle_command("compact", "")
        assert result is True

    @pytest.mark.asyncio
    async def test_handle_command_unknown(self):
        from kora_v2.cli.app import KoraCLI
        cli = KoraCLI()
        result = await cli._handle_command("nonexistent", "")
        assert result is True  # Unknown commands don't exit

    @pytest.mark.asyncio
    async def test_handle_command_with_args(self):
        from kora_v2.cli.app import KoraCLI
        cli = KoraCLI()
        result = await cli._handle_command("memory", "search cats")
        assert result is True


class TestConnectMethod:
    @pytest.mark.asyncio
    async def test_connect_no_port_returns_false(self):
        from kora_v2.cli.app import KoraCLI
        cli = KoraCLI()
        # No port discoverable (no lockfile, no explicit port)
        with patch.object(cli, '_discover_port', return_value=None):
            with patch.object(cli, '_read_token', return_value="test-token"):
                result = await cli.connect()
        assert result is False

    @pytest.mark.asyncio
    async def test_connect_no_token_returns_false(self):
        from kora_v2.cli.app import KoraCLI
        cli = KoraCLI()
        with patch.object(cli, '_discover_port', return_value=9000):
            with patch.object(cli, '_read_token', return_value=None):
                result = await cli.connect()
        assert result is False

    @pytest.mark.asyncio
    async def test_connect_uses_explicit_port(self):
        from kora_v2.cli.app import KoraCLI
        cli = KoraCLI(port=9000, token="tok")
        mock_ws = AsyncMock()
        with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
            mock_connect.return_value = mock_ws
            result = await cli.connect()
        assert result is True
        assert cli._ws is mock_ws
        mock_connect.assert_called_once()
        call_args = mock_connect.call_args[0][0]
        assert "9000" in call_args
        assert "tok" in call_args

    @pytest.mark.asyncio
    async def test_connect_uses_explicit_token(self):
        from kora_v2.cli.app import KoraCLI
        cli = KoraCLI(port=9000, token="mytoken")
        mock_ws = AsyncMock()
        with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
            mock_connect.return_value = mock_ws
            await cli.connect()
        call_uri = mock_connect.call_args[0][0]
        assert "mytoken" in call_uri

    @pytest.mark.asyncio
    async def test_connect_ws_exception_returns_false(self):
        from kora_v2.cli.app import KoraCLI
        cli = KoraCLI(port=9000, token="tok")
        with patch("websockets.connect", side_effect=ConnectionRefusedError("refused")):
            result = await cli.connect()
        assert result is False

    @pytest.mark.asyncio
    async def test_connect_builds_correct_uri(self):
        from kora_v2.cli.app import KoraCLI
        cli = KoraCLI(host="127.0.0.1", port=8765, token="abc")
        mock_ws = AsyncMock()
        with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
            mock_connect.return_value = mock_ws
            await cli.connect()
        uri = mock_connect.call_args[0][0]
        assert uri == "ws://127.0.0.1:8765/api/v1/ws?token=abc"


class TestReconnect:
    @pytest.mark.asyncio
    async def test_reconnect_succeeds_on_first_attempt(self):
        from kora_v2.cli.app import KoraCLI
        cli = KoraCLI()
        with patch.object(cli, 'connect', return_value=True):
            with patch('asyncio.sleep', new_callable=AsyncMock):
                result = await cli.reconnect()
        assert result is True

    @pytest.mark.asyncio
    async def test_reconnect_fails_all_attempts(self):
        from kora_v2.cli.app import KoraCLI
        cli = KoraCLI()
        with patch.object(cli, 'connect', return_value=False):
            with patch('asyncio.sleep', new_callable=AsyncMock):
                result = await cli.reconnect()
        assert result is False

    @pytest.mark.asyncio
    async def test_reconnect_succeeds_on_third_attempt(self):
        from kora_v2.cli.app import KoraCLI
        cli = KoraCLI()
        call_count = 0

        async def mock_connect():
            nonlocal call_count
            call_count += 1
            return call_count >= 3

        with patch.object(cli, 'connect', side_effect=mock_connect):
            with patch('asyncio.sleep', new_callable=AsyncMock):
                result = await cli.reconnect()
        assert result is True
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_reconnect_uses_backoff_delays(self):
        from kora_v2.cli.app import KoraCLI, MAX_RECONNECT_ATTEMPTS
        cli = KoraCLI()
        sleep_calls = []

        async def track_sleep(delay):
            sleep_calls.append(delay)

        with patch.object(cli, 'connect', return_value=False):
            with patch('asyncio.sleep', side_effect=track_sleep):
                await cli.reconnect()

        assert len(sleep_calls) == MAX_RECONNECT_ATTEMPTS
        # Delays should be 1, 2, 4, 8, 16 (exponential)
        assert sleep_calls == [1, 2, 4, 8, 16]


class TestCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_closes_ws(self):
        from kora_v2.cli.app import KoraCLI
        cli = KoraCLI()
        mock_ws = AsyncMock()
        cli._ws = mock_ws
        await cli._cleanup()
        mock_ws.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_handles_no_ws(self):
        from kora_v2.cli.app import KoraCLI
        cli = KoraCLI()
        cli._ws = None
        # Should not raise
        await cli._cleanup()

    @pytest.mark.asyncio
    async def test_cleanup_handles_close_exception(self):
        from kora_v2.cli.app import KoraCLI
        cli = KoraCLI()
        mock_ws = AsyncMock()
        mock_ws.close.side_effect = Exception("already closed")
        cli._ws = mock_ws
        # Should not raise
        await cli._cleanup()


class TestSendMessage:
    @pytest.mark.asyncio
    async def test_send_message_no_ws(self):
        from kora_v2.cli.app import KoraCLI
        cli = KoraCLI()
        cli._ws = None
        # Should not raise
        await cli._send_message("hello")

    @pytest.mark.asyncio
    async def test_send_message_sends_chat_envelope(self):
        from kora_v2.cli.app import KoraCLI
        cli = KoraCLI()

        messages_received = []

        async def fake_recv():
            return json.dumps({"type": "response_complete"})

        mock_ws = AsyncMock()
        mock_ws.recv = fake_recv

        async def capture_send(data):
            messages_received.append(json.loads(data))

        mock_ws.send = capture_send
        cli._ws = mock_ws

        await cli._send_message("test message")

        assert len(messages_received) >= 1
        first_msg = messages_received[0]
        assert first_msg["type"] == "chat"
        assert first_msg["content"] == "test message"

    @pytest.mark.asyncio
    async def test_send_message_accumulates_tokens(self):
        from kora_v2.cli.app import KoraCLI
        cli = KoraCLI()

        recv_queue = [
            json.dumps({"type": "token", "content": "Hello"}),
            json.dumps({"type": "token", "content": " world"}),
            json.dumps({"type": "response_complete"}),
        ]
        idx = 0

        async def fake_recv():
            nonlocal idx
            val = recv_queue[idx]
            idx += 1
            return val

        mock_ws = AsyncMock()
        mock_ws.recv = fake_recv
        mock_ws.send = AsyncMock()
        cli._ws = mock_ws

        await cli._send_message("hi")
        assert cli._response_buffer == "Hello world"

    @pytest.mark.asyncio
    async def test_send_message_responds_to_ping(self):
        from kora_v2.cli.app import KoraCLI
        cli = KoraCLI()

        sent_messages = []
        recv_queue = [
            json.dumps({"type": "ping"}),
            json.dumps({"type": "response_complete"}),
        ]
        idx = 0

        async def fake_recv():
            nonlocal idx
            val = recv_queue[idx]
            idx += 1
            return val

        async def capture_send(data):
            sent_messages.append(json.loads(data))

        mock_ws = AsyncMock()
        mock_ws.recv = fake_recv
        mock_ws.send = capture_send
        cli._ws = mock_ws

        await cli._send_message("ping test")

        pong_msgs = [m for m in sent_messages if m.get("type") == "pong"]
        assert len(pong_msgs) == 1

    @pytest.mark.asyncio
    async def test_send_message_handles_error_response(self):
        from kora_v2.cli.app import KoraCLI
        cli = KoraCLI()

        recv_queue = [
            json.dumps({"type": "error", "content": "something went wrong"}),
        ]
        idx = 0

        async def fake_recv():
            nonlocal idx
            val = recv_queue[idx]
            idx += 1
            return val

        mock_ws = AsyncMock()
        mock_ws.recv = fake_recv
        mock_ws.send = AsyncMock()
        cli._ws = mock_ws

        # Should not raise
        await cli._send_message("test")
