"""上游 mock —— 严格按静态还原的响应结构实现，用于本地 demo / 压测。

真实上游 imageai.sboomtools.net 在 2026-11 已 502（nginx upstream down, Play 已下架），
所以本轮 demo 与压测跑在 mock 上；协议一旦恢复，只需把 ARTJOURNEY_BASE_URL 指回真实域名。

响应结构依据:
  DataArt    { code:int, status:String, art: Art }
  Art        { id, status, image, temp_image, prompt, ratio, style, styleName, styleImage,
               seed_id, count_down, haveBannedWord }
  Data       { code:int, status:String, styles:[Style], popular_arts:{items:[Prompt], pagination:{...}} }
  Style      { name, model, prompt, image, description, is_vip }
  TextGenerated { prompt }
"""

from __future__ import annotations

import asyncio
import io
import os
import time
import uuid

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from PIL import Image

from . import sign as signing

MOCK_DELAY = float(os.environ.get("MOCK_DELAY", "0.8"))  # 模拟生成耗时（秒）
MOCK_POLLS = int(os.environ.get("MOCK_POLLS", "1"))  # 首次 fetch 前需要轮询几次才 ready

STYLES = [
    {
        "name": "Anime",
        "model": "anime-v1",
        "prompt": "anime style, vibrant colors",
        "image": "https://cdn.example.invalid/style/anime.jpg",
        "description": "Japanese anime illustration",
        "is_vip": 0,
    },
    {
        "name": "Oil Painting",
        "model": "oil-v2",
        "prompt": "oil painting, thick brush strokes",
        "image": "https://cdn.example.invalid/style/oil.jpg",
        "description": "Classic oil painting texture",
        "is_vip": 0,
    },
    {
        "name": "3D Render",
        "model": "render3d-v1",
        "prompt": "3d render, octane, soft light",
        "image": "https://cdn.example.invalid/style/3d.jpg",
        "description": "Pixar-like 3D rendering",
        "is_vip": 1,
    },
    {
        "name": "Photoreal",
        "model": "photo-v3",
        "prompt": "photorealistic, 85mm lens",
        "image": "https://cdn.example.invalid/style/photo.jpg",
        "description": "Photorealistic portrait",
        "is_vip": 1,
    },
]

app = FastAPI(title="ArtJourney upstream mock", version="3.2.4-mock")
_tasks: dict[int, dict] = {}
_seq = {"id": 1}
_metrics = {"art": 0, "sketch": 0, "fetch": 0, "style_reject": 0, "sig_reject": 0}


def _verify(prompt: str, ratio: str, style: str, seed_id, steps, cfg_scale, negative_prompt, signature):
    if not signature:
        _metrics["sig_reject"] += 1
        raise HTTPException(status_code=401, detail="missing signature")
    expected = signing.sign(prompt, ratio, style, seed_id, steps, cfg_scale, negative_prompt)
    if signature != expected:
        _metrics["sig_reject"] += 1
        raise HTTPException(status_code=401, detail="invalid signature")


def _new_task(prompt: str, ratio: str, style: str, seed_id: int | None, base_url: str) -> dict:
    art_id = _seq["id"]
    _seq["id"] += 1
    task = {
        "id": art_id,
        "status": 0,
        "image": "",
        "temp_image": "",
        "prompt": prompt,
        "ratio": ratio,
        "style": style,
        "styleName": next((s["name"] for s in STYLES if s["model"] == style), style),
        "styleImage": "",
        "seed_id": seed_id or 0,
        "count_down": MOCK_DELAY,
        "haveBannedWord": 0,
        "ready_at": time.time() + MOCK_DELAY,
        "polls": 0,
        "_base_url": base_url,
    }
    sim = style if style.startswith("__sim_") else ""
    if sim == "__sim_banned__":
        task["haveBannedWord"] = 1
    elif sim == "__sim_slow__":
        task["ready_at"] = time.time() + 3600
    elif sim == "__sim_image404__":
        task["status"] = 1
        task["image"] = f"{base_url}/static/missing.png"
    _tasks[art_id] = task
    return task


def _public(task: dict) -> dict:
    return {k: v for k, v in task.items() if not k.startswith("_") and k != "ready_at" and k != "polls"}


@app.get("/health")
async def health():
    return {"status": "ok", "style": "mock", "metrics": _metrics}


@app.get("/api/v3/home")
async def home(page: int = 1):
    return {
        "code": 200,
        "status": "success",
        "styles": STYLES,
        "popular_arts": {
            "items": [
                {
                    "id": 1,
                    "image": "https://cdn.example.invalid/popular/1.jpg",
                    "prompt": "a cat astronaut",
                    "ratio": "1:1",
                    "style": "anime-v1",
                }
            ],
            "pagination": {"current_page": page, "per_page": 20, "total": 1, "total_pages": 1},
        },
    }


