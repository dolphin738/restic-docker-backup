"""restic 命令拼装与子进程执行（§7.1，方案 §6 注入防护）。

硬约束：**无自由字符串进 shell**——
- 命令由 ``asyncio.create_subprocess_exec`` 直接 exec（list argv，不经 shell）；
- verb 走白名单枚举，参数走 :class:`WhitelistArgs` 具名字段；
- 所有路径先经 :meth:`PathMapper.validate`，其余字符串值经 :func:`_assert_safe_value`；
- 子进程环境只注入白名单前缀变量（``RESTIC_*`` / ``AWS_*`` / ``B2_*``）与 ``TZ``。
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import math
import os
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from app.constants import (
    FAILURE_KIND_CREDENTIALS,
    FAILURE_KIND_LOCKED,
    FAILURE_KIND_NETWORK,
    FAILURE_KIND_NOT_INITIALIZED,
    FAILURE_KIND_PASSWORD,
    FAILURE_KIND_TIMEOUT,
    FAILURE_KIND_UNKNOWN,
    INPLACE_RESTORE_TARGET_ROOT,
    SUBPROCESS_LOOP_HINT,
)
from app.errors import ResticFailedError, ValidationError
from app.services.exclude_spec import validate_size
from app.services.path_mapper import PathMapper, source_allowed_prefixes
from app.settings import Settings, get_settings
from app.utils.time import tz_env

# verb 白名单（§7.1 九类 + 批次 3 为 E30 容量探测新增只读 verb ``stats``）
# B1 自愈新增 ``repair``（restic repair index / repair packs，重建索引/卷一致性）；
# B3 自愈新增 ``recover``（restic recover，找回丢失索引的快照）
VERBS: tuple[str, ...] = (
    "backup", "restore", "forget", "check", "snapshots", "ls", "unlock", "init", "cat", "stats", "tag",
    "repair", "recover", "prune",
    # C4 多目的地：copy（跨仓库复制快照）+ key（仓库密钥管理，两级子命令）
    "copy", "key",
)

# 允许注入子进程的环境变量：精确名 + 前缀（凭据走各后端官方前缀，restic 自身走 RESTIC_*）。
# A1 扩展（restic 0.19.1 源码核验的各后端官方 env 前缀）：
# - AZURE_（azure 后端 ApplyEnvironment：AZURE_ACCOUNT_NAME/KEY/SAS/ENDPOINT_SUFFIX）
# - GOOGLE_（gs 后端：GOOGLE_PROJECT_ID + Google 客户端库的 APPLICATION_CREDENTIALS/ACCESS_TOKEN）
# - RCLONE_（rclone 子进程自身读取：RCLONE_CONFIG/CONFIG_PASS/BWLIMIT/RCLONE_<REMOTE>_<OPT>）
# - SSH_（sftp 后端经系统 ssh 子进程：SSH_AUTH_SOCK 等；restic sftp 是 NoPassword，无密码 env）
# 注：rest 后端凭据是 RESTIC_REST_USERNAME/PASSWORD，已被 RESTIC_ 覆盖，无需单列前缀。
ENV_ALLOWED_EXACT: tuple[str, ...] = ("TZ",)
ENV_ALLOWED_PREFIXES: tuple[str, ...] = (
    "RESTIC_",
    "AWS_",
    "B2_",
    "AZURE_",
    "GOOGLE_",
    "RCLONE_",
    "SSH_",
)

COMPRESSION_MODES: tuple[str, ...] = ("auto", "off", "max")
OVERWRITE_MODES: tuple[str, ...] = ("if-changed", "always", "never")
# C4 密钥轮换：restic key 的子命令白名单（v0.19.1 cmd_key.go 注册顺序为
# add / list / passwd / remove）。**本项目不用 passwd**（见 key 轮换服务注释：
# 它会在我们落地新口令文件之前就删掉旧密钥）。
KEY_TARGETS: tuple[str, ...] = ("add", "list", "remove", "passwd")
# stats --mode 白名单（只读探测，用于 E30 仓库容量）
STATS_MODES: tuple[str, ...] = ("raw-data", "restore-size", "blobs-per-file", "files-per-contents")
SNAPSHOT_ID_PATTERN = frozenset("0123456789abcdef")
# A2 forget 时长档（--keep-within*）：restic duration 形态——若干个 <整数><单位> 顺接，
# 单位 y（年）/m（月）/d（日）/h（时），任意顺序（restic internal/data/duration.go
# ParseDuration：2y5m7d3h；无单位数字、负号、其余字母在 restic 端直接 Fatal）。
DURATION_PATTERN = re.compile(r"^(?:\d+[ymdh])+$")

# --read-data-subset 取值域约束（restic 0.19.1 checkFlags 口径；§7.1 V4 事实 3）
READ_DATA_SUBSET_MAX_BUCKETS = 256          # 分数形态 n/t 的 t 上限（totalBucketsMax）
READ_DATA_SUBSET_SUFFIXES = frozenset("kmgt")  # 字节大小后缀（大小写不敏感）


@dataclass(frozen=True)
class ProbeResult:
    """只读探测（E17/E18/E29/E30）的一次调用结果。

    Attributes:
        exit_code: restic 退出码；``-1`` 表示超时被杀。
        lines: 采集到的输出行（已去 CR）。
        truncated: 是否因超时或行数上限被截断（供上层置 ``truncated`` 标记）。
    """

    exit_code: int = -1
    lines: list[str] = field(default_factory=list)
    truncated: bool = False

    def kind(self) -> str:
        """把本次结果翻译成结构化失败分类（``FAILURE_KIND_*``）。

        与 :func:`failure_kind` 等价，但省掉调用方每次重复传 ``(exit_code, lines)``
        ——Q3 裁决 A 的「翻译辅助」：模块只回结果，**翻译成 kind/reason 由这里提供**，
        抛不抛仍由调用方按各自端点契约决定。
        """
        return failure_kind(self.exit_code, self.lines)

    def reason(self, timeout_seconds: float | None = None) -> str:
        """把本次结果翻译成可直接展示的原因文案。

        Args:
            timeout_seconds: 本次调用的超时秒数（退出码 -1 时用于生成超时文案）。

        Returns:
            中文原因文案（锁残留 / 凭据缺失 / 仓库未初始化 / 网络不可达 / 退出码 + 首条错误）。
        """
        return failure_reason(self.exit_code, self.lines, timeout_seconds)


@dataclass(frozen=True)
class ConsumeResult:
    """流式调用（:meth:`ResticCmd.consume`）的一次调用结果。

    只描述**机制层事实**（退出码 / 是否超时 / 是否被回调提前叫停），不解释业务含义——
    失败该不该抛、该映射成哪个 ``failure_kind``，是调用方的语义决定（Q3 裁决 A：
    模块只返回结果，不替调用方选择「失败即异常」还是「失败回结构化结果」）。

    Attributes:
        exit_code: restic 退出码；``-1`` 表示超时被杀或未能取得退出码。
        timed_out: 是否因超时被 SIGKILL。
        stopped_early: 是否因 ``on_line`` 回调返回真值而提前停止（如条目上限截断）。
    """

    exit_code: int = -1
    timed_out: bool = False
    stopped_early: bool = False


@dataclass
class WhitelistArgs:
    """restic 参数白名单：只允许具名字段，调用方无法塞入任意 flag。"""

    targets: tuple[str, ...] = ()
    snapshot_id: str | None = None
    target_dir: str | None = None
    tags: tuple[str, ...] = ()
    excludes: tuple[str, ...] = ()
    include: tuple[str, ...] = ()  # B1 文件级恢复：restore --include（容器形态，过 PathMapper 校验）
    # 批次②（设计方案 §2.2/§2.5）扩展过滤参数——**全部 backup-only**，均过 S2 argv 白名单：
    # iexcludes = --iexclude（大小写不敏感孪生，独立 flag 列表）；
    # exclude_larger_than = --exclude-larger-than（大小阈值，如 100M）；
    # exclude_if_present = --exclude-if-present（标记文件，多值各一条）；
    # exclude_caches = --exclude-caches（CACHEDIR.TAG 缓存目录开关）。
    iexcludes: tuple[str, ...] = ()
    exclude_larger_than: str | None = None
    exclude_if_present: tuple[str, ...] = ()
    exclude_caches: bool = False
    host: str | None = None
    group_by: str | None = None
    keep_last: int | None = None
    keep_hourly: int | None = None
    keep_daily: int | None = None
    keep_weekly: int | None = None
    keep_monthly: int | None = None
    keep_yearly: int | None = None
    # A2 时间线保留（Duplicati keep-time / retention-policy 分段的 restic 等价物）：
    # restic duration 形态（y/m/d/h，如 1y5m7d2h），None/空 = 未启用该档；
    # 形态校验与归一见 _normalize_duration（拼装层前移，restic 端 Fatal 无 JSON 输出）。
    keep_within: str | None = None
    keep_within_hourly: str | None = None
    keep_within_daily: str | None = None
    keep_within_weekly: str | None = None
    keep_within_monthly: str | None = None
    keep_within_yearly: str | None = None
    # A2 --keep-tag（restic TagLists：每次出现一个 tag 列表；本项目每元素一条 flag，
    # 多值间 OR 语义——命中任一的快照保留）。空 tuple = 未启用。
    keep_tags: tuple[str, ...] = ()
    # A3 快照版本标签修改（restic `tag` 子命令；Duplicati 版本标签的 restic 等价物）。
    # --add / --remove 各一条 flag（与 --tag 同款 _assert_safe_value 校验防注入）；
    # 二者均空 = 无操作（restic 端 "nothing to do!"，拼装层前移拒 422）。
    # 注意：本字段是「标签操作」，与 `tags`（forget/snapshots 的 --tag **筛选**维度）语义不同。
    tag_add: tuple[str, ...] = ()
    tag_remove: tuple[str, ...] = ()
    one_file_system: bool = False
    compression: str | None = None
    limit_up_kibps: int = 0
    limit_down_kibps: int = 0
    json_output: bool = True
    dry_run: bool = False
    verbose: bool = False
    read_data: bool = False
    # R-017：check --read-data-subset（三形态：n/t 分数、x% 比例、字节大小）。
    # 形态校验前移到本层（拼装侧）——非法值 restic 端直接 Fatal 且零 JSON 产出，
    # 执行层没有任何兜底机会（§7.1 V4 事实 3）。
    read_data_subset: str | None = None
    overwrite: str | None = None
    stats_mode: str | None = None
    # R-015 原位恢复：--target 取 INPLACE_RESTORE_TARGET_ROOT（"/"）的**显式放行开关**。
    # 调用方只能给布尔值，取什么值由 ResticCmd 决定——不是自由字符串通道（S2）。
    inplace_target: bool = False
    # R-016 forget：--prune 随 forget 回收数据（预览恒 dry_run=True，不落 prune）
    prune: bool = False
    extra_positionals: tuple[str, ...] = field(default_factory=tuple)
    # B1 自愈：restic repair 的子命令（"index" / "packs"），经白名单校验——
    # repair 是两级命令（restic repair index），子命令走具名字段而非自由位置参数（S2）。
    repair_target: str | None = None
    # C4 密钥轮换：restic key 的两级子命令（add / list / remove / passwd），与
    # repair 同款——子命令是白名单枚举值，不是自由位置参数（S2）。
    key_target: str | None = None
    # key remove 的位置参数（key id），格式校验与快照 id 同为 restic.ID。
    key_id: str | None = None
    # key add / key passwd 的 --new-password-file（**路径**，不是口令本身——
    # 口令绝不进 argv，S3）。
    new_password_file: str | None = None
    # 中性化 key（init 后抹去 keys/ 文件的明文身份元数据）：``key add`` 的
    # ``--user`` / ``--host``。值经 :func:`_assert_safe_value`（防 flag 注入），
    # 仅 ``key add`` 拼装（passwd / remove 无此语义）。
    key_user: str | None = None
    key_host: str | None = None
    # ``--key-hint``（全局 flag）：强制优先尝试指定 key 解密，使「当前 key」可控——
    # 中性化时把当前 key 切到新 key，才能删掉旧 key（restic deleteKey 拒绝删当前 key）。
    key_hint: str | None = None


class ResticProcess:
    """restic 子进程句柄：stdout/stderr 逐行 tee 到任务日志文件。"""

    def __init__(self, proc: asyncio.subprocess.Process, log_path: Path) -> None:
        """构造进程句柄。

        Args:
            proc: asyncio 子进程对象（stdout=PIPE，stderr 合并进 stdout）。
            log_path: 任务日志文件路径（tee 目标，同时是 SSE 回放底座）。
        """
        self._proc = proc
        self.log_path = log_path

    @property
    def pid(self) -> int | None:
        """子进程 PID。"""
        return self._proc.pid

    @property
    def returncode(self) -> int | None:
        """退出码；进程仍在运行时为 None。"""
        return self._proc.returncode

    async def lines(self) -> AsyncIterator[str]:
        """逐行产出子进程输出，并同步 tee 追加写入日志文件。

        Yields:
            去掉行尾换行符的单行文本（UTF-8 解码，非法字节以替换符处理）。

        Notes:
            该方法同时承担"读"与"落盘"两件事，调用方必须完整消费迭代器，
            否则子进程 stdout 缓冲区写满会阻塞（SSE 侧应读取日志文件而非本迭代器）。
        """
        log_path = self.log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("ab") as sink:
            stream = self._proc.stdout
            if stream is None:
                return
            buffer = b""
            async for chunk in stream:
                sink.write(chunk)
                sink.flush()
                buffer += chunk
                if b"\n" not in buffer:
                    continue
                # 按字节换行切分：跨 chunk 的半行留在 buffer，保证"文件行数 == 事件 id"
                *complete, buffer = buffer.split(b"\n")
                for raw in complete:
                    yield _decode(raw)
            if buffer.strip():
                yield _decode(buffer)

    async def wait(self) -> int:
        """等待进程结束并返回退出码。"""
        return await self._proc.wait()

    def terminate(self) -> None:
        """发送 SIGTERM（优雅退出）。"""
        if self._proc.returncode is None:
            self._proc.terminate()

    def kill(self) -> None:
        """发送 SIGKILL（宽限期超时后的强制终止）。"""
        if self._proc.returncode is None:
            self._proc.kill()


# 凭据来源适配器：给定 ``repo_id``，返回待注入子进程的环境变量（键须落在各后端官方
# 前缀内，最终仍由 :meth:`ResticCmd.build_env` 的白名单二次把关）。
# 由装配方注入（``main.lifespan`` 绑定 ``app.services.env_sync.load_credentials``），
# 使本模块**不 import** 配置仓储与凭据服务——Q2 裁决 A：注入适配器，不用函数 import。
CredentialSource = Callable[[str], Awaitable[dict[str, str]]]


class ResticCmd:
    """restic 命令拼装（白名单）与 spawn（tee 日志）。"""

    def __init__(
        self,
        settings: Settings | None = None,
        path_mapper: PathMapper | None = None,
        *,
        credentials: CredentialSource | None = None,
    ) -> None:
        """构造命令器。

        Args:
            settings: 运行期配置（二进制路径、缓存目录、密码文件路径）。
            path_mapper: 路径映射器（校验所有进入命令的路径）。
            credentials: 凭据来源适配器（见 :data:`CredentialSource`）。只由
                :meth:`probe` 使用；未注入时调用 :meth:`probe` 会**显式报错**——
                宁可炸在装配错误上，也不让凭据静默缺席后表现为「仓库未初始化」。
        """
        self._settings = settings or get_settings()
        self._path_mapper = path_mapper or PathMapper(self._settings)
        self._credentials = credentials
        # restic 子进程 TZ（E39 统一口径：config.timezone 为单一事实源，
        # settings.tz 仅作启动回退）——运行期经 set_timezone 刷新，
        # 否则用户改 UI 时区后子进程仍按旧 TZ 渲染文本输出。
        self.tz: str = self._settings.tz

    def set_timezone(self, tz_name: str | None) -> None:
        """刷新子进程 TZ（UI 时区变更即时生效；空值回落 settings.tz）。

        与 scheduler.reload() 同模式：put_config 命中 ``timezone`` 键时调用，
        失败不吞配置写入（下次重启仍按新时区重建）。
        """
        name = (tz_name or "").strip()
        self.tz = name or self._settings.tz

    def build(
        self,
        verb: str,
        *,
        args: WhitelistArgs,
        env_extras: dict[str, str] | None = None,
    ) -> list[str]:
        """按白名单拼装 argv（顺序：全局 flag → verb → verb flag → 位置参数）。

        Args:
            verb: restic 子命令，必须落在 :data:`VERBS` 内。
            args: 具名参数白名单。
            env_extras: 附加环境变量（仅用于白名单校验预热，实际注入见 :meth:`build_env`）。

        Returns:
            可直接交给 ``create_subprocess_exec`` 的 argv 列表。

        Raises:
            ValidationError: verb 不在白名单、参数值非法或路径越界。
        """
        if verb not in VERBS:
            raise ValidationError(f"不支持的 restic 子命令：{verb}", data={"verb": verb, "allowed": list(VERBS)})
        self._sanitize_env(env_extras)

        argv: list[str] = [self._settings.restic_binary]
        argv.extend(self._global_flags(args))
        argv.append(verb)
        argv.extend(self._verb_flags(verb, args))
        if verb in ("backup", "restore", "ls", "snapshots", "cat", "tag"):
            argv.extend(self._positionals(verb, args))
        return argv

    def build_env(
        self,
        *,
        repo_url: str,
        repo_id: str = "default",
        env_extras: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """构造子进程环境变量：宿主环境 + restic 必需项 + 白名单凭据 + TZ。

        Args:
            repo_url: 仓库地址，注入 ``RESTIC_REPOSITORY``。
            repo_id: 仓库标识（R-023 多仓库：密码文件按仓库派生；缺省 ``default``）。
            env_extras: 界面配置的后端凭据（A1 起含 AZURE_/GOOGLE_/RCLONE_/SSH_/RESTIC_REST_*；
                越界前缀由 :data:`ENV_ALLOWED_PREFIXES` 拦截，见 :meth:`_env_allowed`）。

        Returns:
            子进程环境字典。
        """
        env = dict(os.environ)
        env["RESTIC_REPOSITORY"] = repo_url
        # E5 per-repo 布局（唯一实现点 Settings.passfile_path）；RESTIC_PASSWORD_FILE 覆盖优先
        env["RESTIC_PASSWORD_FILE"] = str(self._settings.passfile_path(repo_id))
        env["RESTIC_CACHE_DIR"] = self._settings.cache_dir
        # E39：TZ 取 self.tz（config.timezone → settings.tz 回退，可运行期刷新），
        # 不再直读 settings.tz——否则 UI 改时区对 restic 文本输出不生效。
        env.update(tz_env(self.tz))
        for key, value in (env_extras or {}).items():
            if self._env_allowed(key):
                env[key] = str(value)
        return env

    async def spawn(self, argv: list[str], log_path: str | Path, *, env: dict[str, str] | None = None) -> ResticProcess:
        """启动 restic 子进程（不经 shell），并准备日志文件。

        Args:
            argv: :meth:`build` 产出的参数列表。
            log_path: 任务日志文件路径（父目录自动创建，文件先 touch）。
            env: 子进程环境；缺省继承当前进程环境。

        Returns:
            :class:`ResticProcess` 句柄。
        """
        path = Path(log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )
        except NotImplementedError as exc:
            # Windows + uvicorn --reload：SelectorEventLoop 不支持 create_subprocess_exec
            # （2026-10-19 真机实证：裸 --reload 启动时全部 restic 操作 500）。转成
            # ResticFailedError（原因明示，禁止静默）而非裸 NotImplementedError 500。
            raise ResticFailedError(SUBPROCESS_LOOP_HINT) from exc
        return ResticProcess(proc, path)

    async def credential_extras(self, repo_id: str) -> dict[str, str]:
        """取指定仓库的附加环境变量（凭据，经注入的适配器）。

        单独暴露是为了让「一次取凭据、多次调用」的编排（init 后跑 key 操作、
        同一仓库连跑 cat config + stats）不必每步都重读配置表——它是
        :meth:`env_for` / :meth:`probe` / :meth:`consume` 的共同前缀。

        Args:
            repo_id: 仓库标识（凭据命名空间）。

        Returns:
            待注入的环境变量字典（键已落在各后端官方前缀内）。

        Raises:
            RuntimeError: 未注入凭据适配器（装配错误，禁止静默降级为"无凭据"）。
        """
        if self._credentials is None:
            raise RuntimeError(
                "ResticCmd 未注入凭据来源适配器，无法读取凭据；"
                "请在装配处传入 credentials=（见 main.lifespan）"
            )
        return await self._credentials(repo_id)

    async def env_for(self, *, repo_url: str, repo_id: str) -> dict[str, str]:
        """组装一次调用的子进程环境（凭据 + 仓库地址 + 密码文件 + TZ）。

        这是「env 复用型」调用的接缝：调用方随后可以自己 :meth:`spawn` 长任务、
        或把 env 交给需要多次调用同一仓库的服务（快照目录计数、密钥轮换），
        而不必各自重复 ``await load_credentials(...)`` + ``build_env(...)`` 两行。

        Args:
            repo_url: 仓库地址（``repos.url``）。
            repo_id: 仓库标识（凭据命名空间与密码文件路径都用它）。

        Returns:
            可直接传给 ``create_subprocess_exec`` 的环境字典。

        Raises:
            RuntimeError: 未注入凭据适配器。
        """
        env_extras = await self.credential_extras(repo_id)
        return self.build_env(repo_url=repo_url, repo_id=repo_id, env_extras=env_extras)

    async def probe(
        self,
        verb: str,
        *,
        repo_url: str,
        repo_id: str,
        args: WhitelistArgs | None = None,
        timeout_seconds: float,
    ) -> ProbeResult:
        """执行一次只读探测调用：凭据 → 环境 → argv → 收完输出。

        这是「探测型调用」的机制接缝（Q1 裁决 B 的探测档）：把此前在路由里逐字重复的
        三行——``build_env(... load_credentials ...)``、``build(...)``、``run_json(...)``
        ——收成一次调用。凭据经注入的 :data:`CredentialSource` 取得，本模块因此不必
        import 配置仓储与凭据服务（Q2 裁决 A）。

        **只返回结果、不抛业务异常**（Q3 裁决 A）：失败该映射成结构化 ``failure_reason``
        还是抛 :class:`ResticFailedError`，由调用方按各自端点契约决定——探测端点
        （E29/E30）必须 200 + 结构化原因，长操作必须抛。

        Args:
            verb: restic 子命令（须在 :data:`VERBS` 内）。
            repo_url: 仓库地址（``repos.url``）。
            repo_id: 仓库标识（凭据命名空间与密码文件路径都用它）。
            args: 具名参数白名单；缺省 :class:`WhitelistArgs`（``json_output=True``）。
            timeout_seconds: 超时秒数；超时 SIGKILL 并返回 ``exit_code=-1``。

        Returns:
            :class:`ProbeResult`（退出码 / 输出行 / 是否截断）。

        Raises:
            RuntimeError: 未注入凭据适配器（装配错误，禁止静默降级为"无凭据"）。
            ValidationError: verb 或参数未过白名单（由 :meth:`build` 抛出）。
        """
        env_extras = await self.credential_extras(repo_id)
        argv = self.build(verb, args=args if args is not None else WhitelistArgs(), env_extras=env_extras)
        env = self.build_env(repo_url=repo_url, repo_id=repo_id, env_extras=env_extras)
        return await self.run_json(argv, env=env, timeout_seconds=timeout_seconds)

    async def run_json(
        self,
        argv: list[str],
        *,
        env: dict[str, str],
        timeout_seconds: float,
        max_lines: int = 200_000,
    ) -> ProbeResult:
        """执行一次带超时与行数上限的 restic 只读调用（E17/E18/E29/E30 探测用）。

        与 :meth:`spawn` 的区别：本方法**同步收完**全部输出并返回 :class:`ProbeResult`，
        适合输出规模可控的探测类命令；长任务（backup/restore）必须走 :meth:`spawn`
        + 逐行消费，避免内存无界。

        Args:
            argv: :meth:`build` 产出的参数列表。
            env: 子进程环境。
            timeout_seconds: 超时秒数；超时后 SIGKILL 并返回退出码 -1。
            max_lines: 采集行数上限，超出即停止读取并 kill（防止探测端点被大仓库拖垮）。

        Returns:
            :class:`ProbeResult`（退出码 / 输出行 / 是否被截断）。
        """
        log_path = self._settings.jobs_dir / f"probe-{uuid4().hex}.log"
        try:
            proc = await self.spawn(argv, log_path, env=env)
        except OSError as exc:
            # restic 二进制缺失/不可执行时，探测端点（E29/E30）不能 5xx——
            # 它们的契约是"失败回结构化结果"，让 failure_reason 生成文案
            log_path.unlink(missing_ok=True)
            return ProbeResult(exit_code=-1, lines=[f"restic 启动失败：{exc}"], truncated=False)
        lines: list[str] = []
        truncated = False
        try:
            async with asyncio.timeout(timeout_seconds):
                async for line in proc.lines():
                    lines.append(line)
                    if len(lines) >= max_lines:
                        truncated = True
                        break
            if truncated:
                proc.kill()
            exit_code = await proc.wait()
        except TimeoutError:
            truncated = True
            proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()
            exit_code = -1
        finally:
            log_path.unlink(missing_ok=True)
        return ProbeResult(exit_code=exit_code, lines=lines, truncated=truncated)

    async def consume(
        self,
        argv: list[str],
        *,
        env: dict[str, str] | None = None,
        repo_url: str | None = None,
        repo_id: str | None = None,
        timeout_seconds: float,
        on_line: Callable[[str], bool | None | Awaitable[bool | None]],
        log_prefix: str = "call",
        retain_log: str | Path | None = None,
    ) -> ConsumeResult:
        """执行一次 restic 调用并逐行交给回调（流式消费的唯一入口）。

        这是「一次 restic 调用」的机制接缝：spawn、超时、kill、日志文件命名与生命周期、
        逐行解码全部收在这里，调用方只提供 ``on_line`` 回调与超时。此前 E31 dry-run
        在两个路由里各抄了一份约 56 行的等价循环（``sources.py`` 与 ``backup_tasks.py``），
        两份的超时文案、kill 时机、日志清理都要手工保持一致——本方法把那套机制
        收敛为单一实现点（Q1 裁决 B / Q4 裁决 A）。

        与 :meth:`run_json` / :meth:`spawn` 的分工：
        - :meth:`run_json`：**收完**输出再返回（探测类，输出规模可控）；
        - :meth:`consume`：**逐行**交给回调（读取过程中累积或提前停止）；
        - :meth:`spawn`：**不消费**，交给调用方自行编排（长任务 + 独立 SSE 通道）。

        Args:
            argv: :meth:`build` 产出的参数列表。
            env: 子进程环境。给定则直接使用；不给则由本方法按 ``repo_url``/``repo_id``
                经 :meth:`env_for` 组装（凭据 + 仓库地址 + 密码文件 + TZ）。
            repo_url: 仓库地址（``env`` 未给出时必需）。
            repo_id: 仓库标识（``env`` 未给出时必需）。
            timeout_seconds: 超时秒数；超时即 SIGKILL 并置 ``timed_out``。
            on_line: 每读到一行调用一次；**返回真值表示"够了"**，本方法停止读取并
                kill 子进程（返回 None/False 表示继续）。
            log_prefix: 临时日志文件名前缀。**命名权归本方法**（Q4 裁决 A）——两个
                dry-run 调用点此前的唯一差异就是 ``dryrun-`` / ``dryrun-task-`` 前缀。
            retain_log: 需要**留存**日志时给出的目标路径（如 copy 的产物留存）；
                给定时日志写在该路径并保留，不给则用 ``jobs_dir/<prefix>-<uuid>.log``
                并在结束时删除。

        Returns:
            :class:`ConsumeResult`（退出码 / 是否超时 / 是否被回调提前停止）。

        Raises:
            OSError: restic 二进制缺失或不可执行（由 :meth:`spawn` 透传；临时日志已清理）。
            ValueError: 既未给 ``env`` 又未给 ``repo_url``/``repo_id``。
            RuntimeError: 需要自建 env 但未注入凭据适配器。
        """
        if env is None:
            if repo_url is None or repo_id is None:
                raise ValueError("consume() 需要 env，或 repo_url + repo_id 以便自行组装环境")
            env = await self.env_for(repo_url=repo_url, repo_id=repo_id)
        if retain_log is not None:
            path = Path(retain_log)
        else:
            path = self._settings.jobs_dir / f"{log_prefix}-{uuid4().hex}.log"
        temp = retain_log is None

        stopped_early = False
        timed_out = False
        exit_code = -1
        try:
            proc = await self.spawn(argv, path, env=env)
        except OSError:
            if temp:
                path.unlink(missing_ok=True)
            raise
        try:
            async with asyncio.timeout(timeout_seconds):
                async for line in proc.lines():
                    verdict = on_line(line)
                    # 回调可以是同步或异步（CPU 密集的解析需自行 to_thread 后 await）
                    if inspect.isawaitable(verdict):
                        verdict = await verdict
                    if verdict:
                        # 回调宣告"够了"：停止读取并 kill，避免子进程继续写满管道后挂住
                        stopped_early = True
                        proc.kill()
                        break
            exit_code = await proc.wait()
        except TimeoutError:
            timed_out = True
            proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()
            exit_code = -1
        finally:
            if temp:
                path.unlink(missing_ok=True)
        return ConsumeResult(
            exit_code=exit_code,
            timed_out=timed_out,
            stopped_early=stopped_early,
        )

    # ---------------------------------------------------------------- 内部实现

    def _global_flags(self, args: WhitelistArgs) -> list[str]:
        """拼装全局 flag（位于 verb 之前）。"""
        flags: list[str] = []
        if args.json_output:
            flags.append("--json")
        flags.extend(["--cache-dir", self._settings.cache_dir])
        if args.limit_up_kibps and args.limit_up_kibps > 0:
            flags.extend(["--limit-upload", str(int(args.limit_up_kibps))])
        if args.limit_down_kibps and args.limit_down_kibps > 0:
            flags.extend(["--limit-download", str(int(args.limit_down_kibps))])
        if args.key_hint:
            # --key-hint 是全局选项（verb 之前）：强制 restic 优先尝试指定 key，
            # 中性化删旧 key 时必须让「当前 key」指向新 key（deleteKey 拒绝删当前 key）。
            flags.extend(["--key-hint", _assert_key_id(args.key_hint)])
        return flags

    def _verb_flags(self, verb: str, args: WhitelistArgs) -> list[str]:
        """按 verb 拼装子命令 flag（全部值经白名单/安全校验）。"""
        flags: list[str] = []
        for tag in args.tags:
            flags.extend(["--tag", _assert_safe_value(tag, "tag")])
        for exclude in args.excludes:
            flags.extend(["--exclude", _assert_safe_value(exclude, "exclude")])
        if args.host:
            flags.extend(["--host", _assert_safe_value(args.host, "host")])

        if verb == "backup":
            if args.one_file_system:
                flags.append("--one-file-system")
            if args.compression:
                if args.compression not in COMPRESSION_MODES:
                    raise ValidationError(
                        f"压缩模式非法：{args.compression}", data={"allowed": list(COMPRESSION_MODES)}
                    )
                flags.extend(["--compression", args.compression])
            if args.dry_run:
                flags.append("--dry-run")
            if args.verbose:
                flags.append("--verbose")
            # 批次② 过滤参数（backup 专属；全过 S2 argv 白名单，值经安全校验）
            for pattern in args.iexcludes:
                flags.extend(["--iexclude", _assert_safe_value(pattern, "iexclude")])
            if args.exclude_larger_than:
                flags.extend(["--exclude-larger-than", validate_size(args.exclude_larger_than)])
            for name in args.exclude_if_present:
                flags.extend(
                    ["--exclude-if-present", _assert_safe_value(name, "exclude-if-present")]
                )
            if args.exclude_caches:
                flags.append("--exclude-caches")
            return flags

        if verb == "restore":
            if args.target_dir:
                flags.extend(["--target", self._restore_target(args)])
            # B1 --include（R-027）：目录含子树、文件精确匹配；白名单前缀取当前模式根
            # （A0 模式收敛后恒为 /sources——与 sources 归一口径一致，
            # exclusions.py normalize_user_excludes 先例）。S1 校验点②（纵深防御）：
            # 路由层已 display→container 归一，此处再过一次白名单，杜绝绕过路由直拼。
            for include in args.include:
                flags.extend(
                    [
                        "--include",
                        self._path_mapper.validate(
                            include, allowed_prefixes=source_allowed_prefixes(self._settings)
                        ),
                    ]
                )
            if args.overwrite:
                if args.overwrite not in OVERWRITE_MODES:
                    raise ValidationError(
                        f"覆盖策略非法：{args.overwrite}", data={"allowed": list(OVERWRITE_MODES)}
                    )
                flags.append(f"--overwrite={args.overwrite}")
            return flags

        if verb == "forget":
            keep_pairs = (
                ("--keep-last", args.keep_last),
                ("--keep-hourly", args.keep_hourly),
                ("--keep-daily", args.keep_daily),
                ("--keep-weekly", args.keep_weekly),
                ("--keep-monthly", args.keep_monthly),
                ("--keep-yearly", args.keep_yearly),
            )
            for flag, value in keep_pairs:
                if value is not None and value >= 0:
                    flags.extend([flag, str(int(value))])
            # A2 时间线档：--keep-within*（restic duration，拼装层校验+归一；
            # 全零/空 = 未启用，不加 flag——与 keep_* 的 0 口径一致）
            within_pairs = (
                ("--keep-within", args.keep_within),
                ("--keep-within-hourly", args.keep_within_hourly),
                ("--keep-within-daily", args.keep_within_daily),
                ("--keep-within-weekly", args.keep_within_weekly),
                ("--keep-within-monthly", args.keep_within_monthly),
                ("--keep-within-yearly", args.keep_within_yearly),
            )
            for flag, value in within_pairs:
                if not value:
                    continue
                normalized = _normalize_duration(value, flag.lstrip("-"))
                if normalized:
                    flags.extend([flag, normalized])
            # A2 --keep-tag：每元素一条 flag（OR 语义）；值过 _assert_safe_value
            # 防 flag 注入（与 --tag 同款校验）
            for keep_tag in args.keep_tags:
                flags.extend(["--keep-tag", _assert_safe_value(keep_tag, "keep_tag")])
            # 空串是**合法且有意义**的取值：restic `--group-by ""` = 不分组
            # （全部快照视为同一组，keep-* 才能对整个仓库生效）。
            # 只判 None —— 用 truthiness 会把空串当"没给"而静默丢掉该 flag。
            if args.group_by is not None:
                flags.extend(["--group-by", _assert_safe_value(args.group_by, "group_by")])
            # E21 预览：--dry-run 只读（R-016 预览恒 dry_run，由路由层强制）；
            # 实际执行（E12 type=forget）经确认后才允许 --prune 回收数据。
            if args.dry_run:
                flags.append("--dry-run")
            if args.prune:
                flags.append("--prune")
            return flags

        if verb == "prune":
            # 独立回收：默认执行真实 prune（不含 dry_run）；预览端点显式传 dry_run=True。
            # prune 接受的可调参数（--max-unused / --max-repack-size / --repack-cacheable-only）
            # 本期不暴露前端，保持最小白名单面——后续要调优再开具名字段（S2）。
            if args.dry_run:
                flags.append("--dry-run")
            return flags

        if verb == "check":
            if args.read_data and args.read_data_subset:
                raise ValidationError(
                    "--read-data 与 --read-data-subset 互斥（restic check 不允许同时给出）",
                    data={"read_data": True, "read_data_subset": args.read_data_subset},
                )
            if args.read_data:
                flags.append("--read-data")
            if args.read_data_subset:
                flags.extend(
                    ["--read-data-subset", _validate_read_data_subset(args.read_data_subset)]
                )
            return flags

        if verb == "stats":
            mode = args.stats_mode or "raw-data"
            if mode not in STATS_MODES:
                raise ValidationError(f"stats 模式非法：{mode}", data={"allowed": list(STATS_MODES)})
            flags.extend(["--mode", mode])
            return flags

        if verb == "tag":
            # A3 打/去标签：--add / --remove 各一条 flag（与 --tag 同款 _assert_safe_value
            # 防 flag 注入）；restic 端约束（"nothing to do!" / "--set 与 --add/--remove 互斥"）
            # 由路由层校验前移——本层只负责把白名单字段拼成 argv，不重复判定业务语义。
            if not args.tag_add and not args.tag_remove:
                raise ValidationError(
                    "tag 操作至少需要 --add 或 --remove 之一",
                    data={"tag_add": args.tag_add, "tag_remove": args.tag_remove},
                )
            for tag in args.tag_add:
                flags.extend(["--add", _assert_safe_value(tag, "tag_add")])
            for tag in args.tag_remove:
                flags.extend(["--remove", _assert_safe_value(tag, "tag_remove")])
            return flags

        if verb == "unlock" or verb == "init" or verb == "snapshots":
            return flags

        if verb == "ls" or verb == "cat":
            return flags

        if verb == "repair":
            # B1 自愈：restic repair 是两级命令，子命令（index/packs）走具名字段而非
            # 自由位置参数——值经白名单校验，杜绝 flag 注入（S2）。repair 不产生 JSON
            # 输出，json_output 由 JobRunner 置 False（build 不加 --json）。
            target = args.repair_target or "index"
            if target not in ("index", "packs"):
                raise ValidationError(
                    "repair 子命令仅支持 index / packs", data={"repair_target": target}
                )
            flags.append(target)
            return flags

        if verb == "recover":
            # B3 自愈：restic recover 找回丢失索引的快照，无子命令、无额外 flag；
            # 输出为人类可读文本（json_output 置 False）。
            return flags

        if verb == "copy":
            # C4 多目的地：restic copy（源 = 全局仓库，目的 = 次级仓库 env）。
            # 目的仓库的 url 与口令文件**绝不进 argv**——与源仓库同口径走 env
            # （见 :func:`secondary_repo_env`），argv 上只有过滤条件（S3）。
            # 输出为人类可读文本（cmd_copy.go 硬编码 NewProgressPrinter(false, …)，
            # --json 对它无效），json_output 由调用方置 False。
            # --exclude 是 backup 专属 flag，copy 段没有它——本层显式拒，避免上层
            # 复用 WhitelistArgs 时误带进来变成 restic 端「unknown flag」。
            if args.excludes:
                raise ValidationError("copy 不接受 --exclude", data={"excludes": args.excludes})
            return flags

        if verb == "key":
            # C4 密钥轮换：restic key 是两级命令，子命令走具名字段（与 repair 同款，S2）。
            target = args.key_target
            if target not in KEY_TARGETS:
                raise ValidationError(
                    "key 子命令仅支持 add / list / remove / passwd", data={"key_target": target}
                )
            flags.append(target)
            if target in ("add", "passwd") and args.new_password_file:
                # 值是**路径**不是口令：口令本体在文件里，argv 上只有位置（S3）
                flags.extend(
                    ["--new-password-file", _assert_safe_value(args.new_password_file, "new_password_file")]
                )
            if target == "add":
                # 中性化 key：自定义新 key 的身份标注（不暴露真实机器/账号）。
                # 值过 _assert_safe_value 防 flag 注入（S2）。
                if args.key_user:
                    flags.extend(["--user", _assert_safe_value(args.key_user, "key_user")])
                if args.key_host:
                    flags.extend(["--host", _assert_safe_value(args.key_host, "key_host")])
            if target == "remove":
                # restic 端要求且仅接受 1 个 key id（cmd_key_remove.go：len(args) != 1
                # → "key remove expects one argument as the key id"），前移到本层 422。
                if not args.key_id:
                    raise ValidationError("key remove 需要 key_id", data={"key_target": target})
                flags.append(_assert_key_id(args.key_id))
            return flags

        return flags

    def _restore_target(self, args: WhitelistArgs) -> str:
        """拼装 restore 的 ``--target``（原位恢复走常量的显式放行分支）。

        常规恢复（时间戳子目录）的目标照旧过 :meth:`PathMapper.validate`
        （A0 收敛后为 ``/sources`` / ``/restore`` 白名单，S1）。

        原位恢复（``inplace_target=True``）**只能**取
        :data:`INPLACE_RESTORE_TARGET_ROOT`——restic 落点 = ``--target`` + 快照内
        绝对路径，快照存的是容器绝对路径（``/sources/etc/...``），故只有 ``/`` 是真原位
        （规范 §3.1 S7「执行层落点纪律」）。为什么这不破坏 S1/S2：

        - 取值来自 constants 单一真源，调用方传的是**布尔开关**而非路径字符串，
          不存在"借该字段塞任意路径"的自由通道（S2）；
        - 值不是用户输入，是执行层落点常量；真正的用户可控范围由**双重锁**
          （``/sources`` 前缀白名单 + 快照 paths 交集）与强制非空 ``--include`` 共同限定（S1）；
        - 开关打开但值不等于该常量时直接 422，杜绝"借布尔开关夹带别的值"。

        Raises:
            ValidationError: 开关打开但 ``target_dir`` 不是该常量（422）。
        """
        if args.inplace_target:
            if args.target_dir != INPLACE_RESTORE_TARGET_ROOT:
                raise ValidationError(
                    "原位恢复的 --target 只能取 INPLACE_RESTORE_TARGET_ROOT",
                    data={"target_dir": args.target_dir},
                )
            return INPLACE_RESTORE_TARGET_ROOT
        return self._path_mapper.validate(args.target_dir)

    def _positionals(self, verb: str, args: WhitelistArgs) -> list[str]:
        """拼装位置参数（快照 id / 备份源路径），全部经路径白名单或 id 格式校验。"""
        positionals: list[str] = []
        if args.snapshot_id:
            positionals.append(_assert_snapshot_id(args.snapshot_id))
        for target in args.targets:
            positionals.append(self._path_mapper.validate(target))
        positionals.extend(_assert_safe_value(item, "positional") for item in args.extra_positionals)
        if verb == "backup" and not positionals:
            raise ValidationError("backup 至少需要指定一个备份源路径")
        return positionals

    def _sanitize_env(self, env_extras: dict[str, str] | None) -> None:
        """校验附加环境变量键名是否落在白名单前缀内。"""
        for key in (env_extras or {}):
            if not self._env_allowed(key):
                raise ValidationError(f"环境变量 {key} 不在注入白名单内", data={"key": key})

    @staticmethod
    def _env_allowed(key: str) -> bool:
        """判断环境变量键是否允许注入子进程。"""
        return key in ENV_ALLOWED_EXACT or key.startswith(ENV_ALLOWED_PREFIXES)


# 失败原因关键词 → 可操作提示（§5.1 E29「原因明示，禁止静默」；按特异性从高到低排列）
_FAILURE_HINTS: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (
        ("already locked", "unable to create lock", "remove stale locks"),
        FAILURE_KIND_LOCKED,
        "仓库锁残留（确认无任务运行后可走解锁操作清理）",
    ),
    (
        ("wrong password", "invalid password", "incorrect password", "no key found"),
        FAILURE_KIND_PASSWORD,
        "仓库密码错误（请检查仓库密码文件挂载）",
    ),
    (
        ("does not exist", "not initialized", "unable to open config file", "no such bucket"),
        FAILURE_KIND_NOT_INITIALIZED,
        "仓库不存在或未初始化（请检查 repo.url，或先执行 init）",
    ),
    (
        ("credentials", "accessdenied", "access denied", "unauthorized", "invalidaccesskey"),
        FAILURE_KIND_CREDENTIALS,
        "对象存储凭据缺失或无效（请检查 cred.* 配置）",
    ),
    (
        ("no such host", "connection refused", "network is unreachable", "i/o timeout", "tls handshake"),
        FAILURE_KIND_NETWORK,
        "网络不可达或端点错误（请检查网络与端点地址）",
    ),
)


def failure_kind(exit_code: int, lines: list[str]) -> str:
    """把探测失败归到**结构化分类**（供 UI 判定该给哪个动作，不必匹配中文文案）。

    Returns:
        ``FAILURE_KIND_*`` 之一；无命中（含退出码 -1 超时）分别为 unknown / timeout。
    """
    if exit_code == -1:
        return FAILURE_KIND_TIMEOUT
    joined = "\n".join(lines).lower()
    for keywords, kind, _reason in _FAILURE_HINTS:
        if any(keyword in joined for keyword in keywords):
            return kind
    return FAILURE_KIND_UNKNOWN


def failure_reason(exit_code: int, lines: list[str], timeout_seconds: float | None = None) -> str:
    """把 restic 探测失败转换为**可直接展示**的原因文案（禁止静默失败）。

    Args:
        exit_code: restic 退出码（-1 表示超时被杀）。
        lines: 采集到的输出行。
        timeout_seconds: 探测超时秒数；退出码为 -1 时用于生成超时文案。

    Returns:
        中文原因文案（锁残留 / 凭据缺失 / 仓库未初始化 / 网络不可达 / 原始退出码 + 首条错误）。
    """
    if exit_code == -1:
        if timeout_seconds:
            return f"restic 探测超时（超过 {timeout_seconds:g}s）"
        return "restic 探测超时"
    joined = "\n".join(lines).lower()
    for keywords, _kind, reason in _FAILURE_HINTS:
        if any(keyword in joined for keyword in keywords):
            return reason
    detail = _first_meaningful(lines)
    if detail:
        return f"restic 退出码 {exit_code}：{detail}"
    return f"restic 退出码 {exit_code}（无输出）"


def _first_meaningful(lines: list[str]) -> str:
    """取首条可读错误描述：优先 ``exit_error`` 的 message/error 字段，其次非 JSON 行。"""
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("{"):
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                for key in ("error", "message", "msg"):
                    value = payload.get(key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()[:200]
            continue
        return stripped[:200]
    return ""


def _decode(raw: bytes) -> str:
    """解码一行字节并去掉可能的 CR 尾巴（兼容 CRLF 输出，避免污染 JSON 解析）。"""
    return raw.decode("utf-8", errors="replace").rstrip("\r")


def _assert_safe_value(value: str, field_name: str) -> str:
    """校验可作为 flag 值/位置参数的字符串（防 flag 注入与换行注入）。

    Args:
        value: 待校验字符串。
        field_name: 字段名，用于错误提示。

    Returns:
        原字符串。

    Raises:
        ValidationError: 以 ``-`` 开头（会被解析为 flag）、含 NUL 或换行。
    """
    if value.startswith("-"):
        raise ValidationError(f"{field_name} 不能以 '-' 开头（禁止注入 flag）", data={"field": field_name})
    if "\x00" in value or "\n" in value or "\r" in value:
        raise ValidationError(f"{field_name} 含非法字符", data={"field": field_name})
    return value


def _assert_snapshot_id(snapshot_id: str) -> str:
    """校验快照 id 为小写十六进制（restic 快照 id 形态，长度 8~64）。"""
    candidate = snapshot_id.strip()
    if not (8 <= len(candidate) <= 64) or not set(candidate) <= SNAPSHOT_ID_PATTERN:
        raise ValidationError("快照 id 非法（须为 8~64 位小写十六进制）", data={"snapshot_id": snapshot_id})
    return candidate


def _assert_key_id(key_id: str) -> str:
    """校验 key id（C4 密钥轮换）。

    key id 与快照 id 同为 ``restic.ID``（64 位小写十六进制，短形态 8 位起），
    故直接复用 :func:`_assert_snapshot_id` 的正则——不复制第二套格式约束。
    """
    return _assert_snapshot_id(key_id)


def secondary_repo_env(url: str, password_file: str | Path) -> dict[str, str]:
    """C4 ``copy`` 的次级仓库（**目的**仓库）env 片段——方向经源码级核验。

    v0.19.1 事实（``cmd_copy.go`` + ``internal/global/secondary_repo.go``）：

    - 次级仓库有两组写法：``--from-*``（源）与已弃用的 ``--repo2`` 组（目的）；
    - ``FillGlobalOpts`` 里 ``isFromRepo`` 为**真**时才交换 src/dst，因此走
      ``RESTIC_REPOSITORY2`` 这组时**默认方向**是：全局 ``RESTIC_REPOSITORY`` = 源、
      本函数两个变量 = 目的；
    - 两组互斥（同时给 restic 直接 Fatal），本项目只用第二组——源仓库沿用既有
      注入（``build_env``），零改动且不触碰方向语义。

    值只给**口令文件路径**而非口令本身：口令绝不进 argv（S3），与源仓库同口径。
    """
    return {"RESTIC_REPOSITORY2": url, "RESTIC_PASSWORD_FILE2": str(password_file)}


def _validate_read_data_subset(value: str) -> str:
    """校验 ``--read-data-subset`` 取值为 restic 认可的三形态之一（R-017，S2）。

    取值域（restic 0.19.1 ``checkFlags()``；非法即 Fatal、无 JSON 输出——校验必须
    前移到拼装层，执行层没有兜底机会，§7.1 V4 事实 3）：

    - ``n/t`` 分数：正整数，``n ≤ t``，``t ≤ 256``（totalBucketsMax）；
    - ``x%`` / ``x.y%`` 比例：``0 < x ≤ 100.0``；
    - 字节大小：数值 + ``k/K m/M g/G t/T`` 后缀，``> 0``。

    Args:
        value: 待校验取值。

    Returns:
        原值（去首尾空白后；形态合法时原样透传给 restic）。

    Raises:
        ValidationError: 空值或任一形态不合法。
    """
    text = (value or "").strip()
    data = {"field": "read_data_subset", "value": value}
    invalid = "read_data_subset 取值非法（支持 n/t 分数、x% 比例、字节大小如 1G）"
    if not text:
        raise ValidationError(invalid, data=data)

    # 形态①：n/t 分数
    if "/" in text:
        left, _, right = text.partition("/")
        if not (left.isdigit() and right.isdigit()):
            raise ValidationError(invalid, data=data)
        numerator, denominator = int(left), int(right)
        if numerator <= 0 or denominator <= 0 or numerator > denominator:
            raise ValidationError(invalid, data=data)
        if denominator > READ_DATA_SUBSET_MAX_BUCKETS:
            raise ValidationError(
                f"read_data_subset 分母超出上限 {READ_DATA_SUBSET_MAX_BUCKETS}", data=data
            )
        return text

    # 形态②：x% 比例（含小数）
    if text.endswith("%"):
        try:
            ratio = float(text[:-1])
        except ValueError as exc:
            raise ValidationError(invalid, data=data) from exc
        if not math.isfinite(ratio) or ratio <= 0 or ratio > 100:
            raise ValidationError(invalid, data=data)
        return text

    # 形态③：字节大小（可选 k/m/g/t 后缀，大小写不敏感）
    body = text
    if len(text) > 1 and text[-1].lower() in READ_DATA_SUBSET_SUFFIXES:
        body = text[:-1]
    try:
        size = float(body)
    except ValueError as exc:
        raise ValidationError(invalid, data=data) from exc
    if not math.isfinite(size) or size <= 0:
        raise ValidationError(invalid, data=data)
    return text


def _normalize_duration(value: str | None, field_name: str) -> str | None:
    """校验并归一 forget 时长档位取值（A2：``--keep-within*``，S2）。

    取值域（restic 0.19.1 ``data.ParseDuration``）：若干个 ``<整数><单位>`` 顺接，
    单位 ``y``/``m``/``d``/``h``（年/月/日/时），任意顺序，如 ``1y5m7d2h``。
    与 restic 的两处**刻意差异**（本项目 stricter，均在拼装层 422 前置）：

    - **重复单位拒绝**：restic 对 ``2y3y`` 后者覆盖前者（静默变 3y），语义歧义，
      直接 422 要求合并为一段；
    - **全零归一未启用**：``0d``/``0y0m`` 解析为零时长（restic 无约束力），
      归一为 ``None``（不加 flag）——与 ``keep_*`` 整数档 0 = 未启用同口径。

    Args:
        value: 待校验取值（None/空串 = 未启用）。
        field_name: 字段名（错误提示与 data 回带）。

    Returns:
        归一后的时长串（去首尾空白）；未启用返回 ``None``。

    Raises:
        ValidationError: 形态非法（无单位数字、负号、未知单位等）或重复单位。
    """
    text = (value or "").strip()
    if not text:
        return None
    data = {"field": field_name, "value": value}
    if not DURATION_PATTERN.match(text):
        raise ValidationError(
            f"{field_name} 取值非法（restic 时长格式：数字+单位顺接，单位 y/m/d/h，如 1y5m7d2h）",
            data=data,
        )
    units = [match.group(1) for match in re.finditer(r"\d+([ymdh])", text)]
    if len(set(units)) != len(units):
        raise ValidationError(
            f"{field_name} 存在重复单位（restic 会后者覆盖前者，语义歧义，请合并为一段）",
            data=data,
        )
    if all(int(match.group(1)) == 0 for match in re.finditer(r"(\d+)[ymdh]", text)):
        return None  # 全零 = 零时长 = 无约束力，按未启用处理
    return text
