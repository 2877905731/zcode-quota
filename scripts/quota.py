#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""api-quota - 查看当前 ZCode 模型 API 的剩余余额与生成速度。

用法:
    python quota.py              # 人类可读报告
    python quota.py --json       # 结构化 JSON（供悬浮窗消费）
    python quota.py --hook       # ZCode hook 输出格式（SessionStart）
    python quota.py --watch 60   # 每 60 秒刷新一次
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

# 中文在管道/重定向下也不乱码
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ZCODE_HOME = Path(os.environ.get("ZCODE_HOME") or Path.home() / ".zcode")
ROLLOUT_DIR = ZCODE_HOME / "cli" / "rollout"
PROVIDER_CONFIG = ZCODE_HOME / "v2" / "config.json"

DEFAULT_WINDOW = 10
TAIL_BYTES = 1024 * 1024
TAIL_LINES = 400
HTTP_TIMEOUT = 10


# --------------------------------------------------------------------------
# 数据读取
# --------------------------------------------------------------------------

def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _tail_lines(path: Path, max_bytes: int = TAIL_BYTES, max_lines: int = TAIL_LINES):
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
                fh.readline()  # 丢掉可能被截断的半行
            data = fh.read()
    except OSError:
        return []
    return data.decode("utf-8", "replace").splitlines()[-max_lines:]


def rollout_files(limit: int = 5) -> list[Path]:
    """按修改时间倒序返回最近的会话记录文件。"""
    try:
        files = [p for p in ROLLOUT_DIR.glob("model-io-*.jsonl") if p.is_file()]
    except OSError:
        return []
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files[:limit]


def read_calls(path: Path | None) -> list[dict]:
    """读取最近的模型调用记录（按时间升序）。"""
    if path is None:
        return []
    calls = []
    for line in _tail_lines(path):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        model = rec.get("model") or {}
        usage = ((rec.get("response") or {}).get("usage")) or {}
        calls.append({
            "model_id": model.get("modelId"),
            "provider_id": model.get("providerId"),
            "role": model.get("role") or "main",
            "input_tokens": usage.get("inputTokens"),
            "output_tokens": usage.get("outputTokens"),
            "cache_read_tokens": usage.get("cacheReadTokens"),
            "duration_ms": rec.get("durationMs"),
            "completed_at": rec.get("completedAt"),
        })
    return calls


# --------------------------------------------------------------------------
# 余额
# --------------------------------------------------------------------------

def _http_json(url: str, api_key: str) -> dict:
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "User-Agent": "api-quota/1.0",
    })
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def fetch_balance(provider: dict) -> dict:
    """查询余额。目前实现了 DeepSeek，其它服务商返回明确的未支持提示。"""
    options = provider.get("options") or {}
    name = provider.get("name") or "未知服务商"
    api_key = (options.get("apiKey") or "").strip()
    base_url = (options.get("baseURL") or "").strip()

    if not api_key:
        return {"ok": False, "provider_name": name, "message": "当前服务商没有配置 API Key"}
    if not base_url:
        return {"ok": False, "provider_name": name, "message": "当前服务商没有配置 baseURL"}

    host = urlparse(base_url).hostname or ""
    if "deepseek" not in host:
        return {
            "ok": False,
            "provider_name": name,
            "message": f"{name} 暂不支持自动查询余额（目前支持 DeepSeek）",
        }

    endpoint = f"https://{host}/user/balance"
    try:
        data = _http_json(endpoint, api_key)
    except Exception as exc:  # 网络、鉴权、解析
        return {"ok": False, "provider_name": name, "message": f"余额查询失败：{exc}"}

    currencies = [{
        "currency": item.get("currency"),
        "total": item.get("total_balance"),
        "granted": item.get("granted_balance"),
        "topped_up": item.get("topped_up_balance"),
    } for item in (data.get("balance_infos") or [])]
    # 金额大的排前面：多币种账户里真正在计费的那个通常是主余额
    currencies.sort(key=lambda c: abs(_to_float(c["total"])), reverse=True)

    return {
        "ok": True,
        "provider_name": name,
        "endpoint": endpoint,
        "available": bool(data.get("is_available")),
        "currencies": currencies,
    }


# --------------------------------------------------------------------------
# 速度
# --------------------------------------------------------------------------

def speed_stats(calls: list[dict], window: int = DEFAULT_WINDOW) -> dict:
    picks = [c for c in calls if c.get("role") == "main"] or calls
    usable = [c for c in picks if c.get("output_tokens") and c.get("duration_ms")]
    recent = usable[-window:]
    rates = [c["output_tokens"] / (c["duration_ms"] / 1000.0) for c in recent]
    last = recent[-1] if recent else None

    return {
        "samples": len(recent),
        "last_rate": rates[-1] if rates else None,
        "median_rate": statistics.median(rates) if rates else None,
        "mean_rate": statistics.fmean(rates) if rates else None,
        "min_rate": min(rates) if rates else None,
        "max_rate": max(rates) if rates else None,
        "last_output_tokens": last["output_tokens"] if last else None,
        "last_duration_ms": last["duration_ms"] if last else None,
        "last_model": last["model_id"] if last else None,
        "last_completed_at": last["completed_at"] if last else None,
        "window_output_tokens": sum(c["output_tokens"] for c in recent),
        "window_input_tokens": sum(c["input_tokens"] or 0 for c in recent),
        "window_cache_read_tokens": sum(c["cache_read_tokens"] or 0 for c in recent),
    }


