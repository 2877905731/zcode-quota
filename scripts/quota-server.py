#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""api-quota 本地数据服务：给 ZCode 界面内的状态条提供余额/速度 JSON。

只监听 127.0.0.1，不对外暴露。接口：
    GET /quota   余额 + 速度快照（30 秒缓存）
    GET /health  存活检查
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import quota  # noqa: E402

HOST = os.environ.get("API_QUOTA_HOST", "127.0.0.1")
PORT = int(os.environ.get("API_QUOTA_PORT", "8788"))
CACHE_SECONDS = int(os.environ.get("API_QUOTA_CACHE", "30"))

JSON_TYPE = "application/json; charset=utf-8"
JS_TYPE = "application/javascript; charset=utf-8"

_build_lock = threading.Lock()
_cache_lock = threading.Lock()
_cache: dict = {"at": 0.0, "payload": None}


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


def main() -> int:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    print(f"api-quota server listening on http://{HOST}:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
