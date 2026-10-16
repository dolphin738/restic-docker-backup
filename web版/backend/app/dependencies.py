"""跨路由共享依赖：当前仓库解析（R-023 / P3-C）。

E6 裁决的迁移形态：P2 起**新端点**一律 repo 作用域（路径前缀，E34–E36 先例）；
R-023 把**既有隐式端点**统一迁移时选「显式 ``repo_id`` 请求头」而非全量改路径——
存量前端调用点与测试零签名变更，单点注入（前端 http 层统一带头）。

- 头名 ``X-Repo-Id``；缺失/为空 = ``default``（存量部署行为不变）；
- 值必须是**已存在**的仓库 id，否则 404 ``REPO_NOT_FOUND``（防切换器脏值）；
- 与路径前缀两种形态并存不冲突：``/api/repos/{repo_id}/backup-tasks`` 的路由
  自己校验存在性（同一 :class:`ReposRepo`），本依赖只服务「隐式端点」。
"""

from __future__ import annotations

from fastapi import Request

from app.db.repositories.repo_repo import DEFAULT_REPO_ID, ReposRepo
from app.errors import RepoNotFoundError

REPO_HEADER = "x-repo-id"


async def current_repo_id(request: Request) -> str:
    """解析当前仓库 id（``X-Repo-Id`` 头 → 缺省 ``default`` → 存在性校验）。

    Raises:
        RepoNotFoundError: 404 REPO_NOT_FOUND（仓库不存在，与 E34–E36 同码）。
    """
    repo_id = (request.headers.get(REPO_HEADER) or "").strip() or DEFAULT_REPO_ID
    repos_repo: ReposRepo = request.app.state.repos_repo
    if not await repos_repo.exists(repo_id):
        raise RepoNotFoundError(repo_id)
    return repo_id
