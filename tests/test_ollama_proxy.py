"""
tests/test_ollama_proxy.py

A local Ollama must stay reachable when the user's environment sets
HTTP_PROXY (common with privacy proxies such as Privoxy) and no NO_PROXY:
requests honours HTTP_PROXY even for localhost, so every call would go to
the proxy instead. These tests run a real HTTP server on 127.0.0.1 that
answers like Ollama, with HTTP_PROXY pointing at a dead port.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from codebud import llm_stub
from codebud.cli import doctor


class _FakeOllama(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"models": [{"name": "m:latest"}], "version": "9.9.9"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def local_ollama(monkeypatch):
    server = HTTPServer(("127.0.0.1", 0), _FakeOllama)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    # Nothing listens on port 9: a request routed via the proxy fails.
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.setattr(llm_stub, "_OLLAMA_BASE", base)
    monkeypatch.setattr(doctor, "_OLLAMA_BASE", base)
    monkeypatch.setenv("OLLAMA_BASE_URL", base)  # read at call time by run_agent
    yield base
    server.shutdown()


def test_local_ollama_reachable_despite_http_proxy(local_ollama):
    assert llm_stub._ollama_available() is True


def test_list_models_bypasses_proxy_for_local_ollama(local_ollama):
    assert llm_stub.list_models() == ["m:latest"]


def test_doctor_reaches_local_ollama_despite_http_proxy(local_ollama):
    ok, message, _hint = doctor.check_ollama_reachable()
    assert ok, message


def test_doctor_model_check_bypasses_proxy(local_ollama, monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "m:latest")
    ok, message, _hint = doctor.check_model_pulled()
    assert ok, message


@pytest.mark.parametrize(
    "base",
    ["http://localhost:11434", "http://127.0.0.1:11434", "http://[::1]:11434"],
)
def test_loopback_bases_bypass_proxy(base):
    assert llm_stub.ollama_proxies(base) == {"http": None, "https": None, "all": None}


def test_remote_base_keeps_environment_proxy():
    assert llm_stub.ollama_proxies("http://gpu-box.lan:11434") is None


def _run_cli(argv, monkeypatch):
    from codebud.cli import main as run_agent

    monkeypatch.setattr("sys.argv", ["codebud", *argv])
    try:
        run_agent.main()
    except SystemExit as exc:
        return exc.code
    return 0


def test_models_subcommand_bypasses_proxy(local_ollama, monkeypatch, capsys):
    assert _run_cli(["models"], monkeypatch) in (0, None)
    assert "m:latest" in capsys.readouterr().out


def test_version_subcommand_bypasses_proxy(local_ollama, monkeypatch, capsys):
    _run_cli(["version"], monkeypatch)
    assert "9.9.9" in capsys.readouterr().out
