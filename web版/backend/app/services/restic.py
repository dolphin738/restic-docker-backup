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
import json
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from app.errors import ValidationError
from app.services.path_mapper import PathMapper
from app.settings import Settings, get_settings

# verb 白名单（§7.1 九类 + 批次 3 为 E30 容量探测新增只读 verb ``stats``）
VERBS: tuple[str, ...] = (
    "backup", "restore", "forget", "check", "snapshots", "ls", "unlock", "init", "cat", "stats",
)

# 允许注入子进程的环境变量：精确名 + 前缀（凭据走 AWS_*/B2_*，restic 自身走 RESTIC_*）
ENV_ALLOWED_EXACT: tuple[str, ...] = ("TZ",)
ENV_ALLOWED_PREFIXES: tuple[str, ...] = ("RESTIC_", "AWS_", "B2_")

COMPRESSION_MODES: tuple[str, ...] = ("auto", "off", "max")
OVERWRITE_MODES: tuple[str, ...] = ("if-changed", "always", "never")
# stats --mode 白名单（只读探测，用于 E30 仓库容量）
STATS_MODES: tuple[str, ...] = ("raw-data", "restore-size", "blobs-per-file", "files-per-contents")
SNAPSHOT_ID_PATTERN = frozenset("0123456789abcdef")


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


@dataclass
class WhitelistArgs:
    """restic 参数白名单：只允许具名字段，调用方无法塞入任意 flag。"""

    targets: tuple[str, ...] = ()
    snapshot_id: str | None = None
    target_dir: str | None = None
    tags: tuple[str, ...] = ()
    excludes: tuple[str, ...] = ()
    host: str | None = None
    group_by: str | None = None
    keep_last: int | None = None
    keep_hourly: int | None = None
    keep_daily: int | None = None
    keep_weekly: int | None = None
    keep_monthly: int | None = None
    keep_yearly: int | None = None
    one_file_system: bool = False
    compression: str | None = None
    limit_up_kibps: int = 0
    limit_down_kibps: int = 0
    json_output: bool = True
    dry_run: bool = False
    verbose: bool = False
    read_data: bool = False
    overwrite: str | None = None
    stats_mode: str | None = None
    extra_positionals: tuple[str, ...] = field(default_factory=tuple)


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


class ResticCmd:
    """restic 命令拼装（白名单）与 spawn（tee 日志）。"""

    def __init__(self, settings: Settings | None = None, path_mapper: PathMapper | None = None) -> None:
        """构造命令器。

        Args:
            settings: 运行期配置（二进制路径、缓存目录、密码文件路径）。
            path_mapper: 路径映射器（校验所有进入命令的路径）。
        """
        self._settings = settings or get_settings()
        self._path_mapper = path_mapper or PathMapper(self._settings)

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
        if verb in ("backup", "restore", "ls", "snapshots", "cat"):
            argv.extend(self._positionals(verb, args))
        return argv

    def build_env(self, *, repo_url: str, env_extras: dict[str, str] | None = None) -> dict[str, str]:
        """构造子进程环境变量：宿主环境 + restic 必需项 + 白名单凭据 + TZ。

        Args:
            repo_url: 仓库地址，注入 ``RESTIC_REPOSITORY``。
            env_extras: 界面配置的后端凭据（``AWS_*`` / ``B2_*``）。

        Returns:
            子进程环境字典。
        """
        env = dict(os.environ)
        env["RESTIC_REPOSITORY"] = repo_url
        env["RESTIC_PASSWORD_FILE"] = self._settings.passfile
        env["RESTIC_CACHE_DIR"] = self._settings.cache_dir
        if self._settings.tz:
            env["TZ"] = self._settings.tz
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
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        return ResticProcess(proc, path)

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
        proc = await self.spawn(argv, log_path, env=env)
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
            return flags

        if verb == "restore":
            if args.target_dir:
                flags.extend(["--target", self._path_mapper.validate(args.target_dir)])
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
            if args.group_by:
                flags.extend(["--group-by", _assert_safe_value(args.group_by, "group_by")])
            return flags

        if verb == "check":
            if args.read_data:
                flags.append("--read-data")
            return flags

        if verb == "stats":
            mode = args.stats_mode or "raw-data"
            if mode not in STATS_MODES:
                raise ValidationError(f"stats 模式非法：{mode}", data={"allowed": list(STATS_MODES)})
            flags.extend(["--mode", mode])
            return flags

        if verb == "unlock" or verb == "init" or verb == "snapshots":
            return flags

        if verb == "ls" or verb == "cat":
            return flags
        return flags

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
_FAILURE_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("already locked", "unable to create lock", "remove stale locks"),
        "仓库锁残留（确认无任务运行后可走解锁操作清理）",
    ),
    (
        ("wrong password", "invalid password", "incorrect password", "no key found"),
        "仓库密码错误（请检查仓库密码文件挂载）",
    ),
    (
        ("does not exist", "not initialized", "unable to open config file", "no such bucket"),
        "仓库不存在或未初始化（请检查 repo.url，或先执行 init）",
    ),
    (
        ("credentials", "accessdenied", "access denied", "unauthorized", "invalidaccesskey"),
        "对象存储凭据缺失或无效（请检查 cred.* 配置）",
    ),
    (
        ("no such host", "connection refused", "network is unreachable", "i/o timeout", "tls handshake"),
        "网络不可达或端点错误（请检查网络与端点地址）",
    ),
)


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
    for keywords, reason in _FAILURE_HINTS:
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
