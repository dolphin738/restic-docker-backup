"""API 契约单一事实来源的漂移门禁（候选 5）。

背景：前端 ``src/api/types.ts`` 曾经是**手写第二份**契约（97 个类型），与后端 pydantic
模型（107 个 schema）各自维护——改一端忘另一端只会在运行时炸。本文件把契约钉在
后端推导链上：

    pydantic 模型  ->  openapi.json（scripts/gen-openapi.py 离线导出，入库）
                   ->  schema.d.ts（frontend，pnpm -C frontend gen:api 生成，入库）

两条断言：
1. ``openapi.json`` 必须与当前代码**逐字一致**（改模型没重导出 = 失败）；
2. ``schema.d.ts`` 必须**覆盖全部** schema 名（生成后忘了提交 = 失败）。

第 2 条不比对类型细节（那由 ``vue-tsc`` 在构建时兜底），只做名字覆盖的廉价检查——
它的目的不是替代生成器，而是让「忘了重新生成」这件事在 pytest 里立刻可见。

重新生成：
    backend/.venv/Scripts/python.exe scripts/gen-openapi.py    # Windows
    backend/.venv/bin/python          scripts/gen-openapi.py    # Linux/WSL
    pnpm -C frontend gen:api
"""

from __future__ import annotations

import json
from pathlib import Path

FRONTEND_API = Path(__file__).resolve().parents[2] / "frontend" / "src" / "api"
OPENAPI_JSON = FRONTEND_API / "openapi.json"
SCHEMA_DTS = FRONTEND_API / "schema.d.ts"


def _current_spec() -> dict:
    """用当前代码现算 OpenAPI（不读磁盘上的 openapi.json）。"""
    from app.main import create_app

    return create_app().openapi()


def test_openapi_json_matches_current_code() -> None:
    """openapi.json 必须与当前 pydantic 模型一致（改了模型就必须重导出）。"""
    assert OPENAPI_JSON.is_file(), (
        f"缺少 {OPENAPI_JSON.relative_to(OPENAPI_JSON.parents[3])}；"
        "请运行 scripts/gen-openapi.py"
    )
    on_disk = json.loads(OPENAPI_JSON.read_text(encoding="utf-8"))
    current = _current_spec()

    # 按解析后的结构比较（键序无关），只报出差异位置，避免整份 JSON 打屏
    if on_disk != current:
        old_schemas = set(on_disk.get("components", {}).get("schemas", {}))
        new_schemas = set(current.get("components", {}).get("schemas", {}))
        old_paths = set(on_disk.get("paths", {}))
        new_paths = set(current.get("paths", {}))
        details = []
        if new_schemas - old_schemas:
            details.append(f"新增 schema（未重导出）：{sorted(new_schemas - old_schemas)}")
        if old_schemas - new_schemas:
            details.append(f"删除 schema（未重导出）：{sorted(old_schemas - new_schemas)}")
        if new_paths - old_paths:
            details.append(f"新增端点（未重导出）：{sorted(new_paths - old_paths)}")
        if old_paths - new_paths:
            details.append(f"删除端点（未重导出）：{sorted(old_paths - new_paths)}")
        if not details:
            details.append("schema/端点集合一致但字段有变（模型字段被改动）")
        raise AssertionError(
            "openapi.json 落后于代码——请重新生成：\n"
            "  backend/.venv/Scripts/python.exe scripts/gen-openapi.py\n"
            + "\n".join(details)
        )


def test_schema_dts_covers_all_backend_schemas() -> None:
    """schema.d.ts 必须覆盖后端全部 schema 名（生成后忘了提交 = 失败）。"""
    assert SCHEMA_DTS.is_file(), (
        f"缺少 {SCHEMA_DTS.relative_to(SCHEMA_DTS.parents[3])}；"
        "请运行 pnpm -C frontend gen:api"
    )
    dts = SCHEMA_DTS.read_text(encoding="utf-8")
    schemas = sorted(_current_spec()["components"]["schemas"])
    missing = [name for name in schemas if f"{name}:" not in dts and f'"{name}":' not in dts]
    assert not missing, (
        "schema.d.ts 未覆盖以下后端 schema（生成后未提交？）：\n"
        f"  {missing}\n请运行 pnpm -C frontend gen:api"
    )
