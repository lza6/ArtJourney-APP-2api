"""ArtJourney 2API 网关（OpenAI 兼容），供 new-api 渠道直接对接。

端点:
  GET  /health
  GET  /v1/models                     -> 由上游 api/v3/home 的 styles 映射
  POST /v1/images/generations         -> api/v5/art
  POST /v1/images/edits  (multipart)  -> api/sketch
  POST /admin/refresh-styles          -> 重新拉取 styles
  GET  /v1/styles                     -> 原始 styles 列表

鉴权: Authorization: Bearer $GATEWAY_API_KEY

模式:
  ARTJOURNEY_MODE=mock  -> 上游指向本地 mock（默认，因为真实上游 2026-11 起 502）
  ARTJOURNEY_MODE=real  -> 直连 http://imageai.sboomtools.net
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from pathlib import Path

import httpx
from fastapi import Body, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from . import mock_upstream  # noqa: F401  仅用于拿到 mock 的默认样式表
from .client import ArtJourneyClient, ContentFiltered, UpstreamError

GATEWAY_API_KEY = os.environ.get("GATEWAY_API_KEY", "sk-artjourney-demo")
MODE = os.environ.get("ARTJOURNEY_MODE", "mock").lower()
REAL_BASE_URL = os.environ.get("ARTJOURNEY_BASE_URL", "http://imageai.sboomtools.net")
MOCK_BASE_URL = os.environ.get("ARTJOURNEY_MOCK_URL", "http://127.0.0.1:8911")
BASE_URL = MOCK_BASE_URL if MODE == "mock" else REAL_BASE_URL
POLL_INTERVAL = float(os.environ.get("ARTJOURNEY_POLL_INTERVAL", "1.0"))
POLL_TIMEOUT = float(os.environ.get("ARTJOURNEY_POLL_TIMEOUT", "180"))
MAX_CONCURRENCY = max(1, int(os.environ.get("GATEWAY_MAX_CONCURRENCY", "16")))
_semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

DATA_DIR = Path(os.environ.get("ARTJOURNEY_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
STYLES_FILE = DATA_DIR / "styles.json"

app = FastAPI(title="ArtJourney 2API", version="1.0.0")
client = ArtJourneyClient(base_url=BASE_URL, hmac_key=os.environ.get("ARTJOURNEY_HMAC_KEY"))

_styles: list[dict] = []
_styles_meta = {"source": "unloaded", "fetched_at": 0}


SIZE_TO_RATIO = {
    "512x512": "1:1",
    "768x768": "1:1",
    "1024x1024": "1:1",
    "1024x1536": "2:3",
    "768x1152": "2:3",
    "1536x1024": "3:2",
    "1152x768": "3:2",
}
RATIO_TO_SIZE = {"1:1": "1024x1024", "2:3": "1024x1536", "3:2": "1536x1024"}

DEMO_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>ArtJourney 2API demo</title>
<style>
 :root{color-scheme:dark}
 body{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;background:#0f1115;color:#e8eaed;margin:0;padding:32px}
 .wrap{max-width:900px;margin:0 auto}
 h1{font-size:20px;margin:0 0 4px} .sub{color:#8b93a1;font-size:13px;margin-bottom:20px}
 .card{background:#171a21;border:1px solid #242a35;border-radius:12px;padding:16px;margin-bottom:16px}
 label{display:block;font-size:12px;color:#9aa4b2;margin:10px 0 4px}
 input,select,textarea{width:100%;box-sizing:border-box;background:#0f1319;border:1px solid #2a3240;border-radius:8px;color:#e8eaed;padding:9px 10px;font-size:14px;font-family:inherit}
 textarea{min-height:70px;resize:vertical}
 button{margin-top:14px;background:#2f6df6;border:0;color:#fff;padding:10px 18px;border-radius:8px;font-size:14px;cursor:pointer}
 button:disabled{opacity:.5;cursor:default}
 .row{display:grid;grid-template-columns:1fr 1fr;gap:12px}
 #out{margin-top:16px;font-size:13px;white-space:pre-wrap;color:#9aa4b2}
 #img{margin-top:16px;max-width:100%;border-radius:12px;display:none}
 .kv{font-size:12px;color:#6f7987;margin-top:8px}
</style></head><body><div class="wrap">
<h1>ArtJourney 2API · demo</h1>
<div class="sub">OpenAI 兼容网关 · 上游协议: POST /api/v5/art + /api/art/{id}/fetch · 签名 HMAC-SHA256</div>
<div class="card">
  <label>Gateway API Key</label>
  <input id="key" value="sk-artjourney-demo">
  <label>Prompt</label>
  <textarea id="prompt">a cat astronaut floating in a neon nebula, cinematic lighting</textarea>
  <div class="row">
    <div><label>Model (style)</label><select id="model"></select></div>
    <div><label>Size</label><select id="size">
      <option>1024x1024</option><option>1024x1536</option><option>1536x1024</option>
    </select></div>
  </div>
  <label>Negative prompt (可选)</label>
  <input id="neg" placeholder="blurry, low quality">
  <button id="go">生成</button>
  <div id="out"></div>
  <img id="img" alt="generated">
  <div class="kv" id="meta"></div>
</div>
<script>
const $ = s => document.querySelector(s);
async function loadModels(){
  const r = await fetch('/v1/models',{headers:{Authorization:'Bearer '+$('#key').value}});
  if(!r.ok){ $('#out').textContent='models 拉取失败: HTTP '+r.status; return; }
  const data = (await r.json()).data;
  $('#model').innerHTML = data.map(m=>`<option value="${m.id}">${m.name||m.id}${m.is_vip?' (VIP)':''}</option>`).join('');
}
$('#key').addEventListener('change', loadModels);
$('#go').addEventListener('click', async ()=>{
  const t0 = performance.now();
  $('#go').disabled = true; $('#out').textContent = '生成中…'; $('#img').style.display='none'; $('#meta').textContent='';
  try{
    const body = {prompt:$('#prompt').value, model:$('#model').value, size:$('#size').value, n:1};
    if($('#neg').value.trim()) body.negative_prompt = $('#neg').value.trim();
    const r = await fetch('/v1/images/generations',{method:'POST',
      headers:{'Content-Type':'application/json', Authorization:'Bearer '+$('#key').value},
      body: JSON.stringify(body)});
    const j = await r.json();
    if(!r.ok){ $('#out').textContent = 'HTTP '+r.status+'\\n'+JSON.stringify(j,null,2); return; }
    const item = j.data[0];
    $('#img').src = item.url; $('#img').style.display='block';
    $('#meta').textContent = '耗时 '+Math.round(performance.now()-t0)+'ms · seed '+item.seed;
    $('#out').textContent = item.url;
  }catch(e){ $('#out').textContent = 'ERR '+e; }
  finally{ $('#go').disabled = false; }
});
loadModels();
</script></div></body></html>"""


