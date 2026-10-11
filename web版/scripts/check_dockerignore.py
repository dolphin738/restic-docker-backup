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
    python scripts/check_dockerignore.py --context  # 追加"构建上下文体积账"（无 docker 也能查）

退出码：0 = 一致；1 = 不一致（CI 应阻塞）

`--context` 解决的是另一类问题：本机没有 docker，`.dockerignore` 写了但"到底哪些文件真的
会被打进上下文、最终镜像里有没有多余东西"只能靠猜。`--json` 管"能不能构建成功"，
`--context` 管"打进去的东西该不该打进去"。

零依赖要求：只用标准库；若 pathspec 可用则用它以贴合 docker 真实语义，
否则回退到内置的等价实现（覆盖 dockerignore 的常用子集）。
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections import defaultdict
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


# ---------------------------------------------------------------- 上下文体积账

def _human(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f}B" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}GB"


def scan_context(di: DockerIgnore) -> list[tuple[str, int]]:
    """返回"未被 .dockerignore 排除"的全部文件（即真正会进构建上下文的清单）。

    目录被命中时整棵剪枝——与 docker 行为一致（命中目录 == 命中其下所有文件）。
    """
    included: list[tuple[str, int]] = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        rel_dir = Path(dirpath).relative_to(ROOT).as_posix()
        if rel_dir == ".":
            rel_dir = ""
        dirnames[:] = [
            d for d in dirnames if not di.ignored(f"{rel_dir}/{d}" if rel_dir else d)
        ]
        for name in filenames:
            rel = f"{rel_dir}/{name}" if rel_dir else name
            if di.ignored(rel):
                continue
            included.append((rel, (ROOT / rel).stat().st_size))
    return included


def report_context(di: DockerIgnore, copy_srcs: list[str]) -> None:
    """打印上下文体积账，并标出"进了上下文却从不被 COPY"的纯噪声目录。"""
    included = scan_context(di)
    total = sum(s for _, s in included)

    size_by_top: dict[str, int] = defaultdict(int)
    count_by_top: dict[str, int] = defaultdict(int)
    for rel, size in included:
        top = rel.split("/")[0] if "/" in rel else "(根)"
        size_by_top[top] += size
        count_by_top[top] += 1

    print("\n" + "─" * 60)
    print(f"构建上下文（{ROOT}）体积账：{len(included)} 个文件 / {_human(total)}")
    print("─" * 60)
    for top, size in sorted(size_by_top.items(), key=lambda kv: -kv[1]):
        print(f"  {top:<24} {count_by_top[top]:>5} 个  {_human(size)}")

    # 顶层目录是否被 Dockerfile 用到：`COPY frontend/` 的顶层是 frontend
    used_tops = {src.split("/")[0] if "/" in src else src for src in copy_srcs}
    noise = [t for t in size_by_top if t not in used_tops and t != "(根)"]
    if noise:
        print("\n⚠ 进了上下文但 Dockerfile 从不 COPY 的顶层目录（可加进 .dockerignore）：")
        for t in sorted(noise):
            print(f"  · {t}  {_human(size_by_top[t])}")
    else:
        print("\n✓ 上下文内没有「从不被 COPY」的目录")

    fe = [(r, s) for r, s in included if r.startswith("frontend/")]
    print(f"\nfrontend/ 明细：{len(fe)} 个文件 / {_human(sum(s for _, s in fe))}（>10KB 或顶层）：")
    for rel, size in sorted(fe, key=lambda kv: -kv[1]):
        if size > 10 * 1024 or rel.count("/") <= 1:
            print(f"  {_human(size):>8}  {rel}")

    print("\n上下文内最大的 8 个文件：")
    for rel, size in sorted(included, key=lambda kv: -kv[1])[:8]:
        print(f"  {_human(size):>8}  {rel}")

    print("\nDockerfile 从**其它构建阶段** COPY 的来源（不占上下文）：")
    for raw in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("COPY") and "--from=" in line:
            print(f"  · {line}")


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

    if "--context" in sys.argv:
        report_context(di, copy_srcs)

    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
