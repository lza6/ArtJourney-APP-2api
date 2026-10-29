"""ArtJourney 请求签名复刻。

静态还原自 ArtJourney v3.2.4 (newway.ai.art.image.picture.photo.generator.avatar.maker)
  - 签名串拼装: w0.g.d(prompt, ratio, style, seed_id, steps, cfg_scale, negative_prompt)
  - 签名计算:   w0.g.c(message) -> HMAC-SHA256(hex, lower)
  - 密钥来源:   Firebase RTDB  key_hash_hmac/key  (n.f6149a)
  - 传输位置:   HTTP header `signature`  (PhotoNetwork.createArt @Header("signature"))

原始 Kotlin 语义要点（容易踩坑）:
  1. 每个参数都以 "&" 结尾拼接, 因此原始串一定以 "&" 结尾; 若不以 "&" 结尾则签名直接返回空串。
  2. 取 `b13.lastIndexOf("&", lastIndex)` -> 尾部那个 "&" 的下标, 然后拼接 前段 + "" + 后段,
     等价于 **去掉尾部的 "&"** 再计算 HMAC。
  3. 可选参数为 null 时整段不参与拼接（不是拼空值）。
"""

from __future__ import annotations

import hashlib
import hmac
import os

# 2026-04 版 APK 内 Firebase RTDB: https://imageai-d865e-default-rtdb.firebaseio.com/key_hash_hmac/key.json
# 匿名可读（无需 App Check / 无需登录），实测返回该值。
FALLBACK_HMAC_KEY = "d36cc0e575597ad822a76db7e4a89f3c"
RTDB_KEY_URL = os.environ.get(
    "ARTJOURNEY_RTDB_KEY_URL",
    "https://imageai-d865e-default-rtdb.firebaseio.com/key_hash_hmac/key.json",
)

_OPTIONAL_ORDER = ("seed_id", "steps", "cfg_scale", "negative_prompt")


def canonical_message(
    prompt: str,
    ratio: str,
    style: str,
    seed_id: str | int | None = None,
    steps: str | int | None = None,
    cfg_scale: str | int | float | None = None,
    negative_prompt: str | None = None,
) -> str:
    """按客户端逻辑拼出待签名串（已去掉尾部 "&"）。"""
    values = {
        "seed_id": seed_id,
        "steps": steps,
        "cfg_scale": cfg_scale,
        "negative_prompt": negative_prompt,
    }
    parts = [f"prompt={prompt}&", f"ratio={ratio}&", f"style={style}&"]
    for name in _OPTIONAL_ORDER:
        value = values[name]
        if value is None:
            continue
        parts.append(f"{name}={value}&")
    raw = "".join(parts)
    if not raw.endswith("&"):
        raise ValueError("canonical string must end with '&'")
    # lastIndexOf('&') == len-1, 拼接去掉该字符
    return raw[:-1]


def sign(
    prompt: str,
    ratio: str,
    style: str,
    seed_id: str | int | None = None,
    steps: str | int | None = None,
    cfg_scale: str | int | float | None = None,
    negative_prompt: str | None = None,
    key: str | None = None,
) -> str:
    """返回 header `signature` 的值（lowercase hex）。"""
    secret = key or os.environ.get("ARTJOURNEY_HMAC_KEY") or FALLBACK_HMAC_KEY
    msg = canonical_message(prompt, ratio, style, seed_id, steps, cfg_scale, negative_prompt)
    return hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()


def fetch_remote_key(timeout: float = 10.0) -> str | None:
    """从 Firebase RTDB 拉取当前轮换密钥（需要网络；失败返回 None）。"""
    try:
        import httpx

        resp = httpx.get(RTDB_KEY_URL, timeout=timeout)
        resp.raise_for_status()
        value = resp.json()
        return value if isinstance(value, str) and value else None
    except Exception:
        return None
