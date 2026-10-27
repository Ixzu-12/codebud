"""
tests/test_local_only.py

The default setup never connects anywhere but this machine (phase 8,
Part A4). socket.connect is guarded so that any connection to a
non-loopback address fails the test, and the whole default flow runs
against a fake Ollama on 127.0.0.1: planning, executing a step, building
the project index and searching it -- with OPENAI_API_KEY set, to show it
is not used.
"""

import ipaddress
import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

import pytest

PLAN = {
    "status": "ok",
    "plan": [{"id": "step_0", "description": "list", "tool": "command", "args": {"cmd": "ls"}}],
}


class _FakeOllama(BaseHTTPRequestHandler):
    def _send(self, body: bytes, content_type="application/json"):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send(json.dumps({"models": [{"name": "m:latest"}, {"name": "m"}]}).encode())

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
        if self.path == "/api/embed":
            n = len(request["input"])
            self._send(json.dumps({"embeddings": [[1.0, 0.5, 0.25]] * n}).encode())
            return
        # /api/generate, streamed as NDJSON like Ollama
        line = json.dumps({"response": json.dumps(PLAN), "done": True}) + "\n"
        self._send(line.encode(), "application/x-ndjson")

    def log_message(self, *args):
        pass


def _is_local(address) -> bool:
    if isinstance(address, (str, bytes)):  # AF_UNIX
        return True
    host = address[0]
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@pytest.fixture
def outside_connections(monkeypatch):
    """Record (and refuse) every connection to a non-loopback address."""
    attempts = []
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def guarded(real):
        def connect(self, address):
            if not _is_local(address):
                attempts.append(address)
                raise OSError(f"test refused a connection to {address}")
            return real(self, address)

        return connect

    monkeypatch.setattr(socket.socket, "connect", guarded(real_connect))
    monkeypatch.setattr(socket.socket, "connect_ex", guarded(real_connect_ex))
    return attempts


@pytest.fixture
def fake_ollama(monkeypatch):
    server = HTTPServer(("127.0.0.1", 0), _FakeOllama)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("OLLAMA_BASE_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("OLLAMA_MODEL", "m")
    # Present, to show that it is not used.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-must-not-be-used")
    monkeypatch.delenv("WEB_SEARCH_ENABLED", raising=False)
    monkeypatch.delenv("CODEBUD_EMBED_STUB", raising=False)
    yield
    server.shutdown()


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "app.py").write_text("def main():\n    return 0\n")
    monkeypatch.chdir(root)
    return root


def _codebud(*argv):
    from codebud.cli import main as cli

    with patch.object(sys, "argv", ["codebud", *argv]):
        try:
            cli.main()
        except SystemExit as exc:
            return exc.code or 0
    return 0


def test_run_plans_and_executes_without_leaving_the_machine(
    fake_ollama, project, outside_connections, capsys
):
    assert _codebud("--no-progress", "run", "--yes", "list the files") == 0
    assert "app.py" in capsys.readouterr().err  # the ls step really ran
    assert outside_connections == []


def test_indexing_and_search_stay_local(fake_ollama, project, outside_connections):
    pytest.importorskip("numpy")
    from codebud.tools.embeddings import EmbeddingSearchTool

    assert _codebud("--no-progress", "index") == 0
    result = EmbeddingSearchTool().run({"query": "main"})
    assert result["returncode"] == 0, result["stderr"]
    assert json.loads(result["stdout"])[0]["path"] == "app.py"
    assert outside_connections == []


def test_the_guard_catches_an_outside_connection(outside_connections):
    """The check above would be worthless if the guard let things through."""
    sock = socket.socket()
    try:
        with pytest.raises(OSError, match="refused"):
            sock.connect(("192.0.2.1", 80))
    finally:
        sock.close()
    assert outside_connections == [("192.0.2.1", 80)]
