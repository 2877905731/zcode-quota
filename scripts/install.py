#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""安装 api-quota：注册插件目录 + 启动数据服务。

用法：
    python scripts/install.py

做的事：
    1. 检查 Python 版本和 tkinter（悬浮窗需要）
    2. 把本插件目录写进 ~/.zcode/cli/config.json 的 plugins.dirs（会先备份）
    3. 启动本地数据服务

装完之后还需要手动跑一次 应用界面补丁.cmd 并重启 ZCode，界面内状态条才会出现。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
ZCODE_HOME = Path(os.environ.get("ZCODE_HOME") or Path.home() / ".zcode")
CONFIG = ZCODE_HOME / "cli" / "config.json"
PORT = int(os.environ.get("API_QUOTA_PORT", "8788"))
HEALTH = f"http://127.0.0.1:{PORT}/health"


def log(msg: str = "") -> None:
    print(msg, flush=True)


def check_python() -> list[str]:
    problems = []
    if sys.version_info < (3, 10):
        problems.append(f"Python 版本过低：{sys.version.split()[0]}，需要 3.10+")
    try:
        import tkinter  # noqa: F401
    except Exception:
        problems.append("缺少 tkinter，悬浮窗不可用（余额查询和状态条不受影响）")
    return problems


def register_plugin() -> str:
    if not CONFIG.parent.is_dir():
        return f"没找到 {CONFIG.parent}，请先安装并启动一次 ZCode，再重跑本脚本"

    data: dict = {}
    if CONFIG.is_file():
        try:
            data = json.loads(CONFIG.read_text(encoding="utf-8"))
        except ValueError:
            return f"{CONFIG} 不是合法 JSON，跳过自动注册（请手动把插件目录加进 plugins.dirs）"

    plugins = data.setdefault("plugins", {})
    dirs = plugins.setdefault("dirs", [])
    target = str(PLUGIN_ROOT)
    if target in dirs:
        return "插件目录已在 plugins.dirs 中，无需重复注册"

    if CONFIG.is_file():
        backup = CONFIG.with_suffix(".json.bak")
        if not backup.is_file():
            shutil.copy2(CONFIG, backup)
    dirs.append(target)
    CONFIG.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    return f"已把插件目录写进 plugins.dirs：{target}"


def service_running() -> bool:
    try:
        with urllib.request.urlopen(HEALTH, timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


def start_service() -> str:
    if service_running():
        return "数据服务已在运行"
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = str(pythonw) if pythonw.is_file() else sys.executable
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | \
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        subprocess.Popen([exe, str(PLUGIN_ROOT / "scripts" / "quota-server.py")],
                         creationflags=flags, close_fds=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:
        return f"数据服务启动失败：{exc}（可手动运行 启动余额服务.cmd）"
    for _ in range(10):
        time.sleep(0.5)
        if service_running():
            return f"数据服务已启动（http://127.0.0.1:{PORT}）"
    return "数据服务没有响应，请手动运行 启动余额服务.cmd 看看报错"


def main() -> int:
    log("=" * 46)
    log(" api-quota 安装")
    log("=" * 46)
    log(f"插件目录：{PLUGIN_ROOT}")
    log()

    problems = check_python()
    if problems:
        log("环境检查有问题：")
        for item in problems:
            log(f"  - {item}")
        log()

    log(f"[1/2] {register_plugin()}")
    log(f"[2/2] {start_service()}")
    log()
    log("-" * 46)
    log("接下来：")
    log("  1) 双击 应用界面补丁.cmd（无需退出 ZCode）")
    log("  2) 重启 ZCode")
    log("  之后输入框下方就会常驻显示余额和速度。")
    log()
    log("可选：")
    log("  - 双击 启动余额悬浮窗.cmd 可以再开一个置顶小窗")
    log("  - 把 启动 文件夹里的 VBS 复制过去可让数据服务开机自启")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
