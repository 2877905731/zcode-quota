#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""api-quota 本地数据服务：给 ZCode 界面内的状态条提供余额/速度 JSON。

只监听 127.0.0.1，不对外暴露。接口：
    GET /quota           余额 + 速度快照（30 秒缓存）
    GET /quota-status.js 界面内注入的脚本
    GET /health          存活检查

生命周期默认**跟随 ZCode**：ZCode 退出后（宽限期结束）自动停止。
由插件的 SessionStart 钩子通过 `--ensure` 拉起，不需要开机自启。

用法：
    python quota-server.py               # 前台运行，随 ZCode 启停
    python quota-server.py --ensure      # 没在跑就后台拉起一个，然后立刻退出（钩子用）
    python quota-server.py --standalone  # 不跟随 ZCode，一直运行（手动调试用）
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import quota  # noqa: E402

HOST = os.environ.get("API_QUOTA_HOST", "127.0.0.1")
PORT = int(os.environ.get("API_QUOTA_PORT", "8788"))
CACHE_SECONDS = int(os.environ.get("API_QUOTA_CACHE", "30"))
EXIT_GRACE_SECONDS = int(os.environ.get("API_QUOTA_EXIT_GRACE", "20"))
POLL_SECONDS = 5

JSON_TYPE = "application/json; charset=utf-8"
JS_TYPE = "application/javascript; charset=utf-8"

_build_lock = threading.Lock()
_cache_lock = threading.Lock()
_cache: dict = {"at": 0.0, "payload": None}


# --------------------------------------------------------------------------
# ZCode 进程检测（Toolhelp32 快照，不依赖外部命令）
# --------------------------------------------------------------------------

class _PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_ulong),
        ("cntUsage", ctypes.c_ulong),
        ("th32ProcessID", ctypes.c_ulong),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", ctypes.c_ulong),
        ("cntThreads", ctypes.c_ulong),
        ("th32ParentProcessID", ctypes.c_ulong),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", ctypes.c_ulong),
        ("szExeFile", ctypes.c_char * 260),
    ]


def zcode_running() -> bool:
    """ZCode.exe 是否在运行。查不到时返回 True（宁可不停服务）。"""
    if os.name != "nt":
        return True
    TH32CS_SNAPPROCESS = 0x00000002
    INVALID = ctypes.c_void_p(-1).value
    k32 = ctypes.windll.kernel32
    snapshot = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID or not snapshot:
        return True
    try:
        entry = _PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32)
        ok = k32.Process32First(snapshot, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.decode("ascii", "ignore").lower() == "zcode.exe":
                return True
            ok = k32.Process32Next(snapshot, ctypes.byref(entry))
    except Exception:
        return True
    finally:
        k32.CloseHandle(snapshot)
    return False


def lifecycle_watch(server: ThreadingHTTPServer, standalone: bool) -> None:
    """ZCode 退出后关掉自己。"""
    if standalone:
        return
    absent_since: float | None = None
    while True:
        time.sleep(POLL_SECONDS)
        if zcode_running():
            absent_since = None
            continue
        if absent_since is None:
            absent_since = time.monotonic()
        elif time.monotonic() - absent_since >= EXIT_GRACE_SECONDS:
            print("ZCode 已退出，停止数据服务", flush=True)
            server.shutdown()
            return


# --------------------------------------------------------------------------
# 数据
# --------------------------------------------------------------------------

def snapshot_json() -> str:
    now = time.monotonic()
    with _cache_lock:
        if _cache["payload"] is not None and now - _cache["at"] < CACHE_SECONDS:
            return _cache["payload"]

    # 同一时刻只允许一个请求去查余额，避免打爆接口
    with _build_lock:
        with _cache_lock:
            if _cache["payload"] is not None and now - _cache["at"] < CACHE_SECONDS:
                return _cache["payload"]
        try:
            payload = json.dumps(quota.build_snapshot(), ensure_ascii=False)
        except Exception as exc:
            payload = json.dumps({"error": str(exc)}, ensure_ascii=False)
        with _cache_lock:
            _cache.update(at=time.monotonic(), payload=payload)
        return payload


class Handler(BaseHTTPRequestHandler):
    server_version = "api-quota/1.0"

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path == "/quota":
            body, status, ctype = snapshot_json().encode("utf-8"), 200, JSON_TYPE
        elif path == "/health":
            body, status, ctype = b'{"ok":true}', 200, JSON_TYPE
        elif path == "/quota-status.js":
            # 注入到 ZCode 界面里的脚本，由它去请求 /quota
            try:
                body = (HERE / "quota-status.js").read_bytes()
                status, ctype = 200, JS_TYPE
            except OSError:
                body, status, ctype = b"/* missing */", 404, JS_TYPE
        else:
            body, status, ctype = b'{"error":"not found"}', 404, JSON_TYPE

        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


# --------------------------------------------------------------------------
# --ensure：没在跑就后台拉起一个
# --------------------------------------------------------------------------

def service_up(timeout: float = 3.0) -> bool:
    try:
        with urllib.request.urlopen(f"http://{HOST}:{PORT}/health", timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def ensure_running() -> int:
    if service_up():
        return 0
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = str(pythonw) if pythonw.is_file() else sys.executable
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | \
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        subprocess.Popen([exe, str(HERE / "quota-server.py")],
                         creationflags=flags, close_fds=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="api-quota 本地数据服务")
    parser.add_argument("--ensure", action="store_true",
                        help="没在跑就后台拉起一个，然后立刻退出")
    parser.add_argument("--standalone", action="store_true",
                        help="不跟随 ZCode，一直运行")
    args = parser.parse_args()

    if args.ensure:
        return ensure_running()

    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    threading.Thread(target=lifecycle_watch, args=(server, args.standalone),
                     daemon=True).start()

    mode = "standalone" if args.standalone else "跟随 ZCode 启停"
    print(f"api-quota server listening on http://{HOST}:{PORT} ({mode})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
