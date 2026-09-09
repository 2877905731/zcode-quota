#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""api-quota - 查看当前 ZCode 模型 API 的剩余余额与生成速度。

用法:
    python quota.py              # 人类可读报告
    python quota.py --json       # 结构化 JSON（供悬浮窗消费）
    python quota.py --hook       # ZCode hook 输出格式（SessionStart）
    python quota.py --watch 60   # 每 60 秒刷新一次

数据来源（自动选择，前者优先）:
    1. ~/.zcode/cli/db/db.sqlite 的 model_usage 表 —— 有 TTFT，能算纯解码速度
    2. ~/.zcode/cli/rollout/model-io-*.jsonl —— SQLite 不可用时的回退
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
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
SQLITE_DB = ZCODE_HOME / "cli" / "db" / "db.sqlite"
PROVIDER_CONFIG = ZCODE_HOME / "v2" / "config.json"
REPATCH_STATE = Path(__file__).resolve().parent.parent / "backup" / "repatch.json"

DEFAULT_WINDOW = int(os.environ.get("API_QUOTA_WINDOW", "10"))
MODEL_POOL = 200          # 按模型统计时取最近多少次调用
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


def _call_row(**kwargs) -> dict:
    """统一两种数据源的记录结构。"""
    base = {
        "model_id": None, "provider_id": None, "role": "main",
        "input_tokens": None, "output_tokens": None, "cache_read_tokens": None,
        "duration_ms": None, "ttft_ms": None, "session_id": None, "completed_at": None,
    }
    base.update(kwargs)
    return base


def read_calls_sqlite(limit: int = MODEL_POOL) -> list[dict] | None:
    """从 ZCode 的用量库读取最近调用。失败返回 None，交给 JSONL 回退。"""
    if not SQLITE_DB.is_file():
        return None
    try:
        con = sqlite3.connect(f"file:{SQLITE_DB}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return None
    try:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """
            SELECT model_id, provider_id, session_id, duration_ms, time_to_first_token_ms,
                   output_tokens, input_tokens, cache_read_input_tokens, started_at
            FROM model_usage
            WHERE output_tokens > 0 AND duration_ms > 0
              AND COALESCE(query_source, '') <> 'session_title'
            ORDER BY started_at DESC
            LIMIT ?
            """, (limit,)).fetchall()
    except sqlite3.Error:
        return None
    finally:
        con.close()

    calls = []
    for row in reversed(rows):                       # 转成时间升序
        started = row["started_at"]
        calls.append(_call_row(
            model_id=row["model_id"],
            provider_id=row["provider_id"],
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
            cache_read_tokens=row["cache_read_input_tokens"],
            duration_ms=row["duration_ms"],
            ttft_ms=row["time_to_first_token_ms"],
            session_id=row["session_id"],
            completed_at=(datetime.fromtimestamp(started / 1000).astimezone()
                          .isoformat(timespec="seconds") if started else None),
        ))
    return calls


def read_calls_jsonl(path: Path | None) -> list[dict]:
    """回退数据源：解析 rollout 日志。没有 TTFT。"""
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
        calls.append(_call_row(
            model_id=model.get("modelId"),
            provider_id=model.get("providerId"),
            role=model.get("role") or "main",
            input_tokens=usage.get("inputTokens"),
            output_tokens=usage.get("outputTokens"),
            cache_read_tokens=usage.get("cacheReadTokens"),
            duration_ms=rec.get("durationMs"),
            ttft_ms=None,
            session_id=rec.get("sessionId"),
            completed_at=rec.get("completedAt"),
        ))
    return calls


def load_calls(window: int = DEFAULT_WINDOW) -> dict:
    """返回 {calls, source, session_calls, session_id}。"""
    calls = read_calls_sqlite()
    if calls:
        session_id = calls[-1].get("session_id")
        session_calls = [c for c in calls if c.get("session_id") == session_id]
        return {"calls": calls, "source": "sqlite",
                "session_calls": session_calls, "session_id": session_id}

    # 回退：跨最近几个会话聚合，样本太少时中位数没有意义
    files = rollout_files()
    newest = files[0] if files else None
    session_calls = read_calls_jsonl(newest)
    pool = list(session_calls)
    for older in files[1:]:
        usable = [c for c in pool if c.get("output_tokens") and c.get("duration_ms")]
        if len(usable) >= window * 2:
            break
        pool = read_calls_jsonl(older) + pool
    session_id = newest.stem.replace("model-io-", "") if newest else None
    return {"calls": pool, "source": "jsonl",
            "session_calls": session_calls, "session_id": session_id}


