import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
KEY = "sk-artjourney-demo"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


MOCK_PORT = _free_port()
GATEWAY_PORT = _free_port()
DEAD_PORT = _free_port()
SLOW_PORT = _free_port()


def _wait_port(port: int, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.2)
    raise RuntimeError(f"port {port} not ready")


def _spawn(module: str, port: int, env: dict) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", module, "--port", str(port), "--log-level", "error"],
        cwd=ROOT,
        env=env,
    )


def _base_env(**overrides: str) -> dict:
    env = dict(os.environ)
    env.update(
        {
            "GATEWAY_API_KEY": KEY,
            "ARTJOURNEY_MODE": "mock",
            "ARTJOURNEY_MOCK_URL": f"http://127.0.0.1:{MOCK_PORT}",
            "ARTJOURNEY_POLL_INTERVAL": "0.05",
            "ARTJOURNEY_POLL_TIMEOUT": "30",
            "MOCK_DELAY": "0.2",
            "MOCK_POLLS": "0",
            "GATEWAY_MAX_CONCURRENCY": "16",
        }
    )
    env.update(overrides)
    return env


@pytest.fixture(scope="session")
def mock_server():
    proc = _spawn("app.mock_upstream:app", MOCK_PORT, _base_env())
    try:
        _wait_port(MOCK_PORT)
        yield f"http://127.0.0.1:{MOCK_PORT}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(scope="session")
def servers(mock_server):
    proc = _spawn("app.gateway:app", GATEWAY_PORT, _base_env())
    try:
        _wait_port(GATEWAY_PORT)
        yield {"mock": mock_server, "gw": f"http://127.0.0.1:{GATEWAY_PORT}"}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(scope="session")
def dead_upstream_gateway():
    """real 模式 + 打不通的上游：验证错误路径。"""
    env = _base_env(ARTJOURNEY_MODE="real", ARTJOURNEY_BASE_URL="http://127.0.0.1:9", ARTJOURNEY_POLL_TIMEOUT="5")
    proc = _spawn("app.gateway:app", DEAD_PORT, env)
    try:
        _wait_port(DEAD_PORT)
        yield f"http://127.0.0.1:{DEAD_PORT}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(scope="session")
def short_timeout_gateway(mock_server):
    """轮询超时只有 2s 的网关：验证 504 gateway_timeout。"""
    env = _base_env(ARTJOURNEY_POLL_TIMEOUT="2", ARTJOURNEY_POLL_INTERVAL="0.2")
    proc = _spawn("app.gateway:app", SLOW_PORT, env)
    try:
        _wait_port(SLOW_PORT)
        yield f"http://127.0.0.1:{SLOW_PORT}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
