"""前端手写契约与后端 schema 的**字段名**一致性门禁（候选 5 补充）。

为什么不是「直接生成替代」：实测把 types.ts 的 76 个同名类型改为生成类型别名后，
``vue-tsc`` 报 82 个错误——其中 42 个是 ``possibly 'undefined'``。根因是 FastAPI 把
**带默认值的字段一律标为 optional**（``items?:``），而响应里它们必然存在；生成类型
因此比手写类型**更宽**。全局 ``--properties-required-by-default`` 也救不了：它会把请求体
的可选字段一并变必填（如 ``force`` 的默认 false）。

所以当前策略是：**类型仍手写（精度更高），但字段名由本门禁与后端对齐**——它拦住的
正是候选 5 指出的那类事故：「后端给字段改名/删除，前端没跟上」。

比对范围：两侧同名的类型。前端自造的视图模型（Page<T> / ErrorBody / SSE 载荷等）
不参与。只比**字段名集合**，不比可选性（可选性差异是已知且可接受的生成器局限）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

FRONTEND_API = Path(__file__).resolve().parents[2] / "frontend" / "src" / "api"
TYPES_TS = FRONTEND_API / "types.ts"
SPEC = FRONTEND_API / "openapi.json"


def _frontend_fields() -> dict[str, set[str]]:
    """解析 types.ts 顶层声明 → {类型名: 字段名集合}。

    只处理 ``export interface X {`` 与 ``export type X = {``（多行对象字面量）；
    ``export type X = S['X']`` 之类的别名、以及联合类型不在此列。
    """
    lines = TYPES_TS.read_text(encoding="utf-8").splitlines()
    out: dict[str, set[str]] = {}
    current: str | None = None
    for line in lines:
        m = re.match(r"^export (interface|type) (\w+)(.*)$", line)
        if m:
            kind, name, rest = m.group(1), m.group(2), m.group(3)
            # 参与比对的声明必须「自带完整字段块」：
            #   interface X { }        → 比
            #   type X = { ... }       → 比
            #   interface X extends Y  → 跳过（字段部分继承自 Y，本解析器不解析继承链）
            #   type X = Page<Foo>     → 跳过（别名）
            usable = "extends" not in rest and (kind == "interface" or "{" in rest)
            current = name if usable else None
            if usable:
                out.setdefault(name, set())
            continue
        if current is None:
            continue
        stripped = line.strip()
        # 顶层声明的结束行一定是**列 0** 的 `}`；嵌套对象字面量的 `}` 是缩进的，
        # 不能据此结束（否则会把后续字段漏掉，曾误报 ConfigImportReport 缺字段）。
        if stripped in ("}", "};") and line == line.lstrip():
            current = None
            continue
        # 只取**直接成员**：恰好 2 空格缩进（本项目风格）；更深缩进是嵌套对象字面量，
        # 属被引类型的内部字段，不能算本类型的顶层字段。
        fm = re.match(r"^ {2}(\w+)\??:", line)
        if fm:
            out[current].add(fm.group(1))
    return {k: v for k, v in out.items() if v}


def _backend_fields() -> dict[str, set[str]]:
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    out: dict[str, set[str]] = {}
    for name, schema in spec["components"]["schemas"].items():
        props = schema.get("properties")
        if isinstance(props, dict) and props:
            out[name] = set(props)
    return out


def test_frontend_field_names_match_backend() -> None:
    """两侧同名类型的**字段名集合**必须一致（改名/删字段/新增字段都会被抓住）。"""
    fe = _frontend_fields()
    be = _backend_fields()
    shared = sorted(set(fe) & set(be))
    assert shared, "没有可比对的同名类型——检查 types.ts 与 openapi.json 是否错位"

    problems = []
    for name in shared:
        only_fe = fe[name] - be[name]
        only_be = be[name] - fe[name]
        if only_fe:
            problems.append(f"{name}: 前端多出 {sorted(only_fe)}（后端已无此字段）")
        if only_be:
            problems.append(f"{name}: 前端缺少 {sorted(only_be)}（后端已新增此字段）")

    assert not problems, (
        "前端手写契约与后端 schema 字段不一致——请同步 src/api/types.ts：\n  "
        + "\n  ".join(problems)
    )
