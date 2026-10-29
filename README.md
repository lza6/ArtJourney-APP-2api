# ArtJourney 2API（v3.2.4 逆向 · 网关 + mock + 压测）

把 `newway.ai.art.image.picture.photo.generator.avatar.maker`（ArtJourney v3.2.4）的私有 HTTP 协议
封装成 **OpenAI 兼容图像接口**，供 new-api 直接对接。

> ⚠️ **当前状态：上游已停运（2026-11 实测）**
> `imageai.sboomtools.net` 全路径 502、Play 商店已下架、提示词端点 RunPod pod 已回收。
> 协议已 100% 还原并能用代码复现，但**暂时无法产出真实图片**。
> 默认 `ARTJOURNEY_MODE=mock`（本地 mock 上游），上游恢复后改一行即可转 real。
> 完整证据见 [`docs/逆向报告.md`](docs/逆向报告.md)。

---

## 协议速览

| 项 | 值 |
|---|---|
| 基址 | `http://imageai.sboomtools.net` |
| 鉴权 | **无账号体系**，仅 HTTP header `signature` = HMAC-SHA256(canonical, key) |
| 密钥 | Firebase RTDB `https://imageai-d865e-default-rtdb.firebaseio.com/key_hash_hmac/key.json`（匿名可读） |
| 生成 | `POST /api/v5/art`（multipart：`prompt`/`ratio`/`style`/`seed_id`/`original_image`/`steps`/`cfg_scale`/`negative_prompt`） |
| 轮询 | `POST /api/art/{id}/fetch`，`art.status==1` 且 `art.image` 非空即完成 |
| 图生图 | `POST /api/sketch`（同字段 + 必填 `original_image`） |
| 样式表 | `GET /api/v3/home?page=1` → `styles[]`（`model` 即 `style` 参数） |

签名串（**注意尾部 `&` 会被去掉**，可选参数为 `null` 时整段省略）：

```text
prompt=P&ratio=1:1&style=S[&seed_id=..][&steps=..][&cfg_scale=..][&negative_prompt=..]
```

---

## 快速开始

```powershell
# 依赖：fastapi uvicorn httpx pillow pytest（本机已具备）

# 1) 启动 mock 上游（模拟真实生成耗时 6s）
$env:MOCK_DELAY="6"; $env:MOCK_POLLS="1"
python -m uvicorn app.mock_upstream:app --port 8911

# 2) 启动 2API 网关（另开一个终端）
$env:GATEWAY_API_KEY="sk-artjourney-demo"; $env:ARTJOURNEY_MODE="mock"
$env:ARTJOURNEY_MOCK_URL="http://127.0.0.1:8911"
python -m uvicorn app.gateway:app --port 8912
```

- Demo 页：<http://127.0.0.1:8912/> （截图见 `docs/demo_page.png`）
- 健康检查：`GET /health`
- 模型列表：`GET /v1/models`（Bearer 鉴权）

### 调用示例

```bash
curl -X POST http://127.0.0.1:8912/v1/images/generations \
  -H "Authorization: Bearer sk-artjourney-demo" -H "Content-Type: application/json" \
  -d '{"prompt":"a cat astronaut, neon nebula","model":"anime-v1","size":"1024x1536"}'
# {"created":...,"data":[{"revised_prompt":"...","url":"http://.../static/3.png","seed":0}]}

# 图生图（走上游 /api/sketch）
curl -X POST http://127.0.0.1:8912/v1/images/edits \
  -H "Authorization: Bearer sk-artjourney-demo" \
  -F "image=@input.png" -F "prompt=turn into watercolor" -F "model=anime-v1" -F "size=1024x1024"
```

