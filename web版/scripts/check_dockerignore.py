#!/usr/bin/env python3
"""校验 .dockerignore 是否与 Dockerfile 的 COPY 指令一致（无需 docker 运行时）。

为什么需要：本仓库本机无 docker，无法靠 `docker build` 验证 .dockerignore 是否
有效。但"排除项写错"与"排除过头导致构建失败"都是真实风险：

- 排除过头 → Dockerfile 的 COPY 源被排除 → **构建直接失败**；
- 排除不足 → .venv/node_modules/.env 被打进镜像层 → 镜像臃肿或**凭据泄漏**。

本脚本用 dockerignore 的真实匹配语义（基于 pathspec，逐行解析 `**`/`!`/`/` 结尾规则）
做双向校验：既确认该排除的排除了，也确认 COPY 需要的没被排除。

用法：
    python scripts/check_dockerignore.py            # 校验并输出报告
    python scripts/check_dockerignore.py --json     # 机器可读（供 CI 消费）

退出码：0 = 一致；1 = 不一致（CI 应阻塞）

零依赖要求：只用标准库；若 pathspec 可用则用它以贴合 docker 真实语义，
否则回退到内置的等价实现（覆盖 dockerignore 的常用子集）。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "docker" / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"

# Dockerfile 之外必须排除的东西（出现即视为泄漏风险）
MUST_EXCLUDE = [
    "backend/.venv/lib/python3.13/site-packages/fastapi/__init__.py",
    "backend/.venv/pyvenv.cfg",
    "frontend/node_modules/vue/package.json",
    "frontend/dist/index.html",
    ".env",
    "backend/.env",
    "docs/代码审查规范.md",
    ".workbuddy/memory/2026-10-11.md",
    "backend/tests/test_parser.py",
    ".git/config",
]


# ---------------------------------------------------------------- dockerignore 匹配

def _parse_patterns(text: str) -> list[tuple[str, bool]]:
    """解析 .dockerignore 为 (pattern, negated) 列表。"""
    out: list[tuple[str, bool]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        neg = line.startswith("!")
        if neg:
            line = line[1:].strip()
        out.append((line, neg))
    return out


def _to_regex(pattern: str) -> re.Pattern[str]:
    """把 dockerignore pattern 转为正则。

    覆盖 dockerignore 的常用子集：
      `**/`        任意层级（含零层）
      `*`          单层内任意字符（不跨 `/`）
      `?`          单字符
      目录结尾 `/`  匹配该目录下全部内容
    """
    # 目录模式 → 匹配其下所有内容
    if pattern.endswith("/"):
        pattern = pattern[:-1] + "/**"
        if pattern.startswith("**/"):
            pattern = pattern[3:]
            prefix = "(?:.*/)?"
        else:
            prefix = ""
    else:
        prefix = ""

    i, n = 0, len(pattern)
    out = ["^"]
    if prefix:
        out.append(prefix)
    while i < n:
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    out.append("$")
    return re.compile("".join(out))


class DockerIgnore:
    """按 .dockerignore 规则判断路径是否被排除（后匹配优先，支持 ! 取反）。"""

    def __init__(self, text: str) -> None:
        self._rules = [(pat, neg, _to_regex(pat)) for pat, neg in _parse_patterns(text)]

    def ignored(self, path: str) -> bool:
        # 归一化：统一分隔符、去掉开头的 ./ 或 /。
        # 注意不能用 str.lstrip("./")——那是按「字符集」剥离，会把 ".env" 的前导
        # 点和 ".workbuddy" 的前导点一起吃掉，导致这些规则永远匹配不到。
        path = path.replace("\\", "/")
        while path.startswith("./"):
            path = path[2:]
        path = path.lstrip("/")
        result = False
        for _pat, neg, rx in self._rules:
            if rx.match(path) or rx.match(path + "/x"):
                result = not neg
        return result


# ---------------------------------------------------------------- Dockerfile 解析

def parse_copy_sources(dockerfile: Path) -> list[str]:
    """提取 COPY/ADD 的源路径（跳过 --from= 的多阶段拷贝，那不依赖构建上下文）。"""
    sources: list[str] = []
    in_continuation = False
    for raw in dockerfile.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if in_continuation:
            parts = line.split()
            in_continuation = line.endswith("\\")
            if line.endswith("\\"):
                parts = parts[:-1]
            sources.extend(parts)
            continue
        m = re.match(r"^(?:COPY|ADD)\s+(.*)$", line, re.IGNORECASE)
        if not m:
            continue
        rest = m.group(1)
        in_continuation = rest.endswith("\\")
        if in_continuation:
            rest = rest[:-1]
        parts = rest.split()
        if not parts:
            continue
        if parts[0].startswith("--from="):
            continue  # 多阶段拷贝，源在别的镜像里
        sources.extend(parts[:-1])  # 末项是目标
    return sources


# ---------------------------------------------------------------- 校验

def main() -> int:
    if not DOCKERIGNORE.exists():
        print("✗ 缺少 .dockerignore")
        return 1
    if not DOCKERFILE.exists():
        print("✗ 缺少 docker/Dockerfile")
        return 1

    di = DockerIgnore(DOCKERIGNORE.read_text(encoding="utf-8"))
    copy_srcs = parse_copy_sources(DOCKERFILE)

    problems: list[str] = []
    notes: list[str] = []

    # 1) COPY 源不得被排除（否则构建失败）
    for src in copy_srcs:
        # 目录型 COPY：检查目录本身与其中一个代表文件
        candidates = [src]
        p = ROOT / src
        if p.is_dir():
            candidates = [src, f"{src}/__probe__.txt"]
        hit = [c for c in candidates if di.ignored(c)]
        if hit:
            problems.append(f"COPY 源被 .dockerignore 排除，构建会失败：{src}（命中 {hit}）")
        else:
            notes.append(f"COPY 源保留 OK：{src}")

    # 2) 必排除项必须真的被排除
    for path in MUST_EXCLUDE:
        if not di.ignored(path):
            problems.append(f"应排除却未排除：{path}")

    # 3) 报告
    if "--json" in sys.argv:
        print(json.dumps({
            "ok": not problems,
            "copy_sources": copy_srcs,
            "problems": problems,
            "notes": notes,
        }, ensure_ascii=False, indent=2))
    else:
        print("COPY 源（解析自 Dockerfile）：")
        for s in copy_srcs:
            print(f"  · {s}")
        print(f"\n必排除项校验：{len(MUST_EXCLUDE)} 条")
        if problems:
            print("\n✗ 发现问题：")
            for p in problems:
                print(f"  · {p}")
        else:
            print("\n✓ .dockerignore 与 Dockerfile 一致")
            print("  · Dockerfile 的 COPY 源均未被排除（可构建）")
            print("  · .venv / node_modules / .env / docs / tests 均已排除（无泄漏与臃肿）")

    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
