# new-api 对接指引（ArtJourney 2API）

> 网关已把上游封装成 **OpenAI 图像接口**，new-api 侧按 OpenAI 兼容渠道接入即可。
> ⚠️ 上游当前已停运（见 `docs/逆向报告.md`），此刻只能接 mock；上游恢复后把
> `ARTJOURNEY_MODE=real` 重启网关，new-api 侧配置**不用改**。

---

## 1. 渠道配置（new-api 管理后台）

| 字段 | 值 |
|---|---|
| 类型 | OpenAI（`type = 1`，走 `/v1/images/generations`、`/v1/images/edits`） |
| 名称 | `ArtJourney` |
| Base URL | `http://<网关主机>:8912`（不要带 `/v1`，网关自己处理） |
| 密钥 | 网关的 `GATEWAY_API_KEY`（默认 `sk-artjourney-demo`） |
| 模型 | 填上游 `styles[].model`：`anime-v1` / `oil-v2` / `render3d-v1` / `photo-v3` …（mock 为样例；real 后 `POST /admin/refresh-styles` 拉真实列表，再按 `GET /v1/models` 填） |
| 计费 | **按次**（每张图 1 次调用），上游无 token 概念 |
| 并发闸门 | 网关侧 `GATEWAY_MAX_CONCURRENCY`（默认 16，实测可开到 32 → 243 rps） |

模型名同时接受 `Style.name`（如 `Anime`），网关内部大小写不敏感匹配；未知 model 会原样透传
（上游新增样式时无需改网关）。

## 2. 能力矩阵

| 能力 | 网关端点 | 上游 | 状态 |
|---|---|---|---|
| 文生图 | `POST /v1/images/generations` | `api/v5/art` | ✅ 已验证（mock 全链路 + 浏览器 demo） |
| 图生图 / 编辑 | `POST /v1/images/edits`（multipart `image`） | `api/sketch` | ✅ 已验证（mock 全链路） |
| 尺寸 | `size` → `ratio`：`1:1`/`2:3`/`3:2` | `ratio` 字段 | ✅ 已验证（512x768 出图尺寸匹配 2:3） |
| 返回格式 | `response_format=url` / `b64_json` | — | ✅ 双路径都测过 |
| n>1 | `n` 上限 4 | 串行多次创建 | ✅ 代码路径已验证（单次为主） |
| 样式列表 | `GET /v1/models`、`GET /v1/styles` | `api/v3/home` | ✅ |
| 提示词反推（图生文） | — | `url_gen`（RunPod，已下线） | ❌ 上游端点已死，未封装 |

## 3. 错误语义（对接时最有用的部分）

| 场景 | 网关返回 | 说明 |
|---|---|---|
| 上游不可达 / 已停运 | `502` `upstream_unreachable` | ✅ dead-port 实测 |
| 上游 5xx / 返回非 JSON | `502` `upstream_error` | ✅ mock 故障注入实测 |
| 轮询超时（默认 180s） | `504` `gateway_timeout` | ✅ 实测（2s 超时配置）；建议 new-api 侧超时 ≥ 200s |
| 提示词命中内容过滤 | `400` `content_filter` | ✅ 实测（`haveBannedWord=1`） |
| 出图 URL 拉取失败 | `502` `upstream_error`（`image download failed`） | ✅ 实测 |
| 空 prompt | `400` `invalid_request_error` | 已校验 |
| 未带 key / key 错 | `401` `auth_error` | 已校验 |

## 4. 推荐部署形态

```
new-api  ──HTTP──>  artjourney-gateway (uvicorn, 单进程)
                        │  httpx + HMAC 签名
                        └─> imageai.sboomtools.net  (当前 502)
```

```powershell
# 网关常驻（Windows）
$env:GATEWAY_API_KEY="sk-xxxxxx"
$env:ARTJOURNEY_MODE="real"          # mock 阶段用 mock
python -m uvicorn app.gateway:app --host 0.0.0.0 --port 8912
```

上游恢复判定（三条都要绿）：

```bash
curl -sk -o /dev/null -w "%{http_code}\n" "https://imageai.sboomtools.net/api/v3/home?page=1"   # 期望 200
curl -s https://imageai-d865e-default-rtdb.firebaseio.com/key_hash_hmac/key.json                # 期望返回 key
curl -s -o /dev/null -w "%{http_code}\n" "https://play.google.com/store/apps/details?id=newway.ai.art.image.picture.photo.generator.avatar.maker"  # 期望 200
```

## 5. 待验证（未在真实 new-api 实例上跑）

- 本机无 new-api 生产实例，**渠道注册与计费链路未实测**（网关侧 OpenAI 兼容性已用 pytest + 浏览器验证）。
- new-api 对 `/v1/images/edits` 的 multipart 透传是否支持，需在实例上确认；若不支持，编辑能力先在网关上用
  `POST /v1/images/edits` 直连验证，再决定是否新增自定义渠道类型。
- 上游真实响应与 mock 的差异（尤其 `count_down`、`status` 取值）无法离线确认，上游恢复后需抓一次真实响应核对。
