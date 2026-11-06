"""RcloneCryptoService 备份/回滚语义（真机缺陷回归：备份文件不该越攒越坏）。

锁定语义：
- 逃生通道只由 enable() 建立——备份的是操作前的**明文** conf，口令彻底丢失时
  才有用；落在 <passfile_root>/conf-backup.conf（单文件滚动）。
- rekey() 的输入是旧密文：备份下来的是旧口令才能解开的旧密文，旧口令一换就
  再也解不开，既当不了逃生通道又多留一份密文副本，故 rekey 用**内存快照**
  回滚、不写备份文件（真机实测：换口令后 conf-backup.conf 新旧口令都 check 不过）。
- disable() 直接内存快照回滚，同样不写备份。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.errors import RcloneUnavailableError, ValidationError
from app.services.rclone import RCLONE_CONF_LOCKED_HINT
from app.services.rclone_crypto import (
    _CONF_BACKUP_NAME,
    _PASSWORD_COMMAND_ENV,
    RCLONE_PASS_FILENAME,
    RcloneCryptoService,
    password_command_for,
    resolve_pass_path,
)
from app.settings import Settings

ENCRYPTED_CONF = b"# Encrypted rclone configuration File\n\nRCLONE_ENCRYPT_V0:\nAAAA\n"
PLAIN_CONF = b"[mycrypt]\ntype = crypt\nremote = /home/222\n"


async def _noop_executor(argv) -> tuple[int, str]:
    """替身执行器：任何 rclone 调用都算成功，不碰真实二进制。"""
    return 0, ""


def _service(tmp_path, conf: bytes) -> RcloneCryptoService:
    """构造服务：conf 在 tmp_path/rclone.conf，口令目录是 tmp_path/repos。"""
    conf_path = tmp_path / "rclone.conf"
    conf_path.write_bytes(conf)
    pass_root = tmp_path / "repos"
    pass_root.mkdir()
    settings = Settings(rclone_config=str(conf_path), passfile_root=str(pass_root))
    return RcloneCryptoService(settings, executor=_noop_executor)


async def test_enable_writes_plaintext_escape_backup(tmp_path) -> None:
    """逃生通道：enable 前把明文 conf 备份到口令目录（不落 conf 同目录）。"""
    svc = _service(tmp_path, PLAIN_CONF)
    await svc.enable("new-password")

    backup = tmp_path / "repos" / _CONF_BACKUP_NAME
    assert backup.is_file()
    assert backup.read_bytes() == PLAIN_CONF
    # conf 同目录不得出现任何备份文件（否则加密可以被旁路）
    assert list(tmp_path.glob("conf-backup*")) == []


async def test_rekey_does_not_write_backup(tmp_path) -> None:
    """rekey 不留备份：旧密文备份用哪个口令都解不开，纯属多余副本。"""
    svc = _service(tmp_path, ENCRYPTED_CONF)
    await svc.rekey("old-password", "new-password")

    assert not (tmp_path / "repos" / _CONF_BACKUP_NAME).exists()
    # 口径不变：新口令落盘、conf 仍加密
    assert (tmp_path / "repos" / "rclone.pass").read_text() == "new-password"


async def test_rekey_failure_restores_original_bytes(tmp_path) -> None:
    """rekey 失败按内存快照逐字节还原 conf，口令文件不动。"""
    svc = _service(tmp_path, ENCRYPTED_CONF)
    (tmp_path / "repos" / "rclone.pass").write_bytes(b"old-password")

    async def fail_on_set(argv) -> tuple[int, str]:
        # check 放行、set 失败 → 覆盖回滚分支
        return (1, "") if "set" in argv else (0, "")

    svc._executor = fail_on_set
    with pytest.raises(RcloneUnavailableError):
        await svc.rekey("old-password", "new-password")

    assert (tmp_path / "rclone.conf").read_bytes() == ENCRYPTED_CONF
    assert (tmp_path / "repos" / "rclone.pass").read_bytes() == b"old-password"
    assert not (tmp_path / "repos" / _CONF_BACKUP_NAME).exists()


async def test_enable_without_backup_leaves_no_plaintext_copy(tmp_path) -> None:
    """开关关掉时不留任何明文副本：口令目录与 conf 同目录都不得出现备份。"""
    svc = _service(tmp_path, PLAIN_CONF)
    await svc.enable("new-password", backup=False)

    assert not (tmp_path / "repos" / _CONF_BACKUP_NAME).exists()
    assert list(tmp_path.glob("conf-backup*")) == []
    # 开关只管备份，启用本身照旧：口令落盘
    assert (tmp_path / "repos" / "rclone.pass").read_text() == "new-password"


async def test_enable_without_backup_still_rolls_back(tmp_path) -> None:
    """关掉备份不削弱回滚：失败仍按内存字节还原 conf、不留口令。"""
    svc = _service(tmp_path, PLAIN_CONF)

    async def fail_on_set(argv) -> tuple[int, str]:
        return (1, "") if "set" in argv else (0, "")

    svc._executor = fail_on_set
    with pytest.raises(RcloneUnavailableError):
        await svc.enable("new-password", backup=False)

    assert (tmp_path / "rclone.conf").read_bytes() == PLAIN_CONF
    assert not (tmp_path / "repos" / "rclone.pass").exists()
    assert not (tmp_path / "repos" / _CONF_BACKUP_NAME).exists()


def test_password_script_path_is_process_scoped_and_whitespace_free(tmp_path) -> None:
    """口令脚本路径必须带进程号且不含空白（真机缺陷回归）。

    路径固定会被同机 pytest 改写（那次 GET /api/rclone/configs 500 的成因）；
    含空白则 rclone 按空白切分后 exec 失败（"cat: /mnt/d/agent/AI: No such file"）。
    """
    pass_path = tmp_path / "repos" / "rclone.pass"
    pass_path.parent.mkdir()
    pass_path.write_text("secret")

    command = password_command_for(pass_path)
    assert " " not in command and "\t" not in command
    assert f"-{os.getpid()}.sh" in command
    # 脚本体内只有口令文件路径，绝不含口令本身（S3）
    script = Path(command)
    assert script.is_file()
    assert str(pass_path) in script.read_text(encoding="utf-8")
    assert "secret" not in script.read_text(encoding="utf-8")
    script.unlink()


def test_password_script_path_differs_per_tag(tmp_path) -> None:
    """同一口令路径的不同 tag 落到不同脚本文件，避免并发写同一文件。"""
    pass_path = tmp_path / "rclone.pass"
    seen = {password_command_for(pass_path, tag) for tag in (None, "enable", "check")}
    assert len(seen) == 3
    for command in seen:
        Path(command).unlink(missing_ok=True)


# ---- Standards #2/#3 回归：口令路径单一真源 + hint 形态无关 + unlock 收敛到服务层 ----


def test_resolve_pass_path_is_single_source_and_host_agnostic(tmp_path) -> None:
    """口令路径只由 settings.passfile_root 派生，不含 docker 专属 /repos 字面量。"""
    settings = Settings(passfile_root=str(tmp_path / "keys"))
    assert resolve_pass_path(settings) == tmp_path / "keys" / RCLONE_PASS_FILENAME
    assert "repos" not in RCLONE_CONF_LOCKED_HINT
    assert RCLONE_PASS_FILENAME in RCLONE_CONF_LOCKED_HINT


async def test_unlock_writes_pass_and_rebuilds_env(tmp_path, monkeypatch) -> None:
    """unlock 成功：口令原子落盘、RCLONE_PASSWORD_COMMAND 重建且指向本服务口令文件。"""
    monkeypatch.delenv(_PASSWORD_COMMAND_ENV, raising=False)
    svc = _service(tmp_path, ENCRYPTED_CONF)
    # status() 已加密：executor 放行 check（code 0）→ 验通 → 不回滚
    await svc.unlock("good-password")

    pass_path = tmp_path / "repos" / RCLONE_PASS_FILENAME
    assert pass_path.read_text() == "good-password"
    command = os.environ[_PASSWORD_COMMAND_ENV]
    assert " " not in command and str(pass_path) in Path(command).read_text(encoding="utf-8")
    monkeypatch.delenv(_PASSWORD_COMMAND_ENV, raising=False)
    Path(command).unlink(missing_ok=True)


async def test_unlock_failure_rolls_back_pass_and_env(tmp_path, monkeypatch) -> None:
    """口令错（check 非 0）：口令文件与运行期 env 都回滚到操作前，零残留。"""
    monkeypatch.setenv(_PASSWORD_COMMAND_ENV, "previous-command")
    svc = _service(tmp_path, ENCRYPTED_CONF)
    old_pass = tmp_path / "repos" / RCLONE_PASS_FILENAME
    old_pass.write_bytes(b"old-password")

    async def fail_check(argv) -> tuple[int, str]:
        return (1, "") if "check" in argv else (0, "")

    svc._executor = fail_check
    with pytest.raises(ValidationError):
        await svc.unlock("wrong-password")

    assert old_pass.read_bytes() == b"old-password"
    assert os.environ[_PASSWORD_COMMAND_ENV] == "previous-command"


async def test_unlock_empty_password_is_rejected(tmp_path, monkeypatch) -> None:
    """空口令在写盘前即 422，不触碰既有口令文件与 env。"""
    monkeypatch.delenv(_PASSWORD_COMMAND_ENV, raising=False)
    svc = _service(tmp_path, ENCRYPTED_CONF)
    with pytest.raises(ValidationError):
        await svc.unlock("")
    assert not (tmp_path / "repos" / RCLONE_PASS_FILENAME).exists()
    assert _PASSWORD_COMMAND_ENV not in os.environ
