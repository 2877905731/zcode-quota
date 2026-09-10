#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""api-quota 悬浮窗：始终置顶显示当前 API 的剩余余额与生成速度。

双击刷新，右键菜单可关闭。刷新间隔用环境变量 API_QUOTA_REFRESH 调整（秒，默认 60）。
"""

from __future__ import annotations

import ctypes
import os
import queue
import re
import sys
import threading
import tkinter as tk
from pathlib import Path

_SINGLETON_HANDLE: list = []


def acquire_singleton() -> bool:
    """同一时间只允许一个悬浮窗实例，重复双击直接静默退出。"""
    if os.name != "nt":
        return True
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, False, "Local\\api-quota-widget")
        if not handle:
            return True
        if kernel32.GetLastError() == 183:        # ERROR_ALREADY_EXISTS
            return False
        _SINGLETON_HANDLE.append(handle)          # 持有到进程结束，句柄自动释放
    except Exception:
        return True
    return True

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import quota  # noqa: E402  （与脚本同目录）

REFRESH_SECONDS = max(10, int(os.environ.get("API_QUOTA_REFRESH", "60")))
BG = "#1f1f1f"
FG = "#e8e8e8"
MUTED = "#9a9a9a"
GOOD = "#5fd68a"
WARN = "#f0a04b"
BAD = "#f06b6b"

FONT = ("Microsoft YaHei UI", 9)


class Widget:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("API 余额")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=BG)

        frame = tk.Frame(self.root, bg=BG, padx=12, pady=9,
                         highlightthickness=1, highlightbackground="#3a3a3a")
        frame.pack(fill="both", expand=True)

        self.title_label = tk.Label(frame, text="API 余额", bg=BG, fg=MUTED,
                                    font=FONT, anchor="w", justify="left")
        self.balance_label = tk.Label(frame, text="查询中…", bg=BG, fg=FG,
                                      font=("Microsoft YaHei UI", 13, "bold"),
                                      anchor="w", justify="left")
        self.speed_label = tk.Label(frame, text="--", bg=BG, fg=FG,
                                    font=FONT, anchor="w", justify="left")
        self.updated_label = tk.Label(frame, text="", bg=BG, fg=MUTED,
                                      font=("Microsoft YaHei UI", 8), anchor="w",
                                      justify="left")

        for widget in (self.title_label, self.balance_label,
                       self.speed_label, self.updated_label):
            widget.pack(fill="x")

        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="立即刷新", command=self.refresh)
        self.menu.add_separator()
        self.menu.add_command(label="关闭", command=self.root.destroy)

        for widget in (frame, self.title_label, self.balance_label,
                       self.speed_label, self.updated_label):
            widget.bind("<ButtonPress-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._on_drag)
            widget.bind("<Double-Button-1>", lambda _e: self.refresh())
            widget.bind("<Button-3>", self._popup)

        self._drag = (0, 0)
        # 工作线程只往队列里放结果，所有 Tk 调用都留在主线程
        self.queue: queue.Queue = queue.Queue()
        self._place_top_right()
        self.root.after(250, self._drain)
        self.refresh()
        self.root.mainloop()

    # -- 窗口位置 / 拖动 ---------------------------------------------------

    def _place_top_right(self) -> None:
        self.root.update_idletasks()
        width, height = 240, 116
        screen_w = self.root.winfo_screenwidth()
        self.root.geometry(f"{width}x{height}+{screen_w - width - 40}+60")

    def _start_drag(self, event) -> None:
        self._drag = (event.x_root - self.root.winfo_x(),
                      event.y_root - self.root.winfo_y())

    def _on_drag(self, event) -> None:
        dx, dy = self._drag
        self.root.geometry(f"+{event.x_root - dx}+{event.y_root - dy}")

    def _popup(self, event) -> None:
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    # -- 刷新 -------------------------------------------------------------

    def refresh(self) -> None:
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self) -> None:
        try:
            self.queue.put((quota.build_snapshot(), None))
        except Exception as exc:
            self.queue.put((None, exc))

    def _drain(self) -> None:
        try:
            while True:
                snap, error = self.queue.get_nowait()
                self._apply(snap, error)
        except queue.Empty:
            pass
        self.root.after(250, self._drain)

    def _apply(self, snap, error) -> None:
        if error is not None or snap is None:
            self.balance_label.configure(text="读取失败", fg=BAD)
            self.speed_label.configure(text=str(error)[:60])
            self.updated_label.configure(text="")
            self.root.after(REFRESH_SECONDS * 1000, self.refresh)
            return

        bal = snap["balance"]
        spd = snap["speed"]
        model_info = snap.get("model") or {}

        model = model_info.get("display") or re.sub(
            r"-expires-on-[\w.-]+$", "", snap.get("model_id") or "") or "--"
        if len(model) > 26:
            model = model[:24] + "…"
        self.title_label.configure(
            text=f"{snap.get('provider_name') or snap.get('provider_id') or 'API'} · {model}")

        decode = spd.get("median_decode_rate")
        rate = decode if decode is not None else spd.get("median_rate")
        rate_label = "速度" if decode is not None else "速度(含预填)"
        speed_text = f"{rate_label} {self._rate(rate)}"

        if bal.get("ok") and bal.get("kind") == "quota":
            windows = bal.get("windows") or []
            if windows:
                primary = windows[0]
                pct = primary.get("remaining_pct")
                self.balance_label.configure(
                    text=f"{primary['label']} {quota.fmt_pct(pct)}" if pct is not None
                    else f"{primary['label']} 额度",
                    fg=GOOD if (pct is None or pct >= 20) else WARN)
                extra = "  ".join(
                    f"{w['label']} {quota.fmt_pct(w['remaining_pct'])}"
                    for w in windows[1:3] if w.get("remaining_pct") is not None)
                self.speed_label.configure(
                    text=speed_text + (f"   {extra}" if extra else ""))
            else:
                self.balance_label.configure(text="额度未知", fg=WARN)
                self.speed_label.configure(text=speed_text)
        elif bal.get("ok"):
            items = bal.get("currencies") or []
            if items:
                primary = items[0]
                self.balance_label.configure(
                    text=f"{primary['currency']} {primary['total']}",
                    fg=GOOD if bal.get("available") else WARN)
                extra = "  ".join(f"{i['currency']} {i['total']}" for i in items[1:])
                self.speed_label.configure(
                    text=speed_text + (f"   {extra}" if extra else ""))
            else:
                self.balance_label.configure(text="余额未知", fg=WARN)
                self.speed_label.configure(text=speed_text)
        else:
            self.balance_label.configure(text="余额不可用", fg=WARN)
            self.speed_label.configure(text=f"{bal.get('message', '')}  |  {speed_text}")

        stamp = snap["generated_at"][11:19]
        detail = [f"{stamp} 更新"]
        if spd.get("median_ttft_ms") is not None:
            detail.append(f"首字 {spd['median_ttft_ms'] / 1000:.1f}s")
        if spd.get("cache_hit_rate") is not None:
            detail.append(f"缓存 {quota.fmt_pct(spd['cache_hit_rate'] * 100)}")
        detail.append(f"{spd.get('samples', 0)} 次采样")
        self.updated_label.configure(text=" · ".join(detail))

        self.root.after(REFRESH_SECONDS * 1000, self.refresh)

    @staticmethod
    def _rate(value) -> str:
        return f"{value:,.0f} tok/s" if value is not None else "--"


if __name__ == "__main__":
    if not acquire_singleton():
        sys.exit(0)
    Widget()