# --------------------------------------------------------------------------
# 余额
# --------------------------------------------------------------------------

def _to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _http_json(url: str, api_key: str) -> dict:
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "User-Agent": "api-quota/1.0",
    })
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


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

def _rates(calls: list[dict]) -> tuple[list[float], list[float], list[float]]:
    """返回 (含预填充的速度, 纯解码速度, TTFT 列表)。"""
    total, decode, ttfts = [], [], []
    for call in calls:
        out = call.get("output_tokens")
        dur = call.get("duration_ms")
        if not out or not dur:
            continue
        total.append(out / (dur / 1000.0))
        ttft = call.get("ttft_ms")
        if ttft and dur > ttft:
            decode.append(out / ((dur - ttft) / 1000.0))
            ttfts.append(ttft)
    return total, decode, ttfts


def speed_stats(calls: list[dict], window: int = DEFAULT_WINDOW) -> dict:
    picks = [c for c in calls if c.get("role") == "main"] or calls
    usable = [c for c in picks if c.get("output_tokens") and c.get("duration_ms")]
    recent = usable[-window:]

    total_rates, decode_rates, ttfts = _rates(recent)
    last = recent[-1] if recent else None

    in_tokens = sum(c.get("input_tokens") or 0 for c in recent)
    cache_read = sum(c.get("cache_read_tokens") or 0 for c in recent)

    return {
        "samples": len(recent),
        # 含预填充的总速度（保守下界）
        "last_rate": total_rates[-1] if total_rates else None,
        "median_rate": statistics.median(total_rates) if total_rates else None,
        "mean_rate": statistics.fmean(total_rates) if total_rates else None,
        "min_rate": min(total_rates) if total_rates else None,
        "max_rate": max(total_rates) if total_rates else None,
        # 纯解码速度（需要 TTFT，只有 SQLite 数据源有）
        "last_decode_rate": decode_rates[-1] if decode_rates else None,
        "median_decode_rate": statistics.median(decode_rates) if decode_rates else None,
        "median_ttft_ms": statistics.median(ttfts) if ttfts else None,
        "cache_hit_rate": (cache_read / in_tokens) if in_tokens else None,
        "last_output_tokens": last["output_tokens"] if last else None,
        "last_duration_ms": last["duration_ms"] if last else None,
        "last_model": last["model_id"] if last else None,
        "last_completed_at": last["completed_at"] if last else None,
        "window_output_tokens": sum(c["output_tokens"] for c in recent),
        "window_input_tokens": in_tokens,
        "window_cache_read_tokens": cache_read,
    }


def model_breakdown(calls: list[dict]) -> list[dict]:
    """按模型分组统计——换了模型就能看出速度差多少。"""
    groups: dict[str, list[dict]] = {}
    for call in calls:
        groups.setdefault(call.get("model_id") or "unknown", []).append(call)

    rows = []
    for model_id, items in groups.items():
        stats = speed_stats(items, window=len(items))
        if not stats["samples"]:
            continue
        rows.append({
            "model_id": model_id,
            "provider_id": items[-1].get("provider_id"),
            "calls": stats["samples"],
            "median_rate": stats["median_rate"],
            "median_decode_rate": stats["median_decode_rate"],
            "median_ttft_ms": stats["median_ttft_ms"],
            "cache_hit_rate": stats["cache_hit_rate"],
            "output_tokens": stats["window_output_tokens"],
            "input_tokens": stats["window_input_tokens"],
        })
    rows.sort(key=lambda r: r["calls"], reverse=True)
    return rows


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

def _display_model(model_id: str | None) -> str | None:
    if not model_id:
        return None
    return re.sub(r"-expires-on-[\w.-]+$", "", model_id)


def build_snapshot(window: int = DEFAULT_WINDOW) -> dict:
    data = load_calls(window)
    calls = data["calls"]
    session_calls = data["session_calls"]
    last = session_calls[-1] if session_calls else (calls[-1] if calls else {})

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
        "source": data["source"],
        "provider_id": provider_id,
        "provider_name": provider.get("name"),
        "model_id": model_id,
        # 模型对象的扩展信息：显示名、服务商、数据来源
        "model": {
            "id": model_id,
            "display": _display_model(model_id),
            "provider_id": provider_id,
            "provider_name": provider.get("name"),
            "source": data["source"],
            "window": window,
        },
        "balance": balance,
        "speed": speed_stats(calls, window),
        "models": model_breakdown(calls),
        "session": {
            "id": data["session_id"],
            **session_totals(session_calls),
        },
    }