def _auth(authorization: str | None) -> None:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail={"error": {"message": "missing bearer token", "type": "auth_error"}})
    token = authorization.split(" ", 1)[1].strip()
    if token != GATEWAY_API_KEY:
        raise HTTPException(status_code=401, detail={"error": {"message": "invalid api key", "type": "auth_error"}})


def _size_to_ratio(size: str | None) -> str:
    if not size:
        return "1:1"
    size = size.lower().replace(" ", "")
    if size in SIZE_TO_RATIO:
        return SIZE_TO_RATIO[size]
    if ":" in size:  # 直接传 "2:3"
        return size
    if "x" in size:
        try:
            w, h = (int(v) for v in size.split("x", 1))
        except ValueError:
            return "1:1"
        if w == h:
            return "1:1"
        return "2:3" if h > w else "3:2"
    return "1:1"


def _resolve_style(model: str | None) -> str:
    if not model or model in {"artjourney", "default", "artjourney-v3.2.4"}:
        if not _styles:
            raise HTTPException(status_code=503, detail={"error": {"message": "style list empty; call /admin/refresh-styles", "type": "upstream_error"}})
        return _styles[0]["model"]
    for style in _styles:
        if style.get("model") == model or style.get("name", "").lower() == model.lower():
            return style["model"]
    # 允许直接透传未知 model（上游可能新增样式）
    return model


