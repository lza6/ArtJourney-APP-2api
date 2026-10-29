"""边界与异常用例：签名边界、上游故障注入、并发、超时、取消语义。"""

import concurrent.futures
import sys
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import sign as signing  # noqa: E402

KEY = "sk-artjourney-demo"
AUTH = {"Authorization": f"Bearer {KEY}"}
GEN = "/v1/images/generations"


def _gen(base: str, **body) -> requests.Response:
    payload = {"prompt": "edge test", "model": "anime-v1", "size": "512x512"}
    payload.update(body)
    return requests.post(f"{base}{GEN}", json=payload, headers=AUTH, timeout=180)


# ---------------------------------------------------------------- 签名边界


@pytest.mark.parametrize(
    "prompt,ratio,style",
    [
        ("", "1:1", ""),  # 空 prompt + 空 style（原码允许）
        (" ", "1:1", "anime-v1"),
        ("a" * 8192, "1:1", "anime-v1"),  # 超长
        ("带 & 和 = 的提示词", "2:3", "anime-v1"),  # 会污染分隔符的字符（原样拼接，与原码一致）
        ("line1\nline2\ttab", "3:2", "anime-v1"),  # 换行/制表
        ("emoji 🎨🐱", "1:1", "render3d-v1"),
        ("'quote\"和\\反斜杠", "1:1", "anime-v1"),
    ],
)
def test_signature_handles_extreme_inputs(prompt, ratio, style):
    sig = signing.sign(prompt, ratio, style)
    assert len(sig) == 64
    assert all(c in "0123456789abcdef" for c in sig)  # header 安全（纯 hex）
    msg = signing.canonical_message(prompt, ratio, style)
    assert msg == f"prompt={prompt}&ratio={ratio}&style={style}"


def test_optional_params_change_signature():
    base = signing.sign("p", "1:1", "s")
    assert base != signing.sign("p", "1:1", "s", seed_id="1")
    assert base != signing.sign("p", "1:1", "s", steps="20")
    assert base != signing.sign("p", "1:1", "s", cfg_scale="7")
    assert base != signing.sign("p", "1:1", "s", negative_prompt="bad")
    assert signing.sign("p", "1:1", "s", seed_id=1) == signing.sign("p", "1:1", "s", seed_id="1")


def test_optional_param_order_is_fixed():
    """原码拼接顺序固定为 seed_id, steps, cfg_scale, negative_prompt。"""
    msg = signing.canonical_message("p", "1:1", "s", "9", "20", "7", "np")
    assert msg.index("seed_id") < msg.index("steps") < msg.index("cfg_scale") < msg.index("negative_prompt")


def test_key_override_wins_over_default():
    assert signing.sign("p", "1:1", "s", key="other") != signing.sign("p", "1:1", "s")


# ------------------------------------------------------- 网关参数与异常路径


@pytest.mark.parametrize("n", [99, 0, -3, "abc", None])
def test_n_is_clamped(servers, n):
    r = _gen(servers["gw"], n=n)
    assert r.status_code == 200, r.text
    assert 1 <= len(r.json()["data"]) <= 4


def test_unknown_model_passthrough(servers):
    r = _gen(servers["gw"], model="brand-new-style-v9")
    assert r.status_code == 200, r.text
    assert r.json()["data"][0]["url"].startswith("http")


def test_model_name_alias(servers):
    for alias in ("Anime", "anime", "ANIME"):
        r = _gen(servers["gw"], model=alias)
        assert r.status_code == 200, (alias, r.text)


@pytest.mark.parametrize("size,expect_ok", [("garbage", True), ("3000x1000", True), ("2:3", True), ("", True), (None, True)])
def test_size_fallbacks(servers, size, expect_ok):
    r = _gen(servers["gw"], size=size)
    assert (r.status_code == 200) is expect_ok, r.text


def test_upstream_http500_maps_502(servers):
    r = _gen(servers["gw"], model="__sim_http500__")
    assert r.status_code == 502, r.text
    assert r.json()["detail"]["error"]["type"] == "upstream_error"


def test_upstream_malformed_body_maps_502(servers):
    r = _gen(servers["gw"], model="__sim_malformed__")
    assert r.status_code == 502, r.text
    assert r.json()["detail"]["error"]["type"] == "upstream_error"


def test_upstream_banned_prompt_maps_400(servers):
    r = _gen(servers["gw"], model="__sim_banned__")
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["error"]["type"] == "content_filter"


def test_upstream_image_404_maps_502(servers):
    r = _gen(servers["gw"], model="__sim_image404__", response_format="b64_json")
    assert r.status_code == 502, r.text
    assert "image download failed" in r.json()["detail"]["error"]["message"]


def test_poll_timeout_maps_504(short_timeout_gateway):
    r = _gen(short_timeout_gateway, model="__sim_slow__")
    assert r.status_code == 504, r.text
    assert r.json()["detail"]["error"]["type"] == "gateway_timeout"


def test_edits_requires_prompt(servers):
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (1, 2, 3)).save(buf, "PNG")
    r = requests.post(
        f"{servers['gw']}/v1/images/edits",
        files={"image": ("a.png", buf.getvalue(), "image/png")},
        data={"prompt": "   ", "model": "anime-v1"},
        headers=AUTH,
        timeout=60,
    )
    assert r.status_code == 400


def test_edits_rejects_empty_image(servers):
    r = requests.post(
        f"{servers['gw']}/v1/images/edits",
        files={"image": ("a.png", b"", "image/png")},
        data={"prompt": "x"},
        headers=AUTH,
        timeout=60,
    )
    assert r.status_code == 400


def test_concurrent_requests_are_isolated(servers):
    """32 并发：全部成功，且每张图的 art id 唯一（无串号）。"""
    def call(i: int) -> tuple[int, str]:
        r = _gen(servers["gw"], prompt=f"concurrent prompt #{i}")
        assert r.status_code == 200, r.text
        return i, r.json()["data"][0]["url"]

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(call, range(32)))

    urls = [u for _, u in results]
    ids = {u.rsplit("/", 1)[-1] for u in urls}
    assert len(urls) == 32
    assert len(ids) == 32, f"art id 冲突: {len(ids)} unique / 32"


def test_health_reports_limits(servers):
    body = requests.get(f"{servers['gw']}/health", timeout=10).json()
    assert body["max_concurrency"] >= 1
    assert body["mode"] == "mock"
