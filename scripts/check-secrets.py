#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""提交前 / CI 用的密钥扫描。

用法：
    python scripts/check-secrets.py            # 扫描 git 已跟踪的文件
    python scripts/check-secrets.py --staged   # 只扫暂存区（pre-commit 钩子用）
    python scripts/check-secrets.py --all      # 扫工作区所有文件（含未跟踪）

发现疑似密钥时退出码为 1，并打印文件、行号和打码后的命中片段。
这些规则只做"兜底"：真正的保证是不把密钥写进任何文件。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SKIP_DIRS = {".git", "__pycache__", "backup", "node_modules", ".venv", "venv", ".idea", ".vscode"}
SKIP_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz", ".exe", ".dll",
    ".pyc", ".pyo", ".woff", ".woff2", ".ttf", ".otf", ".mp4", ".webp",
}

PATTERNS = [
    ("OpenAI / DeepSeek 风格 key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("Anthropic 风格 key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b")),
    ("Bearer 字面量", re.compile(r"[Bb]earer\s+[A-Za-z0-9\-._~+/]{24,}")),
    ("硬编码的密钥赋值", re.compile(
        r"(?i)\b(api[_-]?key|apikey|secret|access[_-]?token|auth[_-]?token|password)\b"
        r"\s*[:=]\s*[\"'][^\"'\s]{16,}[\"']")),
    ("私钥文件头", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]


def mask(text: str) -> str:
    text = text.strip()
    if len(text) <= 12:
        return "*" * len(text)
    return f"{text[:6]}…{text[-2:]}（已打码）"


def git(*args: str) -> list[str]:
    try:
        out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                             timeout=60).stdout.decode("utf-8", "replace")
    except Exception:
        return []
    return [line for line in out.splitlines() if line.strip()]


def collect(mode: str) -> list[Path]:
    if mode == "staged":
        names = git("diff", "--cached", "--name-only", "--diff-filter=ACM")
    elif mode == "tracked":
        names = git("ls-files")
    else:
        names = [str(p.relative_to(ROOT)).replace("\\", "/")
                 for p in ROOT.rglob("*") if p.is_file()]
    return [ROOT / n for n in names]


def should_skip(path: Path) -> bool:
    try:
        rel = path.relative_to(ROOT)
    except ValueError:
        return True
    if any(part in SKIP_DIRS for part in rel.parts):
        return True
    if path.suffix.lower() in SKIP_SUFFIXES:
        return True
    return not path.is_file()


def scan(paths: list[Path]) -> int:
    findings: list[str] = []
    checked = 0
    for path in paths:
        if should_skip(path):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        checked += 1
        for lineno, line in enumerate(text.splitlines(), 1):
            for label, pattern in PATTERNS:
                hit = pattern.search(line)
                if hit:
                    rel = path.relative_to(ROOT)
                    findings.append(f"{rel}:{lineno}  [{label}]  {mask(hit.group(0))}")

    if findings:
        print("发现疑似密钥，已阻止：")
        for item in findings:
            print("  " + item)
        print()
        print("如果是误报（例如文档里的示例），把该行改成明显的占位符，")
        print("或在本脚本的 PATTERNS 里调整规则。")
        return 1

    print(f"密钥扫描通过：检查了 {checked} 个文件，没有发现疑似密钥。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="扫描仓库里的疑似密钥")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--staged", action="store_true", help="只扫暂存区")
    group.add_argument("--all", action="store_true", help="扫工作区所有文件")
    args = parser.parse_args()

    mode = "staged" if args.staged else ("all" if args.all else "tracked")
    return scan(collect(mode))


if __name__ == "__main__":
    raise SystemExit(main())
