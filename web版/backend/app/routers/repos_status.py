"""仓库状态 / 密码 / 重建 / 密钥域（E30 + E42/E43 + E47）。

候选 3 五域拆分第 5 域：从 ``routers/repos.py`` 原样搬出，
端点路径 / 响应模型 / 审计动作 / 声明顺序全部不变。

域内 4 个端点 + 3 个私有辅助：
- ``set_repo_password``（写密码文件，force 需先证明仓库打不开）
- ``rebuild_repo``（清空重建，经 RepoLifecycleService）
- ``repo_status``（E30，按 repo_id 走的 TTL 缓存探测）
- ``repo_keys``（E47 只读密钥身份）

私有辅助 ``_probe_repo_status`` / ``_read_capacity`` / ``_looks_locked`` /
``_as_optional_int`` 随域搬来（仅本域使用）。

**测试注意**：``test_api/test_repo_password.py`` 会 monkeypatch 本模块的
``_probe_repo_status``——搬域后 patch 目标必须改到本模块，否则会打空。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request

from app.auth.dependencies import require_session
from app.auth.session import SessionContext
from app.constants import (
    AUDIT_REPO_PASSWORD_SET,
    KEY_OPERATION_TIMEOUT_SECONDS,
    REPO_PROBE_TIMEOUT_SECONDS,
    REPO_STATS_TIMEOUT_SECONDS,
)
from app.db.repositories.repo_repo import ReposRepo
from app.errors import RepoNotFoundError, ValidationError
from app.routers.repos_shared import PROTECTED, validate_repo_id
from app.schemas.api_schemas import (
    RepoCapacity,
    RepoKeyOut,
    RepoKeysOut,
    RepoPasswordRequest,
    RepoPasswordResponse,
    RepoStatusResponse,
)
from app.services.env_sync import passfile_path
from app.services.repo_keys import classify_keys, parse_key_list
from app.services.repo_status_cache import (
    invalidate_repo_status_cache,
    read_cached,
    write_cached,
)
from app.services.repo_status_cache import (
    status_lock as _status_lock,
)
from app.services.restic import ResticCmd, WhitelistArgs
from app.utils.passfile import generate_repo_password, write_passfile

logger = logging.getLogger(__name__)

# 锁残留关键词（从 cat config 的失败输出里识别，零额外 restic 调用）
_LOCK_KEYWORDS: tuple[str, ...] = ("already locked", "unable to create lock", "remove stale locks")

_PROTECTED = PROTECTED
_validate_repo_id = validate_repo_id


def register(router: APIRouter) -> None:
    """把本域端点挂到聚合器（调用顺序 = 路由声明顺序）。"""
    @router.post(
        "/repos/{repo_id}/password",
        response_model=RepoPasswordResponse,
        summary="R-023 设置仓库密码（仅密码文件缺失时）",
        dependencies=_PROTECTED,
    )
    async def set_repo_password(
        repo_id: str,
        payload: RepoPasswordRequest,
        request: Request,
        session: SessionContext = Depends(require_session),
    ) -> RepoPasswordResponse:
        """写入仓库密码文件（``<passfile_root>/<repo_id>/password``，0600 原子写）。

        - ``password`` 为空 → 后端生成强随机密码（长度 ``REPO_PASSWORD_LENGTH``，与前端
          「生成强随机密码」同口径）并**一次性回显**（不落库、不落日志、不进审计）；
        - ``password`` 非空 → 按用户自填写入（响应不回显，用户已知）；
        - ``force`` → 文件已存在时也允许覆盖，但**必须先证明仓库当前打不开**
          （密码错误 / 未初始化 / 凭据无效……）。

        默认**只在文件不存在时允许**：该密码既是 restic 仓库密钥，也是 R-025 凭据加密的密钥源
        （HKDF info 混入 repo_id）——覆盖已存在的文件会让仓库不可读、已加密凭据全部失效。

        force 的放行条件（自救通道，2026-10-20）：密码文件存在但仓库打不开时，用户既删不掉
        default 仓库、旧密码又无从找回，界面上没有任何自救路径——故在此开一条「先探测后覆盖」
        的窄门；仓库能正常打开（密码正确）时仍拒绝，更换密码必须走 restic key passwd（轮换）。
        """
        del session
        _validate_repo_id(repo_id)
        repos_repo: ReposRepo = request.app.state.repos_repo
        repo = await repos_repo.get(repo_id)
        if repo is None:
            raise RepoNotFoundError(repo_id)

        path = passfile_path(request.app.state.settings, repo_id)
        forced = False
        if path.exists():
            if not payload.force:
                raise ValidationError(
                    f"仓库密码文件已存在（{path}）——密码既是仓库密钥也是凭据加密密钥源，"
                    "覆盖会使仓库不可读且已加密凭据失效；如确需更换，请走「密钥轮换」，"
                    "或在仓库已打不开（密码错误）时点「重设密码」覆盖"
                )
            # 覆盖前置门禁：仓库此刻能用现有密码打开 → 绝不允许覆盖（覆盖＝自毁仓库）
            status = await _probe_repo_status(request, repo_id)
            if status.connected:
                raise ValidationError(
                    "仓库当前可正常打开（现有密码正确）——覆盖密码会让仓库不可读。"
                    "若要更换密码，请走「密钥轮换」（restic key passwd）"
                )
            forced = True

        generated = not (payload.password or "").strip()
        plain = generate_repo_password() if generated else (payload.password or "")

        try:
            write_passfile(path, plain)
        except OSError as exc:
            raise ValidationError(
                f"密码文件写入失败（只读挂载或权限不足）：{path}；请在宿主机对应挂载目录手动放置该密码文件"
            ) from exc

        # 审计只记动作与是否生成/是否覆盖，明文绝不入审计/日志（红线 S3）
        await request.app.state.audit.record(
            AUDIT_REPO_PASSWORD_SET,
            target=repo_id,
            detail={"generated": generated, "forced": forced},
            result="ok",
        )
        invalidate_repo_status_cache()
        return RepoPasswordResponse(
            ok=True,
            generated=generated,
            password=plain if generated else None,
            path=str(path),
        )

    @router.post(
        "/repos/{repo_id}/rebuild",
        response_model=RepoPasswordResponse,
        summary="重建仓库（清空快照并用新密码重新初始化；仅本地 FS 后端）",
        dependencies=_PROTECTED,
    )
    async def rebuild_repo(
        repo_id: str,
        payload: RepoPasswordRequest,
        request: Request,
        session: SessionContext = Depends(require_session),
    ) -> RepoPasswordResponse:
        """忘密码 / 文件夹被旧仓库卡住时的自救通道：清空仓库全部快照、用新密码重新初始化。

        - 原为 default 专属（default 不可删、忘密码无出路）；2026-10-21 放开到**全部仓库**：
          删除仓库不触碰磁盘数据，同址新建会撞上打不开的旧仓库（报「密码错误」），
          非默认仓库此前没有任何恢复路径（「删除后换 URL 新建」同址即死循环）。
        - 仅支持本地文件系统后端（url 为本地绝对路径或 ``local:`` 前缀）；远程后端（s3/rest/sftp…）
          返回 422，提示手动清空后端存储后删除仓库、换 URL 新建（旧数据本就不可恢复）。
        - 流程：清空仓库目录内容（保留目录本身）→ 写新密码文件 → restic init——**清空必须
          先于覆盖密码文件**（清空失败 = 零损失，旧密码仍在；见函数体内注释）；
          运行中的任务会被 409 拒绝。
        - 仓库密码是凭据加密密钥源（HKDF info 混入 repo_id）：重建后该仓库的旧 enc:v2
          凭据无法用旧密钥解密，一并清除（需重新录入）；明文凭据不受影响。
        """
        del session
        _validate_repo_id(repo_id)
        # 跨介质序列（清空目录 → 写密码 → restic init → 清 enc:v2 凭据）交给编排器
        # （候选 1 批次 ③）：顺序契约（清空先于覆盖密码）与并发互斥都在那边。
        report = await request.app.state.repo_lifecycle.rebuild(
            repo_id, password=payload.password or None
        )
        return RepoPasswordResponse(
            ok=True,
            generated=report.generated,
            password=report.password,
            path=report.path,
        )


    # 注：``generate_repo_password`` / ``write_passfile`` / ``_PASSWORD_CLASSES`` 已搬到
    # ``utils/passfile.py``（候选 1 批次 ③）——编排器在 services 层需要它们，
    # 而 services 不能反向 import routers。路由与编排器共用同一份实现。

    @router.get(
        "/repos/{repo_id}/status",
        response_model=RepoStatusResponse,
        summary="E30 仓库状态（per-repo）",
        dependencies=_PROTECTED,
    )
    async def repo_status(
        repo_id: str,
        request: Request,
        session: SessionContext = Depends(require_session),
    ) -> RepoStatusResponse:
        """仓库状态卡数据源：``{connected, locked, capacity, last_error}``（按 repo_id 缓存）。"""
        del session
        cached = read_cached(repo_id)
        if cached is not None:
            return cached

        async with _status_lock:
            cached = read_cached(repo_id)  # 双检：等锁期间可能已被别的请求刷新
            if cached is not None:
                return cached
            status = await _probe_repo_status(request, repo_id)
            write_cached(repo_id, status)
            return status


    @router.get(
        "/repos/{repo_id}/keys",
        response_model=RepoKeysOut,
        summary="仓库密钥身份（只读；keys/ 是仓库唯一的明文文件）",
        dependencies=_PROTECTED,
    )
    async def repo_keys(
        repo_id: str,
        request: Request,
        session: SessionContext = Depends(require_session),
    ) -> RepoKeysOut:
        """``restic key list --json`` 的中性化视图（**不缓存**：安全判定不吃 TTL）。

        用途：确认 init 后的身份中性化是否生效、发现历史遗留的真实身份 key、识别多 key
        接入。只返回展示白名单字段（`id`/`id_full`/`user`/`host`/`created`/`created_tz`/
        `current`/`neutral`）——``data``（master key 密文）与 ``salt/N/r/p``（KDF 参数）
        一律不进响应。

        失败不抛 5xx：地址未配置 / 未初始化 / 锁残留都回 ``overall=unknown`` +
        ``probe_error``（与 E30 只读探测的既有口径一致，UI 必须显示原因而非空白）。
        """
        del session
        _validate_repo_id(repo_id)
        repos_repo: ReposRepo = request.app.state.repos_repo
        repo = await repos_repo.get(repo_id)
        if repo is None:
            raise RepoNotFoundError(repo_id)
        if not repo.url:
            return RepoKeysOut(repo_id=repo_id, probe_error="仓库地址未配置（请先填写仓库地址）")

        restic: ResticCmd = request.app.state.restic
        probe = await restic.probe(
            "key",
            repo_url=repo.url,
            repo_id=repo_id,
            args=WhitelistArgs(json_output=True, key_target="list"),
            timeout_seconds=KEY_OPERATION_TIMEOUT_SECONDS,
        )
        if probe.exit_code != 0:
            return RepoKeysOut(
                repo_id=repo_id,
                probe_error=probe.reason(KEY_OPERATION_TIMEOUT_SECONDS),
            )

        # restic 的 created 是按子进程 TZ 渲染的无时区字符串（build_env 注入 restic.tz
        # = config.timezone → settings.tz 回退），原样透出会与用户本地时间差出偏移且
        # 无从判断 → 把该 TZ 一并标注（不换算，C8）
        overall, items = classify_keys(
            parse_key_list(probe.lines), created_tz=request.app.state.restic.tz
        )
        return RepoKeysOut(
            repo_id=repo_id,
            overall=overall,
            keys=[RepoKeyOut.model_validate(item) for item in items],
            # 成功但读不到 key：仓库可能尚未初始化（空仓库上 key list 的行为随版本而异）
            probe_error=None if items else "未读取到任何密钥（仓库可能尚未初始化）",
        )


async def _probe_repo_status(request: Request, repo_id: str) -> RepoStatusResponse:
    """执行一次仓库探测（E30 的未缓存实现）。"""
    restic: ResticCmd = request.app.state.restic
    parser = request.app.state.parser
    repos_repo: ReposRepo = request.app.state.repos_repo

    repo = await repos_repo.get(repo_id)
    if repo is None:
        raise RepoNotFoundError(repo_id)
    if not repo.url:
        return RepoStatusResponse(
            configured=False, connected=False, locked=False, last_error="仓库地址未配置（请先填写仓库地址）"
        )

    probe = await restic.probe(
        "cat",
        repo_url=repo.url,
        repo_id=repo_id,
        args=WhitelistArgs(json_output=False, extra_positionals=("config",)),
        timeout_seconds=REPO_PROBE_TIMEOUT_SECONDS,
    )

    if probe.exit_code != 0:
        return RepoStatusResponse(
            connected=False,
            locked=_looks_locked(probe.lines),
            last_error=probe.reason(REPO_PROBE_TIMEOUT_SECONDS),
            failure_kind=probe.kind(),
        )

    return RepoStatusResponse(
        connected=True,
        locked=False,
        capacity=await _read_capacity(restic, parser, repo_url=repo.url, repo_id=repo_id),
    )


async def _read_capacity(
    restic: ResticCmd, parser: Any, *, repo_url: str, repo_id: str
) -> RepoCapacity:
    """``restic stats --mode raw-data --json`` 读取仓库容量（失败返回空容量，不影响 connected）。"""
    probe = await restic.probe(
        "stats",
        repo_url=repo_url,
        repo_id=repo_id,
        args=WhitelistArgs(json_output=True, stats_mode="raw-data"),
        timeout_seconds=REPO_STATS_TIMEOUT_SECONDS,
    )
    if probe.exit_code != 0:
        return RepoCapacity()
    blob = parser.parse_json_blob("\n".join(probe.lines))
    if not isinstance(blob, dict):
        return RepoCapacity()
    return RepoCapacity(
        size=_as_optional_int(blob.get("total_size")),
        snaps=_as_optional_int(blob.get("snapshots_count")),
    )


def _looks_locked(lines: list[str]) -> bool:
    """从探测失败输出里识别锁残留（避免为锁状态再打一次仓库）。"""
    joined = "\n".join(lines).lower()
    return any(keyword in joined for keyword in _LOCK_KEYWORDS)


def _as_optional_int(value: Any) -> int | None:
    """把 restic JSON 字段安全地转为可选整数。"""
    return int(value) if isinstance(value, int | float) else None