def _clamp_n(value: object) -> int:
    try:
        return max(1, min(int(value or 1), 4))
    except (TypeError, ValueError):
        return 1


def _error(status: int, message: str, err_type: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": {"message": message, "type": err_type}})


def _map_upstream_error(exc: Exception) -> HTTPException:
    """把上游异常翻译成 OpenAI 风格的 HTTP 错误。"""
    if isinstance(exc, ContentFiltered):
        return _error(400, str(exc), "content_filter")
    if isinstance(exc, TimeoutError):
        return _error(504, str(exc), "gateway_timeout")
    if isinstance(exc, UpstreamError):
        return _error(502, str(exc), "upstream_error")
    if isinstance(exc, httpx.HTTPError):
        return _error(502, f"upstream unreachable: {exc}", "upstream_unreachable")
    return _error(500, f"{type(exc).__name__}: {exc}", "internal_error")


async def load_styles(force: bool = False) -> dict:
    global _styles, _styles_meta
    if _styles and not force:
        return _styles_meta
    if STYLES_FILE.exists():
        try:
            payload = json.loads(STYLES_FILE.read_text("utf-8"))
            _styles = payload.get("styles") or []
            _styles_meta = {"source": payload.get("source", "cache"), "fetched_at": payload.get("fetched_at", 0)}
        except Exception:
            _styles = []
    if MODE == "real" or force:
        try:
            data = await client.home(1)
            styles = data.get("styles") or []
            if styles:
                _styles = styles
                _styles_meta = {"source": "upstream", "fetched_at": int(time.time())}
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                STYLES_FILE.write_text(
                    json.dumps({"source": "upstream", "fetched_at": _styles_meta["fetched_at"], "styles": styles}, ensure_ascii=False, indent=2),
                    "utf-8",
                )
        except Exception as exc:
            if not _styles:
                _styles = mock_upstream.STYLES
                _styles_meta = {"source": "fallback-mock", "fetched_at": int(time.time()), "error": str(exc)[:200]}
    if not _styles:
        _styles = mock_upstream.STYLES
        _styles_meta = {"source": "fallback-mock", "fetched_at": int(time.time())}
    return _styles_meta


@app.on_event("startup")
async def on_startup() -> None:
    client.logger = lambda msg: print(f"[upstream] {msg}", flush=True)
    await load_styles()


@app.on_event("shutdown")
async def on_shutdown() -> None:
    await client.aclose()


@app.get("/")
async def demo_page():
    """最小可视 demo 页：填 key + prompt 直接出图。"""
    from fastapi.responses import HTMLResponse

    return HTMLResponse(DEMO_HTML)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "mode": MODE,
        "upstream": BASE_URL,
        "styles": len(_styles),
        "max_concurrency": MAX_CONCURRENCY,
        "styles_meta": _styles_meta,
    }


@app.get("/v1/models")
async def models(authorization: str | None = Header(None)):
    _auth(authorization)
    await load_styles()
    now = int(time.time())
    return {
        "object": "list",
        "data": [
            {
                "id": s["model"],
                "object": "model",
                "created": now,
                "owned_by": "artjourney",
                "name": s.get("name", s["model"]),
                "is_vip": s.get("is_vip", 0),
            }
            for s in _styles
        ],
    }


@app.get("/v1/styles")
async def styles(authorization: str | None = Header(None)):
    _auth(authorization)
    await load_styles()
    return {"object": "list", "data": _styles}


@app.post("/admin/refresh-styles")
async def refresh_styles():
    meta = await load_styles(force=True)
    return {"ok": True, "count": len(_styles), "meta": meta}