@app.get("/api/blogs")
async def blogs(page: int = 1):
    return {
        "code": 200,
        "status": "success",
        "editor_choices": [],
        "popular_blogs": {"items": [], "pagination": {"current_page": page, "per_page": 20, "total": 0, "total_pages": 0}},
    }


@app.get("/api/blog/{blog_id}")
async def blog(blog_id: int):
    return {"code": 200, "status": "success", "id": blog_id, "title": "mock", "content": ""}


@app.post("/api/v5/art")
async def create_art(
    prompt: str = Form(...),
    ratio: str = Form(...),
    style: str = Form(...),
    seed_id: str | None = Form(None),
    steps: str | None = Form(None),
    cfg_scale: str | None = Form(None),
    negative_prompt: str | None = Form(None),
    original_image: UploadFile | None = File(None),
    signature: str | None = Header(None),
    request_host: str | None = Header(None, alias="host"),
):
    _verify(prompt, ratio, style, seed_id, steps, cfg_scale, negative_prompt, signature)
    if style == "__sim_http500__":
        raise HTTPException(status_code=500, detail="simulated upstream failure")
    if style == "__sim_malformed__":
        return PlainTextResponse("<html><body>upstream broke</body></html>", status_code=200)
    if original_image is not None:
        await original_image.read()
    _metrics["art"] += 1
    task = _new_task(prompt, ratio, style, int(seed_id) if seed_id else None, f"http://{request_host}")
    return JSONResponse({"code": 200, "status": "processing", "art": _public(task)})


@app.post("/api/sketch")
async def create_sketch(
    prompt: str = Form(...),
    ratio: str = Form(...),
    style: str = Form(...),
    seed_id: str | None = Form(None),
    steps: str | None = Form(None),
    cfg_scale: str | None = Form(None),
    negative_prompt: str | None = Form(None),
    original_image: UploadFile | None = File(None),
    signature: str | None = Header(None),
    request_host: str | None = Header(None, alias="host"),
):
    _verify(prompt, ratio, style, seed_id, steps, cfg_scale, negative_prompt, signature)
    if original_image is None:
        raise HTTPException(status_code=422, detail="original_image required")
    await original_image.read()
    _metrics["sketch"] += 1
    task = _new_task(prompt, ratio, style, int(seed_id) if seed_id else None, f"http://{request_host}")
    return JSONResponse({"code": 200, "status": "processing", "art": _public(task)})


@app.post("/api/art/{art_id}/fetch")
async def fetch_art(art_id: int):
    _metrics["fetch"] += 1
    task = _tasks.get(art_id)
    if task is None:
        raise HTTPException(status_code=404, detail="art not found")
    task["polls"] += 1
    if time.time() >= task["ready_at"] and task["polls"] > MOCK_POLLS:
        task["status"] = 1
        if not task["image"]:
            task["image"] = f"{task['_base_url']}/static/{art_id}.png"
    return {"code": 200, "status": "success" if task["status"] == 1 else "processing", "art": _public(task)}


_RATIO_SIZE = {"1:1": (512, 512), "2:3": (512, 768), "3:2": (768, 512)}


@app.get("/static/{art_id}.png")
async def static_png(art_id: int):
    """按 art id + ratio 生成一张可辨识的 PNG（不联网，纯本地绘制）。"""
    task = _tasks.get(art_id) or {}
    width, height = _RATIO_SIZE.get(task.get("ratio", "1:1"), (512, 512))
    seed = abs(hash(str(art_id))) % (256 * 256 * 256)
    color = ((seed >> 16) & 0xFF, (seed >> 8) & 0xFF, seed & 0xFF)
    img = Image.new("RGB", (width, height), color)
    px = img.load()
    for y in range(height):
        for x in range(0, width, 4):
            px[x, y] = ((color[0] + x // 4) % 256, (color[1] + y // 4) % 256, color[2])
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


@app.post("/generate-prompts")
async def generate_prompts(
    file: UploadFile = File(...),
    prompt_mode: str = Form("1"),
    output_mode: str = Form("1"),
    max_filename_len: str = Form("255"),
):
    """对照 url_gen（原 RunPod /generate-prompts）的 mock。"""
    raw = await file.read()
    return {"prompt": f"a highly detailed artwork inspired by uploaded image ({len(raw)} bytes)"}


@app.get("/__metrics")
async def metrics():
    return _metrics


async def _warmup() -> None:
    await asyncio.sleep(0)


@app.on_event("startup")
async def on_startup() -> None:
    await _warmup()
    _ = uuid.uuid4()
