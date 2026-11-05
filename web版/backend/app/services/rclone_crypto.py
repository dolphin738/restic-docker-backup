"""rclone conf 加密口令生命周期（方案 5.10，P2 服务层）。

- status 纯读 conf 文件头；明文路径绝不调用 rclone。已加密时用现有口令文件
  跑 config encryption check，口令文件缺失也照常让 rclone 快速失败（本地不读它）。
- enable / rekey / disable 的口令只经 mkstemp 临时文件传给 --password-command；
  不经 shell，口令不进 argv、日志、审计、异常消息。
- 写后必重跑 check；失败从备份还原 conf，成功才原子落盘口令并重建
  RCLONE_PASSWORD_COMMAND（disable 成功则移除）。
- 本服务层不写审计：调用方须按 constants.AUDIT_RCLONE_CONF_ENCRYPT、
  constants.AUDIT_RCLONE_CONF_REKEY、constants.AUDIT_RCLONE_CONF_DECRYPT 埋点。
- 并发门禁（有 running job → 409 constants.JOB_CONFLICT）由 P5 路由层负责；
  本层不查 DB（A1）。
"""

from __future__ import annotations

import os
import re
import secrets
import shlex
import tempfile
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from app.constants import RCLONE_CONFIG_TIMEOUT_SECONDS
from app.errors import AppError, RcloneUnavailableError, ValidationError
from app.services.rclone import conf_encrypted, exec_rclone, resolve_conf_path
from app.settings import Settings
from app.utils.time import now_utc

type RcloneExecutor = Callable[[Sequence[str]], Awaitable[tuple[int, str]]]

_PASSWORD_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
_PASSWORD_COMMAND_ENV = "RCLONE_PASSWORD_COMMAND"
_TAG_RE = re.compile(r"[a-z0-9-]+")


@dataclass(frozen=True)
class ConfEncryptionStatus:
    """conf 加密三态；路径为字符串，便于直接进 API 响应。"""

    conf_encrypted: bool
    conf_locked: bool
    password_present: bool
    conf_path: str
    pass_path: str


def _temp_file(
    paths: list[Path],
    *,
    prefix: str,
    data: bytes,
    mode: int = 0o600,
    suffix: str = ".tmp",
    directory: Path | None = None,
) -> Path:
    """创建并登记临时文件；调用方的 finally 统一 _cleanup。"""
    fd, name = tempfile.mkstemp(prefix=prefix, suffix=suffix, dir=directory)
    path = Path(name)
    paths.append(path)
    os.close(fd)
    path.write_bytes(data)
    path.chmod(mode)
    return path


def _cleanup(paths: list[Path]) -> None:
    """删除本次操作登记的全部临时口令 / 脚本文件（成功与失败路径都调用）。"""
    for path in paths:
        with suppress(OSError):
            path.unlink()


def _password_script_path(tag: str | None) -> Path:
    """口令命令脚本的固定路径；TMPDIR 含空白时退回 /tmp（命令值不得含空白）。"""
    name = "restic-web-rclone-pass" + (f"-{tag}" if tag else "") + ".sh"
    script = Path(tempfile.gettempdir()) / name
    if " " in str(script) or "\t" in str(script):
        script = Path("/tmp") / name
    return script


def _script_dir() -> Path:
    """临时脚本目录：与固定口令脚本同目录，保证路径不含空白。"""
    return _password_script_path(None).parent