### 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `ARTJOURNEY_MODE` | `mock` | `mock` / `real` |
| `ARTJOURNEY_BASE_URL` | `http://imageai.sboomtools.net` | real 模式上游 |
| `ARTJOURNEY_MOCK_URL` | `http://127.0.0.1:8911` | mock 模式上游 |
| `ARTJOURNEY_HMAC_KEY` | 内置已还原密钥 | 上游轮换后覆盖 |
| `ARTJOURNEY_POLL_INTERVAL` | `1.0` | 轮询间隔（秒） |
| `ARTJOURNEY_POLL_TIMEOUT` | `180` | 单任务轮询超时（秒） |
| `GATEWAY_API_KEY` | `sk-artjourney-demo` | 网关 Bearer key |
| `GATEWAY_MAX_CONCURRENCY` | `16` | 网关→上游并发闸门（压测证明可开到 32） |
| `MOCK_DELAY` / `MOCK_POLLS` | `0.8` / `1` | mock 生成耗时 / 首次完成所需轮询次数 |

---

## 测试与压测（都是实跑结果）

```powershell
python -m pytest tests/ -q          # 49 passed
python bench/bench.py --concurrency 1,4,8,16 --requests 8
```

测试覆盖：签名边界（空值/8KB 超长/中英混排/emoji/含 `&` `=` 换行制表/特殊字符）、参数边界
（`n` 越界与非法值、未知 model 透传、model 别名、size 兜底）、上游故障注入
（HTTP500 / 非 JSON HTML / 内容过滤 / 图片 404 / 轮询超时）、并发隔离（32 并发 32 个独立 art id）、
错误语义映射（400/401/502/504）。

### 压测 A：模拟真实生成（mock 6s，2026-11 实测）

### 压测 A：模拟真实生成（mock 6s）

| 并发 | 请求 | 成功 | p50 | p90 | p99 | RPS |
|---|---|---|---|---|---|---|
| 1 | 8 | 8/8 | 6057 ms | 6072 ms | 6633 ms | 0.16 |
| 4 | 8 | 8/8 | 6086 ms | 6087 ms | 6088 ms | 0.66 |
| 8 | 8 | 8/8 | 6178 ms | 6183 ms | 6185 ms | 1.29 |
| 16 | 8 | 8/8 | 6200 ms | 6205 ms | 6206 ms | 1.29 |

结论：网关自身开销 ≈ 60–200 ms（6.05s 中约 6.0s 为 mock 模拟的生成耗时），32/32 零失败。

### 压测 B：网关门禁吞吐（mock 50 ms，64 请求/档）

| 并发 | 成功 | p50 | p90 | p99 | RPS |
|---|---|---|---|---|---|
| 4 | 64/64 | 61.6 ms | 62.8 ms | 617.3 ms | 41.4 |
| 16 | 64/64 | 73.0 ms | 103.5 ms | 116.4 ms | **183.9** |
| 32 | 64/64 | 109.8 ms | 158.0 ms | 195.9 ms | **243.5** |

结论：单进程网关（`GATEWAY_MAX_CONCURRENCY=32`）在 32 并发下 243 rps、192/192 全绿 ——
瓶颈是上游生成时长，不是网关；并发闸门保证不会把上游打爆。

---

## 目录

```
app/sign.py           签名算法（含 RTDB 密钥拉取）
app/client.py         7 个上游端点的异步客户端 + 轮询封装
app/gateway.py        OpenAI 兼容网关 + demo 页
app/mock_upstream.py  上游 mock（严格 schema + 真实校验签名）
tests/Sign.java       Kotlin 语义的 Java 复刻（与 Python 交叉校验）
bench/bench.py        并发压测
docs/逆向报告.md      完整逆向过程与存活判定证据
docs/NEWAPI_INTEGRATION.md  new-api 对接指引
```

---

## 免责与脱敏说明

- 本项目仅用于**协议研究与互操作性验证**（安全研究用途）。目标 App 已于 2026 年停运、Play 商店下架，
  仓库内不包含 APK 本体、用户数据或任何可用凭证。
- 已脱敏：Firebase API Key、AppsFlyer Dev Key、AdMob 广告位 ID（文档中替换为 `<REDACTED_*>`）。
- 保留：上游 HMAC 签名密钥 —— 它由目标应用自身通过**匿名可读的 Firebase RTDB 节点**下发
  （任何人都能 `GET` 到，等价于公开常量），且是复现协议与运行测试的必需项；脚本默认会实时从该节点拉取。
- 上游恢复后请自行评估合规性再用于生产。作者不对第三方的滥用承担责任。