def _fmt_rate(value) -> str:
    return f"{value:,.0f} tok/s" if value is not None else "--"


def _fmt_int(value) -> str:
    return f"{value:,}" if isinstance(value, int) else "--"


def _fmt_ms(value) -> str:
    return f"{value:,.0f} ms" if value is not None else "--"


def _fmt_pct(value) -> str:
    return f"{value * 100:.1f}%" if value is not None else "--"


def render_text(snap: dict) -> str:
    out = []
    bal = snap["balance"]
    spd = snap["speed"]
    ses = snap["session"]
    model = snap.get("model") or {}

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
    out.append(f"=== 生成速度（最近 {spd['samples']} 次调用，{snap.get('source')}）===")
    if spd["samples"]:
        out.append(f"  纯解码    {_fmt_rate(spd['median_decode_rate'])}（中位）"
                   f"   最近一次 {_fmt_rate(spd['last_decode_rate'])}")
        out.append(f"  含预填充  {_fmt_rate(spd['median_rate'])}（中位）"
                   f"   区间 {_fmt_rate(spd['min_rate'])} ~ {_fmt_rate(spd['max_rate'])}")
        out.append(f"  首字延迟  {_fmt_ms(spd['median_ttft_ms'])}（中位）"
                   f"   缓存命中 {_fmt_pct(spd['cache_hit_rate'])}")
        out.append(f"  区间用量  输出 {_fmt_int(spd['window_output_tokens'])} tok"
                   f" / 输入 {_fmt_int(spd['window_input_tokens'])} tok"
                   f"（缓存 {_fmt_int(spd['window_cache_read_tokens'])} tok）")
        if spd["median_decode_rate"] is None:
            out.append("  （数据源没有 TTFT，纯解码速度不可用）")
    else:
        out.append("  暂无调用记录")

    models = snap.get("models") or []
    if len(models) > 1:
        out.append("")
        out.append(f"=== 按模型（最近 {sum(m['calls'] for m in models)} 次调用）===")
        for row in models:
            out.append(
                f"  {_display_model(row['model_id']) or row['model_id']}"
                f"   {row['calls']} 次"
                f"   纯解码 {_fmt_rate(row['median_decode_rate'])}"
                f"   首字 {_fmt_ms(row['median_ttft_ms'])}"
                f"   缓存 {_fmt_pct(row['cache_hit_rate'])}"
            )

    out.append("")
    out.append(f"=== 本次会话 ===  {model.get('display') or model.get('id') or '--'}")
    out.append(f"  调用 {ses['calls']} 次，累计输出 {_fmt_int(ses['output_tokens'])} tok"
               f" / 输入 {_fmt_int(ses['input_tokens'])} tok")
    out.append(f"  更新于 {snap['generated_at']}")
    return "\n".join(out)


def recent_repatch_note(max_age_seconds: int = 900) -> str:
    """ZCode 升级后补丁被自动重打过的话，提示一句（15 分钟内有效）。"""
    try:
        state = json.loads(REPATCH_STATE.read_text(encoding="utf-8"))
        at = datetime.fromisoformat(state["at"])
        age = (datetime.now().astimezone() - at).total_seconds()
    except Exception:
        return ""
    if age > max_age_seconds:
        return ""
    return " 另：刚检测到 ZCode 升级并自动重新注入了界面补丁，重启 ZCode 后状态条才会出现。"


def render_hook(snap: dict) -> str:
    """SessionStart hook 输出：把一行状态注入会话上下文。"""
    bal = snap["balance"]
    spd = snap["speed"]
    if bal.get("ok"):
        parts = [f"{c['currency']} {c['total']}" for c in (bal.get("currencies") or [])]
        balance_text = " / ".join(parts) if parts else "未知"
    else:
        balance_text = bal.get("message", "查询失败")
    rate_text = _fmt_rate(spd.get("median_decode_rate") or spd.get("median_rate")) \
        if spd.get("samples") else "无样本"
    context = (f"[api-quota] {snap.get('provider_name') or snap.get('provider_id')} "
               f"余额 {balance_text}；最近 {spd.get('samples', 0)} 次调用中位速度 {rate_text}。"
               f"用户问余额/速度时可直接引用，或运行 /quota 重新查询。")

    note = recent_repatch_note()
    if note:
        context += note

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
