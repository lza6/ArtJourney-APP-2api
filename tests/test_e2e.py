import io
import sys
from pathlib import Path

import pytest
import requests
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
KEY = "sk-artjourney-demo"


def test_upstream_down_returns_502(dead_upstream_gateway):
    r = requests.post(
        f"{dead_upstream_gateway}/v1/images/generations",
        json={"prompt": "x", "model": "anime-v1", "size": "512x512"},
        headers={"Authorization": f"Bearer {KEY}"},
        timeout=30,
    )
    assert r.status_code == 502, r.text
    body = r.json()
    assert body["detail"]["error"]["type"] == "upstream_unreachable"
    assert "unreachable" in body["detail"]["error"]["message"].lower()


def test_mock_health(servers):
    r = requests.get(f"{servers['mock']}/health", timeout=10)
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_gateway_health(servers):
    r = requests.get(f"{servers['gw']}/health", timeout=10)
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "mock"
    assert body["styles"] >= 1


def test_requires_auth(servers):
    r = requests.get(f"{servers['gw']}/v1/models", timeout=10)
    assert r.status_code == 401
    r = requests.get(f"{servers['gw']}/v1/models", headers={"Authorization": "Bearer wrong"}, timeout=10)
    assert r.status_code == 401


def test_models_list(servers):
    r = requests.get(f"{servers['gw']}/v1/models", headers={"Authorization": f"Bearer {KEY}"}, timeout=10)
    assert r.status_code == 200
    models = r.json()["data"]
    assert models and all(m["id"] for m in models)


def test_generation_returns_fetchable_image(servers):
    payload = {"prompt": "a cat astronaut, neon", "model": "anime-v1", "size": "1024x1536", "n": 1}
    r = requests.post(
        f"{servers['gw']}/v1/images/generations",
        json=payload,
        headers={"Authorization": f"Bearer {KEY}"},
        timeout=60,
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert len(data) == 1
    url = data[0]["url"]
    assert url.startswith("http")
    img = requests.get(url, timeout=20)
    assert img.status_code == 200
    assert img.headers["content-type"] == "image/png"
    assert img.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_generation_b64(servers):
    payload = {"prompt": "sunset over mountains", "model": "oil-v2", "size": "512x512", "response_format": "b64_json"}
    r = requests.post(
        f"{servers['gw']}/v1/images/generations",
        json=payload,
        headers={"Authorization": f"Bearer {KEY}"},
        timeout=60,
    )
    assert r.status_code == 200, r.text
    b64 = r.json()["data"][0]["b64_json"]
    assert b64.startswith("iVBOR")


def test_generation_rejects_empty_prompt(servers):
    r = requests.post(
        f"{servers['gw']}/v1/images/generations",
        json={"prompt": "  "},
        headers={"Authorization": f"Bearer {KEY}"},
        timeout=20,
    )
    assert r.status_code == 400


def test_edits_flow(servers):
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (10, 200, 30)).save(buf, "PNG")
    files = {"image": ("in.png", buf.getvalue(), "image/png")}
    data = {"prompt": "turn into watercolor", "model": "anime-v1", "size": "1024x1024"}
    r = requests.post(
        f"{servers['gw']}/v1/images/edits",
        files=files,
        data=data,
        headers={"Authorization": f"Bearer {KEY}"},
        timeout=60,
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"][0]["url"].startswith("http")


def test_mock_rejects_bad_signature(servers):
    r = requests.post(
        f"{servers['mock']}/api/v5/art",
        data={"prompt": "x", "ratio": "1:1", "style": "anime-v1"},
        headers={"signature": "deadbeef"},
        timeout=20,
    )
    assert r.status_code == 401


def test_upstream_shape_matches_app_models(servers):
    """mock 返回的字段必须覆盖 App 端 Gson 模型的字段，否则真实对接会崩。"""
    sys.path.insert(0, str(ROOT))
    from app import sign as signing

    sig = signing.sign("p", "1:1", "anime-v1")
    r = requests.post(
        f"{servers['mock']}/api/v5/art",
        data={"prompt": "p", "ratio": "1:1", "style": "anime-v1"},
        headers={"signature": sig},
        timeout=20,
    )
    assert r.status_code == 200
    body = r.json()
    assert set(["code", "status", "art"]).issubset(body)
    art = body["art"]
    for field in ("id", "status", "image", "temp_image", "prompt", "ratio", "style", "seed_id", "count_down", "haveBannedWord"):
        assert field in art, field
