"""仓库路由**聚合器**（R-023 / P3-C）——候选 3 五域拆分后的唯一入口。

四个业务域（各自 ``register(router)``，调用顺序 = 路由声明顺序）：
1. ``repos_crud``   仓库 CRUD + 归档页仓库侧（E39–E41）
2. ``repos_ops``    连通性测试（E29）+ 仓库用量（E41 usage）
3. ``repos_rclone`` rclone 远端与 conf 段管理（E45 + E48–E51）
4. ``repos_status`` 仓库状态（E30）+ 密码/重建/密钥（E42/E43/E47）

共享件在 :mod:`app.routers.repos_shared`（标识校验/生成、Repo→RepoOut、URL 校验）。

**本模块只做聚合**，不含端点实现——新增端点请加到对应域。

顺序是契约：``/repos/_new_id`` 必须先于 ``/repos/{repo_id}``（见 repos_crud 文件头）。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from app.auth.dependencies import require_bootstrap_done, require_session
from app.routers import repos_crud as crud
from app.routers import repos_ops as ops
from app.routers import repos_rclone as rclone
from app.routers import repos_status as status

logger = logging.getLogger(__name__)


# 聚合器自身挂一层鉴权依赖（S4 双保险）；各域端点另有自己的 _PROTECTED。
router = APIRouter(
    prefix="/api",
    tags=["repos"],
    dependencies=[Depends(require_session), Depends(require_bootstrap_done)],
)

# 候选 3 拆分：各域经 register(router) 挂端点，调用顺序 = 路由声明顺序。
# 顺序是契约：_new_id 必须先于 {repo_id}（见 repos_crud 文件头）。
# 校验拆分是否生效请用 app.openapi()["paths"]——app.routes 里看不出惰性 include。
crud.register(router)
ops.register(router)
rclone.register(router)
status.register(router)


# 本模块自候选 3 起**只做聚合**：各域各自 register，不含任何端点实现。
# 共享件在 repos_shared.py；新增端点请加到对应域，不要加回这里。