def session_totals(calls: list[dict]) -> dict:
    return {
        "calls": len(calls),
        "output_tokens": sum(c.get("output_tokens") or 0 for c in calls),
        "input_tokens": sum(c.get("input_tokens") or 0 for c in calls),
        "cache_read_tokens": sum(c.get("cache_read_tokens") or 0 for c in calls),
    }


# --------------------------------------------------------------------------
# 快照与渲染
# --------------------------------------------------------------------------

def build_snapshot(window: int = DEFAULT_WINDOW) -> dict:
    files = rollout_files()
    newest = files[0] if files else None
    session_calls = read_calls(newest)

    # 速度样本跨最近几个会话聚合：样本太少时中位数没有意义
    pool = list(session_calls)
    for older in files[1:]:
        usable = [c for c in pool if c.get("output_tokens") and c.get("duration_ms")]
        if len(usable) >= window * 2:
            break
        pool = read_calls(older) + pool

    last = session_calls[-1] if session_calls else {}

    provider_id = last.get("provider_id")
    model_id = last.get("model_id")
    providers = (_load_json(PROVIDER_CONFIG).get("provider") or {})
    provider = providers.get(provider_id) or {}

    if provider:
        balance = fetch_balance(provider)
    else:
        balance = {"ok": False, "provider_name": None,
                   "message": "未找到当前服务商配置（读不到 ~/.zcode/v2/config.json）"}

    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "provider_id": provider_id,
        "provider_name": provider.get("name"),
        "model_id": model_id,
        "balance": balance,
        "speed": speed_stats(pool, window),
        "session": session_totals(session_calls),
    }


def _fmt_rate(value) -> str:
    return f"{value:,.0f} tok/s" if value is not None else "--"


def _fmt_int(value) -> str:
    return f"{value:,}" if isinstance(value, int) else "--"


def render_text(snap: dict) -> str:
    out = []
    bal = snap["balance"]
    spd = snap["speed"]
    ses = snap["session"]

    title = snap.get("provider_name") or snap.get("provider_id") or "当前 API"
    out.append(f"=== API 余额（{title}）===")
    if bal.get("ok"):
        for item in bal.get("currencies") or []:
            out.append(
                f"  {item['currency']}  {item['total']}"
                f"   （充值 {item['topped_up']} / 赠送 {item['granted']}）"
            )
        if bal.get("available") is False:
            out.append("  状态：不可用")
    else:
        out.append(f"  {bal.get('message', '查询失败')}")

    out.append("")
    out.append(f"=== 生成速度（最近 {spd['samples']} 次主模型调用）===")
    if spd["samples"]:
        out.append(f"  最近一次  {_fmt_rate(spd['last_rate'])}"
                   f"   （输出 {_fmt_int(spd['last_output_tokens'])} tok"
                   f" / {spd['last_duration_ms'] / 1000:.1f}s）")
        out.append(f"  中位速度  {_fmt_rate(spd['median_rate'])}")
        out.append(f"  平均速度  {_fmt_rate(spd['mean_rate'])}"
                   f"   （区间 {_fmt_rate(spd['min_rate'])} ~ {_fmt_rate(spd['max_rate'])}）")
        out.append(f"  区间用量  输出 {_fmt_int(spd['window_output_tokens'])} tok"
                   f" / 输入 {_fmt_int(spd['window_input_tokens'])} tok"
                   f"（缓存命中 {_fmt_int(spd['window_cache_read_tokens'])} tok）")
    else:
        out.append("  暂无调用记录")

    out.append("")
    out.append(f"=== 本次会话 ===  模型 {snap.get('model_id') or '--'}")
    out.append(f"  调用 {ses['calls']} 次，累计输出 {_fmt_int(ses['output_tokens'])} tok"
               f" / 输入 {_fmt_int(ses['input_tokens'])} tok")
    out.append(f"  更新于 {snap['generated_at']}")
    return "\n".join(out)


def render_hook(snap: dict) -> str:
    """SessionStart hook 输出：把一行状态注入会话上下文。"""
    bal = snap["balance"]
    spd = snap["speed"]
    if bal.get("ok"):
        parts = [f"{c['currency']} {c['total']}" for c in (bal.get("currencies") or [])]
        balance_text = " / ".join(parts) if parts else "未知"
    else:
        balance_text = bal.get("message", "查询失败")
    rate_text = _fmt_rate(spd.get("median_rate")) if spd.get("samples") else "无样本"
    context = (f"[api-quota] {snap.get('provider_name') or snap.get('provider_id')} "
               f"余额 {balance_text}；最近 {spd.get('samples', 0)} 次调用中位速度 {rate_text}。"
               f"用户问余额/速度时可直接引用，或运行 /quota 重新查询。")
    return json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    }, ensure_ascii=True)


# --------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="查看当前 API 的剩余余额与生成速度")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--hook", action="store_true", help="输出 ZCode hook JSON")
    parser.add_argument("--watch", type=int, metavar="SECONDS", help="循环刷新")
    parser.add_argument("--window", type=int, default=DEFAULT_WINDOW, help="速度统计的采样次数")
    args = parser.parse_args()

    def once() -> str:
        snap = build_snapshot(args.window)
        if args.hook:
            return render_hook(snap)
        if args.json:
            return json.dumps(snap, ensure_ascii=False, indent=2)
        return render_text(snap)

    if args.watch:
        try:
            while True:
                print(once(), flush=True)
                time.sleep(max(5, args.watch))
                print()
        except KeyboardInterrupt:
            return 0

    print(once())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