def _write_password_script(body: str, tag: str | None) -> str:
    """原子落盘 0700 脚本并返回其路径；脚本内容只有路径，绝不含口令。"""
    script = _password_script_path(tag)
    # 临时文件与目标同目录：跨文件系统的 os.replace 会抛 EXDEV（TMPDIR 含空白时
    # 会退回 /tmp，此时 mkstemp 的默认位置与目标不同盘）。
    fd, tmp = tempfile.mkstemp(prefix="rclone-passcmd-", suffix=".sh", dir=script.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
        os.chmod(tmp, 0o700)
        os.replace(tmp, script)  # 原子替换，避免并发读到半截
    except BaseException:
        with suppress(OSError):
            os.unlink(tmp)
        raise
    return str(script)


def password_command_for(pass_path: Path | str, tag: str | None = None) -> str:
    """返回 RCLONE_PASSWORD_COMMAND 的值：无空格脚本路径。

    rclone 按空白切分 --password-command 后直接 exec（不认 shell 引号），故命令值
    本身不能含空格。落一个固定路径的脚本，真实口令路径写在脚本里并 shlex.quote。
    tag 让同一次操作里并存多个口令文件（只允许 [a-z0-9-]）；缺省用同一路径。
    """
    if tag is not None and not _TAG_RE.fullmatch(tag):
        raise ValueError(f"脚本 tag 只允许 [a-z0-9-]：{tag!r}")
    body = "#!/bin/sh\nexec /bin/cat " + shlex.quote(str(pass_path)) + "\n"
    return _write_password_script(body, tag)


class RcloneCryptoService:
    """conf 加密口令服务；executor 可注入以便单测拦截 argv。

    默认执行器 app.services.rclone.exec_rclone 用 create_subprocess_exec
    （不经 shell）、stdin=DEVNULL 和 conf 写超时；RcloneProbe 与
    RcloneConfigService 不传 env=，故进程 env 重建后后续
    restic serve restic --stdio 子进程自动继承。
    """

    def __init__(self, settings: Settings, executor: RcloneExecutor | None = None) -> None:
        """构造服务；conf 路径复用 resolve_conf_path 单一实现点。"""
        self._conf_path = Path(resolve_conf_path(settings))
        self._pass_path = Path(settings.passfile_root) / "rclone.pass"
        self._executor: RcloneExecutor = executor or (
            lambda argv: exec_rclone(argv, RCLONE_CONFIG_TIMEOUT_SECONDS)
        )

    async def status(self) -> ConfEncryptionStatus:
        """读文件头 + 已加密时跑 check；口令文件缺失只由 rclone 快速判失败。

        口令文件缺失时脚本里的 cat 快速失败、status 仍返回 locked；本地不读
        口令文件，故不会抛 FileNotFoundError。缺 rclone 二进制 / 超时 / 事件
        循环不支持子进程时保守记为 locked，让面板仍能打开；P3 识别层再区分
        基础设施不可用与真正锁死。
        """
        encrypted = self._header_encrypted()
        password_present = self._pass_path.is_file()
        locked = False
        if encrypted:
            temps: list[Path] = []
            try:
                script = _temp_file(
                    temps,
                    prefix="rclone-check-",
                    data=("#!/bin/sh\n/bin/cat " + shlex.quote(str(self._pass_path)) + "\n").encode(),
                    mode=0o700,
                    suffix=".sh",
                    directory=_script_dir(),
                )
                code = await self._run(self._encryption_argv("check", str(script)))
            except (RcloneUnavailableError, OSError):
                code = 1
            finally:
                _cleanup(temps)
            if code == 2:
                encrypted = False
                locked = False
            else:
                locked = code != 0 or not password_present
        return ConfEncryptionStatus(
            conf_encrypted=encrypted,
            conf_locked=locked,
            password_present=password_present,
            conf_path=str(self._conf_path),
            pass_path=str(self._pass_path),
        )

    async def enable(self, new_password: str | None = None) -> str:
        """仅未加密可用；生成 / 落盘口令、验通后一次性返回明文。

        任何一步失败都从 conf 同目录备份还原，口令文件回到操作前状态，
        且不改变运行期 env；备份文件作为逃生通道保留。
        """
        if self._header_encrypted():
            raise ValidationError("rclone.conf 已加密，不能重复启用加密")
        password = new_password if new_password is not None else self._generate_password()
        if not password:
            raise ValidationError("口令不能为空")

        backup = self._backup_conf()
        previous_pass = self._snapshot_pass()
        previous_env = os.environ.get(_PASSWORD_COMMAND_ENV)
        temps: list[Path] = []
        pass_written = False
        try:
            temp_pass = _temp_file(temps, prefix="rclone-pass-new-", data=password.encode())
            set_code = await self._run(
                self._encryption_argv("set", password_command_for(temp_pass, "enable"))
            )
            if set_code != 0:
                raise RcloneUnavailableError("rclone 加密写入失败，已回滚")
            if await self._check(temp_pass) != 0:
                raise RcloneUnavailableError("rclone 加密后校验失败，已回滚")
            self._write_pass(password, temps)
            pass_written = True
            self._set_password_command_env()
        except Exception as exc:
            self._restore_conf(backup)
            if pass_written:
                self._restore_pass(previous_pass)
            self._restore_env(previous_env)
            if isinstance(exc, AppError):
                raise
            raise RcloneUnavailableError("rclone 加密操作不可用或已回滚") from None
        finally:
            _cleanup(temps)
        return password

    async def rekey(self, old_password: str, new_password: str) -> str:
        """仅已加密且 old_password 可解时可用；双分支脚本驱动原子换口令。

        脚本内容只有临时口令文件路径与环境变量名，不含任何口令；默认执行器
        在 set 时置 stdin=DEVNULL。失败还原 conf 且口令文件保持旧值。
        """
        self._require_encrypted()
        previous_pass = self._snapshot_pass()
        previous_env = os.environ.get(_PASSWORD_COMMAND_ENV)
        temps: list[Path] = []
        backup: Path | None = None
        pass_written = False
        try:
            old_tmp = await self._verify_password(old_password, temps)
            if not new_password:
                raise ValidationError("新口令不能为空")
            new_tmp = _temp_file(temps, prefix="rclone-pass-new-", data=new_password.encode())
            script = (
                "#!/bin/sh\n"
                'if [ "$RCLONE_PASSWORD_CHANGE" = "1" ]; then\n'
                f"  /bin/cat {shlex.quote(str(new_tmp))}\n"
                "else\n"
                f"  /bin/cat {shlex.quote(str(old_tmp))}\n"
                "fi\n"
            ).encode()
            script_tmp = _temp_file(
                temps,
                prefix="rclone-rekey-",
                data=script,
                mode=0o700,
                suffix=".sh",
                directory=_script_dir(),
            )
            backup = self._backup_conf()
            set_code = await self._run(self._encryption_argv("set", str(script_tmp)))
            if set_code != 0:
                raise RcloneUnavailableError("rclone 换口令写入失败，已回滚")
            if await self._check(new_tmp) != 0:
                raise RcloneUnavailableError("rclone 换口令后校验失败，已回滚")
            self._write_pass(new_password, temps)
            pass_written = True
            self._set_password_command_env()
        except Exception as exc:
            if backup is not None:
                self._restore_conf(backup)
            if pass_written:
                self._restore_pass(previous_pass)
            self._restore_env(previous_env)
            if isinstance(exc, AppError):
                raise
            raise RcloneUnavailableError("rclone 换口令操作不可用或已回滚") from None
        finally:
            _cleanup(temps)
        return new_password

    async def disable(self, current_password: str) -> None:
        """仅已加密且当前口令验通时可用；remove 后校验明文头才删口令文件。

        失败把 conf 还原为操作前字节、口令文件与运行期 env 保持原值；
        不新建备份文件，也不留下临时口令文件。
        """
        self._require_encrypted()
        previous_pass = self._snapshot_pass()
        previous_env = os.environ.get(_PASSWORD_COMMAND_ENV)
        temps: list[Path] = []
        original: bytes | None = None
        pass_deleted = False
        try:
            current_tmp = await self._verify_password(current_password, temps)
            try:
                original = self._conf_path.read_bytes()
            except OSError:
                raise RcloneUnavailableError("rclone 配置文件不可读") from None
            remove_code = await self._run(
                self._encryption_argv("remove", password_command_for(current_tmp, "disable"))
            )
            if remove_code != 0:
                raise RcloneUnavailableError("rclone 解密写入失败，已回滚")
            if not self._conf_path.is_file() or self._header_encrypted():
                raise RcloneUnavailableError("rclone 解密后校验失败，已回滚")
            self._pass_path.unlink(missing_ok=True)
            pass_deleted = True
            self._clear_password_command_env()
        except Exception as exc:
            if original is not None:
                with suppress(OSError):
                    self._conf_path.write_bytes(original)
                    self._conf_path.chmod(0o600)
            if pass_deleted:
                self._restore_pass(previous_pass)
            self._restore_env(previous_env)
            if isinstance(exc, AppError):
                raise
            raise RcloneUnavailableError("rclone 解密操作不可用或已回滚") from None
        finally:
            _cleanup(temps)

    @staticmethod
    def _generate_password() -> str:
        """32 位字母数字随机口令；不追加尾随换行。"""
        return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(32))

    @staticmethod
    def _encryption_argv(action: str, command: str) -> list[str]:
        """rclone 加密子命令白名单拼装；command 只含可执行命令与文件路径。"""
        return ["rclone", "--password-command", command, "config", "encryption", action]

    async def _run(self, argv: Sequence[str]) -> int:
        """执行器统一包装；底层异常不回显输出，避免任何凭据进入异常消息。"""
        try:
            code, _ = await self._executor(list(argv))
        except (OSError, TimeoutError, NotImplementedError):
            raise RcloneUnavailableError("rclone 配置加密操作不可用") from None
        return code

    async def _check(self, password_file: Path) -> int:
        """用指定口令文件跑 check；文件不存在也只由 rclone 返回非 0。"""
        command = password_command_for(password_file, "check")
        return await self._run(self._encryption_argv("check", command))

    async def _verify_password(self, password: str, temps: list[Path]) -> Path:
        """check 验通一个明文口令；返回其临时文件路径供 set / remove 复用。"""
        if not password:
            raise ValidationError("口令不能为空")
        password_file = _temp_file(temps, prefix="rclone-pass-current-", data=password.encode())
        if await self._check(password_file) != 0:
            raise ValidationError("当前口令无法解密 rclone.conf")
        return password_file

    def _require_encrypted(self) -> None:
        """rekey / disable 的纯文件头门禁；可解性随后由口令 check 验证。"""
        if not self._header_encrypted():
            raise ValidationError("rclone.conf 未加密")

    def _header_encrypted(self) -> bool:
        """复用 P3 的纯文件头判定单一实现点（缺失 / 不可读按明文处理）。"""
        return conf_encrypted(str(self._conf_path))

    def _backup_conf(self) -> Path:
        """备份当前 conf 到同目录 UTC 时间戳文件，权限 0600。"""
        try:
            original = self._conf_path.read_bytes()
        except OSError:
            raise RcloneUnavailableError("rclone 配置文件不可读") from None
        stamp = now_utc().strftime("%Y%m%dT%H%M%S%fZ")
        backup = self._conf_path.parent / f"conf-backup-{stamp}.conf"
        try:
            backup.write_bytes(original)
            backup.chmod(0o600)
        except OSError:
            raise RcloneUnavailableError("rclone 配置备份不可写") from None
        return backup

    def _restore_conf(self, backup: Path) -> None:
        """从备份还原 conf（保留备份文件作为逃生通道）。"""
        with suppress(OSError):
            self._conf_path.write_bytes(backup.read_bytes())
            self._conf_path.chmod(0o600)

    def _snapshot_pass(self) -> bytes | None:
        """记录操作前口令文件内容，供失败回滚；不存在的记为 None。"""
        try:
            return self._pass_path.read_bytes() if self._pass_path.is_file() else None
        except OSError:
            raise RcloneUnavailableError("配置口令文件不可读") from None

    def _restore_pass(self, previous: bytes | None) -> None:
        """把口令文件恢复到操作前状态（不存在则删除）。"""
        with suppress(OSError):
            if previous is None:
                self._pass_path.unlink(missing_ok=True)
            else:
                self._pass_path.write_bytes(previous)
                self._pass_path.chmod(0o600)

    def _write_pass(self, password: str, temps: list[Path]) -> None:
        """同目录 mkstemp + os.replace 原子落盘，0600、无尾随换行。"""
        try:
            self._pass_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = _temp_file(
                temps,
                prefix="rclone-pass-atomic-",
                data=password.encode(),
                directory=self._pass_path.parent,
            )
            os.replace(tmp, self._pass_path)
        except OSError:
            raise RcloneUnavailableError("配置口令文件不可写") from None

    def _set_password_command_env(self) -> None:
        """只写无空格脚本路径，脚本内含 /bin/cat 与口令文件路径，绝不写口令本身。"""
        os.environ[_PASSWORD_COMMAND_ENV] = password_command_for(self._pass_path)

    def _clear_password_command_env(self) -> None:
        """disable 成功后移除运行期口令命令，使后续子进程回到明文 conf。"""
        os.environ.pop(_PASSWORD_COMMAND_ENV, None)

    def _restore_env(self, previous: str | None) -> None:
        """失败时恢复 env 原值；previous 为 None 时移除该键。"""
        if previous is None:
            self._clear_password_command_env()
        else:
            os.environ[_PASSWORD_COMMAND_ENV] = previous
