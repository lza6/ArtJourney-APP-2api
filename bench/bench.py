"""本地压测 —— 打 2API 网关（网关->上游 mock / 或真实上游）。

用法:
  python bench/bench.py --url http://127.0.0.1:8912 --key sk-artjourney-demo --concurrency 1,4,8,16 --requests 16
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path

import httpx


async def one_request(client: httpx.AsyncClient, url: str, key: str, payload: dict) -> tuple[bool, float, str]:
    start = time.perf_counter()
    try:
        r = await client.post(url, json=payload, headers={"Authorization": f"Bearer {key}"})
        elapsed = (time.perf_counter() - start) * 1000
        if r.status_code == 200 and (r.json().get("data") or [{}])[0].get("url"):
            return True, elapsed, ""
        return False, elapsed, f"HTTP {r.status_code}: {r.text[:160]}"
    except Exception as exc:  # noqa: BLE001 - 压测需要记录任意失败
        return False, (time.perf_counter() - start) * 1000, f"{type(exc).__name__}: {exc}"


async def run_level(base: str, key: str, concurrency: int, total: int) -> dict:
    url = f"{base}/v1/images/generations"
    payload = {"prompt": f"bench prompt {int(time.time()*1000)}", "model": "anime-v1", "size": "1024x1024", "n": 1}
    sem = asyncio.Semaphore(concurrency)
    results: list[tuple[bool, float, str]] = []

    async with httpx.AsyncClient(timeout=180) as client:
        async def worker(i: int) -> None:
            async with sem:
                body = dict(payload, prompt=f"{payload['prompt']} #{i}")
                results.append(await one_request(client, url, key, body))

        start = time.perf_counter()
        await asyncio.gather(*(worker(i) for i in range(total)))
        wall = time.perf_counter() - start

    latencies = sorted(r[1] for r in results)
    ok = sum(1 for r in results if r[0])
    errors = [r[2] for r in results if not r[0]]

    def pct(p: float) -> float:
        if not latencies:
            return 0.0
        idx = min(len(latencies) - 1, int(round((p / 100) * (len(latencies) - 1))))
        return round(latencies[idx], 1)

    return {
        "concurrency": concurrency,
        "requests": total,
        "ok": ok,
        "failed": total - ok,
        "success_rate": round(ok / total * 100, 2),
        "wall_s": round(wall, 2),
        "rps": round(total / wall, 2),
        "p50_ms": pct(50),
        "p90_ms": pct(90),
        "p99_ms": pct(99),
        "mean_ms": round(statistics.fmean(latencies), 1) if latencies else 0.0,
        "sample_errors": errors[:3],
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8912")
    parser.add_argument("--key", default="sk-artjourney-demo")
    parser.add_argument("--concurrency", default="1,4,8,16")
    parser.add_argument("--requests", type=int, default=16)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    levels = [int(x) for x in args.concurrency.split(",")]
    report = {"target": args.url, "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "levels": []}
    for level in levels:
        row = await run_level(args.url, args.key, level, args.requests)
        report["levels"].append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)

    out = Path(args.out) if args.out else Path(__file__).resolve().parent.parent / "data" / f"bench_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    print(f"\n报告已写入 {out}")


if __name__ == "__main__":
    asyncio.run(main())
