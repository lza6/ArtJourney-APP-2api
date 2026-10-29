"""ArtJourney 上游客户端 —— 完整还原 PhotoNetwork 的 7 个接口。

接口清单（静态还原自 com.example.photoapp.manager.network.PhotoNetwork）:
  POST api/v5/art          multipart: prompt, ratio, style, seed_id, [original_image], steps, cfg_scale, negative_prompt  header: signature
  POST api/sketch          multipart: 同上（图生图 / 草图生图）
  POST api/art/{id}/fetch  轮询生成结果
  GET  api/v3/home?page=N  首页数据: styles[] + popular_arts
  GET  api/blogs?page=N    博客列表
  GET  api/blog/{id}       博客详情
  POST {url_gen}           (远端配置 Firebase Remote Config `url_gen`) 图生文/提示词生成, multipart: file + prompt_mode/output_mode/max_filename_len
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx

from . import sign as signing

DEFAULT_BASE_URL = "http://imageai.sboomtools.net"
# 2026-04 版 Remote Config 中的 url_gen（RunPod 代理，pod 已下线）
REMOTE_URL_GEN = "https://f5rxra5ovrr87c-5001.proxy.runpod.net/generate-prompts"


@dataclass
class ArtResult:
    id: int
    status: int
    image: str
    prompt: str = ""
    style: str = ""
    ratio: str = ""
    seed_id: int = 0
    count_down: float = 0.0
    have_banned_word: int = 0
    raw: dict[str, Any] | None = None


class UpstreamError(RuntimeError):
    def __init__(self, status_code: int, body: str):
        super().__init__(f"upstream HTTP {status_code}: {body[:400]}")
        self.status_code = status_code
        self.body = body


class ContentFiltered(RuntimeError):
    """上游命中内容过滤（art.haveBannedWord == 1）。"""


class ArtJourneyClient:
    logger = None  # 由调用方注入 callable(msg: str)

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        hmac_key: str | None = None,
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.hmac_key = hmac_key
        self._timeout = timeout
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self._timeout,
                transport=self._transport,
                headers={"User-Agent": "okhttp/4.12.0"},
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "ArtJourneyClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    def _log(self, msg: str) -> None:
        if self.logger:
            self.logger(msg)

    def build_signature(
        self,
        prompt: str,
        ratio: str,
        style: str,
        seed_id: str | int | None = None,
        steps: str | int | None = None,
        cfg_scale: str | int | float | None = None,
        negative_prompt: str | None = None,
    ) -> str:
        return signing.sign(
            prompt, ratio, style, seed_id, steps, cfg_scale, negative_prompt, key=self.hmac_key
        )

    async def _post_multipart(
        self,
        path: str,
        *,
        prompt: str,
        ratio: str,
        style: str,
        seed_id: str | int | None = None,
        steps: str | int | None = None,
        cfg_scale: str | int | float | None = None,
        negative_prompt: str | None = None,
        image: tuple[str, bytes, str] | None = None,
    ) -> dict[str, Any]:
        signature = self.build_signature(prompt, ratio, style, seed_id, steps, cfg_scale, negative_prompt)
        data: dict[str, str] = {"prompt": prompt, "ratio": ratio, "style": style}
        if seed_id is not None:
            data["seed_id"] = str(seed_id)
        if steps is not None:
            data["steps"] = str(steps)
        if cfg_scale is not None:
            data["cfg_scale"] = str(cfg_scale)
        if negative_prompt is not None:
            data["negative_prompt"] = negative_prompt
        files = {"original_image": image} if image else None
        self._log(f"POST {path} style={style} ratio={ratio} sig={signature[:12]}…")
        resp = await self.client.post(path, data=data, files=files, headers={"signature": signature})
        if resp.status_code >= 400:
            raise UpstreamError(resp.status_code, resp.text)
        try:
            return resp.json()
        except ValueError as exc:  # 上游返回非 JSON（HTML 错误页等）
            raise UpstreamError(resp.status_code, resp.text) from exc

    async def create_art(
        self,
        prompt: str,
        *,
        ratio: str = "1:1",
        style: str = "",
        seed_id: str | int | None = None,
        image: tuple[str, bytes, str] | None = None,
        steps: str | int | None = None,
        cfg_scale: str | int | float | None = None,
        negative_prompt: str | None = None,
    ) -> dict[str, Any]:
        """文生图 / 图生图（同一个端点，带 original_image 即为图生图）。"""
        return await self._post_multipart(
            "/api/v5/art",
            prompt=prompt,
            ratio=ratio,
            style=style,
            seed_id=seed_id,
            steps=steps,
            cfg_scale=cfg_scale,
            negative_prompt=negative_prompt,
            image=image,
        )

    async def create_sketch(
        self,
        prompt: str,
        *,
        ratio: str = "1:1",
        style: str = "",
        image: tuple[str, bytes, str] | None = None,
        seed_id: str | int | None = None,
        steps: str | int | None = None,
        cfg_scale: str | int | float | None = None,
        negative_prompt: str | None = None,
    ) -> dict[str, Any]:
        """sketch-to-image（草图上色）。"""
        return await self._post_multipart(
            "/api/sketch",
            prompt=prompt,
            ratio=ratio,
            style=style,
            seed_id=seed_id,
            steps=steps,
            cfg_scale=cfg_scale,
            negative_prompt=negative_prompt,
            image=image,
        )

    async def fetch_art(self, art_id: str | int) -> dict[str, Any]:
        resp = await self.client.post(f"/api/art/{art_id}/fetch")
        if resp.status_code >= 400:
            raise UpstreamError(resp.status_code, resp.text)
        return resp.json()

    async def home(self, page: int = 1) -> dict[str, Any]:
        resp = await self.client.get("/api/v3/home", params={"page": page})
        if resp.status_code >= 400:
            raise UpstreamError(resp.status_code, resp.text)
        return resp.json()

    async def blogs(self, page: int = 1) -> dict[str, Any]:
        resp = await self.client.get("/api/blogs", params={"page": page})
        if resp.status_code >= 400:
            raise UpstreamError(resp.status_code, resp.text)
        return resp.json()

    async def blog(self, blog_id: str | int) -> dict[str, Any]:
        resp = await self.client.get(f"/api/blog/{blog_id}")
        if resp.status_code >= 400:
            raise UpstreamError(resp.status_code, resp.text)
        return resp.json()

    async def image_to_text(
        self,
        image: tuple[str, bytes, str],
        *,
        url: str = REMOTE_URL_GEN,
        prompt_mode: str = "1",
        output_mode: str = "1",
        max_filename_len: str = "255",
    ) -> dict[str, Any]:
        """图生文：注意这是绝对 URL（来自远端配置），不走 base_url。"""
        files = {"file": image}
        data = {
            "prompt_mode": prompt_mode,
            "output_mode": output_mode,
            "max_filename_len": max_filename_len,
        }
        # 该端点不带 signature 头（原始接口只有一个 @Header("signature")，用在前两个端点）
        resp = await self.client.post(url, data=data, files=files)
        if resp.status_code >= 400:
            raise UpstreamError(resp.status_code, resp.text)
        return resp.json()

    async def wait_for_art(
        self,
        art_id: str | int,
        initial: dict[str, Any] | None = None,
        *,
        poll_interval: float = 1.0,
        poll_timeout: float = 180.0,
    ) -> ArtResult:
        """轮询到出图（art.status == 1 且 art.image 非空）。"""
        latest = initial or {}
        deadline = asyncio.get_event_loop().time() + poll_timeout
        while True:
            if int(latest.get("status") or 0) == 1 and latest.get("image"):
                return self._to_result(latest)
            if int(latest.get("haveBannedWord") or 0) == 1:
                raise ContentFiltered("prompt rejected by upstream content filter")
            if asyncio.get_event_loop().time() > deadline:
                raise TimeoutError(f"polling timeout after {poll_timeout}s (art {art_id})")
            await asyncio.sleep(poll_interval)
            polled = await self.fetch_art(art_id)
            latest = polled.get("art") or latest

    async def generate(
        self,
        prompt: str,
        *,
        ratio: str = "1:1",
        style: str = "",
        image: tuple[str, bytes, str] | None = None,
        negative_prompt: str | None = None,
        steps: str | int | None = None,
        cfg_scale: str | int | float | None = None,
        seed_id: str | int | None = None,
        poll_interval: float = 1.0,
        poll_timeout: float = 180.0,
    ) -> ArtResult:
        """创建任务 + 轮询到出图。"""
        created = await self.create_art(
            prompt,
            ratio=ratio,
            style=style,
            image=image,
            negative_prompt=negative_prompt,
            steps=steps,
            cfg_scale=cfg_scale,
            seed_id=seed_id,
        )
        art = created.get("art") or {}
        art_id = art.get("id")
        if art_id is None:
            raise UpstreamError(200, f"no art id in response: {created}")
        return await self.wait_for_art(
            art_id, art, poll_interval=poll_interval, poll_timeout=poll_timeout
        )

    @staticmethod
    def _to_result(art: dict[str, Any]) -> ArtResult:
        return ArtResult(
            id=int(art.get("id") or 0),
            status=int(art.get("status") or 0),
            image=art.get("image") or art.get("temp_image") or "",
            prompt=art.get("prompt") or "",
            style=art.get("style") or "",
            ratio=art.get("ratio") or "",
            seed_id=int(art.get("seed_id") or 0),
            count_down=float(art.get("count_down") or 0.0),
            have_banned_word=int(art.get("haveBannedWord") or 0),
            raw=art,
        )
