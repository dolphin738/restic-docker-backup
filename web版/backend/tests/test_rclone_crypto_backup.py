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

import pytest

from app.errors import RcloneUnavailableError
from app.services.rclone_crypto import _CONF_BACKUP_NAME, RcloneCryptoService
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
