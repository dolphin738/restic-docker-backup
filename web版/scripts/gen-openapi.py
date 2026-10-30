#!/usr/bin/env python3
"""离线导出后端 OpenAPI 契约（候选 5：契约单一事实来源）。

为什么要它：前端 ``src/api/types.ts`` 此前是**手写第二份**契约（97 个类型），与后端
pydantic 模型（107 个 schema）各自维护——改一端忘另一端只会在运行时炸。本脚本把
FastAPI 的 OpenAPI 文档**离线**导出为 JSON（不启服务、不连库），再由
``openapi-typescript`` 生成 ``src/api/schema.d.ts``，让前端类型从后端推导。

用法（在 web版/ 下，用 backend 的虚拟环境运行）：
    backend/.venv/Scripts/python.exe scripts/gen-openapi.py        # Windows
    backend/.venv/bin/python          scripts/gen-openapi.py       # Linux/WSL
    pnpm -C frontend gen:api                                       # 生成 TS 类型

产物（二者都应入库，供漂移校验比对）：
    frontend/src/api/openapi.json
    frontend/src/api/schema.d.ts   （由 gen:api 生成）

退出码：0 = 成功；非 0 = 导入或导出失败。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # web版/
BACKEND = ROOT / "backend"
OUT = ROOT / "frontend" / "src" / "api" / "openapi.json"


def main() -> int:
    sys.path.insert(0, str(BACKEND))

    from app.main import create_app  # noqa: PLC0415 - 需先注入 sys.path

    spec = create_app().openapi()
    payload = json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(payload, encoding="utf-8", newline="\n")

    print(f"已写入 {OUT.relative_to(ROOT)}")
    print(f"  paths={len(spec.get('paths', {}))}  schemas={len(spec['components']['schemas'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