@app.post("/v1/images/generations")
async def generations(payload: dict = Body(...), authorization: str | None = Header(None)):
    _auth(authorization)
    await load_styles()
    prompt = (payload.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail={"error": {"message": "prompt is required", "type": "invalid_request_error"}})
    ratio = _size_to_ratio(payload.get("size"))
    style = _resolve_style(payload.get("model"))
    n = _clamp_n(payload.get("n"))
    response_format = (payload.get("response_format") or "url").lower()
    negative_prompt = payload.get("negative_prompt")
    steps = payload.get("steps")
    cfg_scale = payload.get("cfg_scale")
    seed = payload.get("seed")

    images = []
    for _ in range(n):
        try:
            async with _semaphore:  # 保护上游的并发上限
                result = await client.generate(
                    prompt,
                    ratio=ratio,
                    style=style,
                    negative_prompt=negative_prompt,
                    steps=steps,
                    cfg_scale=cfg_scale,
                    seed_id=seed,
                    poll_interval=POLL_INTERVAL,
                    poll_timeout=POLL_TIMEOUT,
                )
        except Exception as exc:  # noqa: BLE001 - 统一翻译为 OpenAI 风格错误
            raise _map_upstream_error(exc) from exc

        item = {"revised_prompt": result.prompt or prompt}
        if response_format == "b64_json":
            try:
                async with httpx.AsyncClient(timeout=60) as raw:
                    img = await raw.get(result.image)
                    img.raise_for_status()
                    item["b64_json"] = base64.b64encode(img.content).decode()
            except httpx.HTTPError as exc:
                raise _error(502, f"image download failed: {exc}", "upstream_error") from exc
        else:
            item["url"] = result.image
        item["seed"] = result.seed_id
        images.append(item)

    return JSONResponse({"created": int(time.time()), "data": images})


@app.post("/v1/images/edits")
async def edits(
    image: UploadFile = File(...),
    prompt: str = Form(""),
    model: str = Form(""),
    size: str = Form("1024x1024"),
    n: int = Form(1),
    response_format: str = Form("url"),
    negative_prompt: str | None = Form(None),
    steps: str | None = Form(None),
    cfg_scale: str | None = Form(None),
    authorization: str | None = Header(None),
):
    _auth(authorization)
    await load_styles()
    prompt = (prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail={"error": {"message": "prompt is required", "type": "invalid_request_error"}})
    raw = await image.read()
    if not raw:
        raise HTTPException(status_code=400, detail={"error": {"message": "empty image", "type": "invalid_request_error"}})
    ratio = _size_to_ratio(size)
    style = _resolve_style(model)
    payload = ("original_image", image.filename or "upload.png", image.content_type or "image/png")

    images = []
    for _ in range(_clamp_n(n)):
        try:
            async with _semaphore:
                created = await client.create_sketch(
                    prompt,
                    ratio=ratio,
                    style=style,
                    image=(payload[0], raw, payload[2]),
                    negative_prompt=negative_prompt,
                    steps=steps,
                    cfg_scale=cfg_scale,
                )
                art = created.get("art") or {}
                art_id = art.get("id")
                if art_id is None:
                    raise UpstreamError(200, f"no art id: {created}")
                result = await client.wait_for_art(art_id, art, poll_interval=POLL_INTERVAL, poll_timeout=POLL_TIMEOUT)
        except Exception as exc:  # noqa: BLE001 - 统一翻译为 OpenAI 风格错误
            raise _map_upstream_error(exc) from exc

        item = {"revised_prompt": result.prompt or prompt}
        if response_format.lower() == "b64_json":
            try:
                async with httpx.AsyncClient(timeout=60) as raw_client:
                    img = await raw_client.get(result.image)
                    img.raise_for_status()
                    item["b64_json"] = base64.b64encode(img.content).decode()
            except httpx.HTTPError as exc:
                raise _error(502, f"image download failed: {exc}", "upstream_error") from exc
        else:
            item["url"] = result.image
        item["seed"] = result.seed_id
        images.append(item)

    return JSONResponse({"created": int(time.time()), "data": images})
