#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 api-quota 状态条注入 ZCode 的界面。

ZCode 没有官方的"输入框下方"扩展点，所以只能改它自己的 app.asar。
做法是**等长就地改写**：往 out/renderer/index.html 里插一小段 loader，
同时在 <style> 块里压掉等量空白，让文件长度一个字节都不变。

这样 asar 头部和后面所有文件的数据偏移都不用动，风险最小，
而且 ZCode 正在运行时也能直接写入（不需要退出 ZCode）。

loader 本身不干活，它从本机 http://127.0.0.1:8788/quota-status.js 加载真正的脚本，
所以之后改状态条样式只要改那个 js 文件，不用再动 app.asar。

用法：
    python patch-zcode.py --check      # 查看状态
    python patch-zcode.py --apply      # 打补丁（无需退出 ZCode）
    python patch-zcode.py --restore    # 还原
    python patch-zcode.py --dry-run    # 只生成并校验长度，不写入
"""

from __future__ import annotations

import argparse
import json
import os
import re
import struct
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKUP_DIR = HERE.parent / "backup"
RENDERER_FILE = "out/renderer/index.html"

# --restore 之后写这个标记，--ensure 就不会把补丁偷偷加回来
DISABLED_MARKER = BACKUP_DIR / ".patch-disabled"
# --ensure 自动重打成功后写这个文件，供 /quota 提示用户
REPATCH_STATE = BACKUP_DIR / "repatch.json"

_QUIET = False
_LOG: list[str] = []


# 插到 </body> 前；带 5 次重试，避免数据服务还没起来时加载失败
LOADER = (
    '<script>!function r(n){var s=document.createElement("script");'
    's.src="http://127.0.0.1:8788/quota-status.js?t="+Date.now();'
    's.onerror=function(){n>0&&setTimeout(function(){r(n-1)},3000)};'
    'document.body.appendChild(s)}(5)</script>'
)


def log(msg: str = "") -> None:
    """--ensure 是给钩子调的，stdout 必须是干净的，所以那时改为写日志文件。"""
    if _QUIET:
        _LOG.append(msg)
        return
    print(msg, flush=True)


def flush_log(name: str) -> None:
    if not _LOG:
        return
    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        with (BACKUP_DIR / name).open("a", encoding="utf-8") as fh:
            fh.write(f"\n--- {stamp} ---\n" + "\n".join(_LOG) + "\n")
    except OSError:
        pass
    _LOG.clear()


# --------------------------------------------------------------------------
# asar 读写
# --------------------------------------------------------------------------

def running_zcode_exe() -> Path | None:
    """从正在运行的 ZCode 进程反查安装目录（补丁可以在 ZCode 运行时打）。"""
    if os.name != "nt":
        return None
    try:
        import subprocess
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-Process ZCode -ErrorAction SilentlyContinue | "
             "Where-Object { $_.Path } | Select-Object -First 1 -ExpandProperty Path)"],
            capture_output=True, timeout=25).stdout.decode("utf-8", "replace").strip()
    except Exception:
        return None
    path = Path(out) if out else None
    return path if path and path.is_file() else None


def static_candidates() -> list[Path]:
    """常见安装位置。自定义安装目录请用 --asar 指定。"""
    out: list[Path] = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        out.append(Path(local) / "Programs" / "ZCode" / "resources" / "app.asar")
    for env in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
        base = os.environ.get(env)
        if base:
            out.append(Path(base) / "ZCode" / "resources" / "app.asar")
    return out


def find_asar(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise SystemExit(f"找不到 app.asar: {path}")
        return path

    exe = running_zcode_exe()
    if exe:
        candidate = exe.parent / "resources" / "app.asar"
        if candidate.is_file():
            return candidate

    for candidate in static_candidates():
        if candidate.is_file():
            return candidate

    raise SystemExit(
        "没找到 app.asar。请用 --asar 指定，例如：\n"
        '  python patch-zcode.py --apply --asar "D:\\ZCode\\resources\\app.asar"')


def load_asar(path: Path):
    with path.open("rb") as fh:
        head = fh.read(16)
        if len(head) < 16:
            raise SystemExit("不是有效的 asar 文件")
        json_len = struct.unpack("<I", head[12:16])[0]
        header = json.loads(fh.read(json_len).decode("utf-8"))
    return header, 16 + json_len


def find_entry(node: dict, target: str):
    for part in [p for p in target.split("/") if p]:
        node = (node.get("files") or {}).get(part)
        if node is None:
            return None
    return node


def read_entry(fh, data_start: int, entry: dict) -> bytes:
    fh.seek(data_start + int(entry["offset"]))
    return fh.read(int(entry["size"]))


def html_entry(asar: Path):
    header, data_start = load_asar(asar)
    entry = find_entry(header, RENDERER_FILE)
    if entry is None:
        raise SystemExit(f"app.asar 里找不到 {RENDERER_FILE}")
    return data_start, entry


# --------------------------------------------------------------------------
# 等长改写
# --------------------------------------------------------------------------

def build_loader_html(html: bytes) -> bytes:
    """插入 loader，并从 <style> 块压掉等量空白，保证输出长度与输入完全一致。"""
    text = html.decode("utf-8")
    if "quota-status.js" in text:
        return html

    marker = "</body>"
    if marker not in text:
        raise SystemExit("index.html 里找不到 </body>，无法注入")
    injected = text.replace(marker, LOADER + marker, 1)
    need = len(injected.encode("utf-8")) - len(html)

    start = injected.index("<style>")
    end = injected.index("</style>", start)
    css = injected[start:end]
    compact = re.sub(r"\s+", " ", css)
    saved = len(css) - len(compact)
    if saved < need:
        raise SystemExit(f"可压缩空白不足：需要 {need} 字节，只找到 {saved} 字节")

    if saved > need:                      # 压多了，用空格补回差额
        pad = saved - need
        compact = compact[:len("<style>")] + " " * pad + compact[len("<style>"):]

    patched = (injected[:start] + compact + injected[end:]).encode("utf-8")
    if len(patched) != len(html):
        raise SystemExit(f"长度未保持一致：{len(html)} -> {len(patched)}，已中止")
    return patched


def write_region(asar: Path, data_start: int, entry: dict, data: bytes) -> None:
    if len(data) != int(entry["size"]):
        raise SystemExit("写入长度与条目长度不符，已中止")
    with asar.open("r+b") as fh:
        fh.seek(data_start + int(entry["offset"]))
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())


# --------------------------------------------------------------------------
# 命令
# --------------------------------------------------------------------------

def cmd_check(asar: Path) -> int:
    data_start, entry = html_entry(asar)
    with asar.open("rb") as fh:
        html = read_entry(fh, data_start, entry)
    patched = b"quota-status.js" in html
    backup = BACKUP_DIR / "index.html.orig"

    log(f"app.asar      : {asar}")
    log(f"界面补丁      : {'已注入' if patched else '未注入'}")
    log(f"index.html    : {len(html)} 字节，偏移 {entry['offset']}")
    log(f"备份          : {backup} ({'存在' if backup.is_file() else '无'})")
    try:
        with asar.open("r+b"):
            log("可写          : 是")
    except OSError as exc:
        log(f"可写          : 否（{exc}）")
    return 0


def cmd_apply(asar: Path, dry_run: bool, relaunch: bool) -> int:
    data_start, entry = html_entry(asar)
    with asar.open("rb") as fh:
        original = read_entry(fh, data_start, entry)

    patched = build_loader_html(original)
    if patched == original:
        log("已经注入过了，无需重复操作（要重来请先 --restore）")
        return 0

    log(f"index.html    : {len(original)} 字节，改写后 {len(patched)} 字节（等长）")
    log(f"注入片段      : {LOADER[:60]}…（{len(LOADER)} 字节）")

    if dry_run:
        log("dry-run 通过：长度一致、偏移不变。没有写入任何东西。")
        return 0

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup = BACKUP_DIR / "index.html.orig"
    if not backup.is_file() or backup.read_bytes() != original:
        backup.write_bytes(original)
        log(f"备份原始 index.html -> {backup}")
    (BACKUP_DIR / "index.html.meta.json").write_text(
        json.dumps({"asar": str(asar), "offset": int(entry["offset"]),
                    "size": int(entry["size"])}, ensure_ascii=False, indent=2),
        encoding="utf-8")

    write_region(asar, data_start, entry, patched)
    DISABLED_MARKER.unlink(missing_ok=True)

    # 读回校验
    with asar.open("rb") as fh:
        check = read_entry(fh, data_start, entry)
    if check != patched:
        raise SystemExit("写回校验失败！请立刻运行 --restore")
    log("已写入并通过读回校验。")

    log()
    log("注意：状态条是在页面加载时注入的，当前这个窗口已经加载完了，")
    log("所以需要重启 ZCode（或重新加载窗口）才能看到。")
    if relaunch:
        exe = asar.parent.parent / "ZCode.exe"
        if exe.is_file():
            os.startfile(str(exe))
            log("已启动 ZCode")
    return 0


def cmd_restore(asar: Path) -> int:
    backup = BACKUP_DIR / "index.html.orig"
    if not backup.is_file():
        raise SystemExit(f"没有找到备份 {backup}")
    original = backup.read_bytes()

    data_start, entry = html_entry(asar)
    if int(entry["size"]) != len(original):
        raise SystemExit(
            f"备份 {len(original)} 字节，当前 index.html {entry['size']} 字节，"
            "ZCode 可能已升级，拒绝还原以免破坏文件。")

    write_region(asar, data_start, entry, original)
    with asar.open("rb") as fh:
        if read_entry(fh, data_start, entry) != original:
            raise SystemExit("还原校验失败")

    # 打上标记，避免 --ensure 在下次 ZCode 启动时又把补丁加回来
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    DISABLED_MARKER.write_text("restored\n", encoding="utf-8")
    REPATCH_STATE.unlink(missing_ok=True)
    log("已还原到原始 index.html（重启 ZCode 后状态条消失）")
    log("已设置 .patch-disabled 标记，自动重打不会把它加回来；")
    log("想恢复自动注入请重新运行 --apply。")
    return 0


def cmd_ensure(asar: Path) -> int:
    """给 SessionStart 钩子用：补丁不在就自动补上（ZCode 升级后自愈）。

    要求 stdout 干净（钩子输出会被当作 JSON 解析），所以日志写文件。
    """
    global _QUIET
    _QUIET = True

    if os.environ.get("API_QUOTA_AUTOPATCH", "1") == "0":
        return 0
    if DISABLED_MARKER.is_file():
        return 0

    try:
        data_start, entry = html_entry(asar)
        with asar.open("rb") as fh:
            original = read_entry(fh, data_start, entry)
        if b"quota-status.js" in original:
            return 0

        patched = build_loader_html(original)
        if patched == original:
            return 0

        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        backup = BACKUP_DIR / "index.html.orig"
        if not backup.is_file() or backup.read_bytes() != original:
            backup.write_bytes(original)
        (BACKUP_DIR / "index.html.meta.json").write_text(
            json.dumps({"asar": str(asar), "offset": int(entry["offset"]),
                        "size": int(entry["size"])}, ensure_ascii=False, indent=2),
            encoding="utf-8")

        write_region(asar, data_start, entry, patched)
        with asar.open("rb") as fh:
            if read_entry(fh, data_start, entry) != patched:
                log("写回校验失败，已放弃")
                flush_log("patch.log")
                return 1

        REPATCH_STATE.write_text(
            json.dumps({"at": datetime.now().astimezone().isoformat(timespec="seconds")}),
            encoding="utf-8")
        log("检测到界面补丁缺失（多半是 ZCode 升级过），已自动重新注入")
        flush_log("patch.log")
        return 0
    except Exception as exc:
        log(f"自动注入失败：{exc}")
        flush_log("patch.log")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="给 ZCode 注入 api-quota 状态条")
    parser.add_argument("--asar", help="app.asar 路径（默认自动探测）")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="查看状态")
    group.add_argument("--apply", action="store_true", help="打补丁")
    group.add_argument("--restore", action="store_true", help="还原")
    group.add_argument("--dry-run", action="store_true", help="只校验，不写入")
    group.add_argument("--ensure", action="store_true",
                       help="补丁缺失就自动补上（钩子用，输出静默）")
    parser.add_argument("--relaunch", action="store_true", help="打完后自动启动 ZCode")
    args = parser.parse_args()

    asar = find_asar(args.asar)
    if args.check:
        return cmd_check(asar)
    if args.restore:
        return cmd_restore(asar)
    if args.ensure:
        return cmd_ensure(asar)
    return cmd_apply(asar, dry_run=args.dry_run, relaunch=args.relaunch)


if __name__ == "__main__":
    raise SystemExit(main())
