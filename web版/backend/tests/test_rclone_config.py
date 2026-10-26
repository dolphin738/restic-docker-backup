"""rclone 配置管理（CLI 编排层）单测：字段校验 / CLI 编排 / 凭据过滤 / 降级。

执行器用**带状态的假实现**（模拟真实 conf 的增删改结果），本机无 docker 时也能覆盖
全部逻辑路径；真机语义以 2026-10-26 WSL 真机 spike（rclone v1.60.1）为准，关键实证：

- create/update/delete/dump 的 ``key=value`` 与 ``--obscure`` 语法可用，dump 为 JSON，
  crypt 的 ``filename_encoding=base32768/base64`` 被接受；
- **重名 create 不报错**（exit 0 直接覆盖）、**delete 不存在的段 exit 0**、
  **未知选项键被静默接受**（url2 落盘）——故 409/404/未知键三道校验**必须在编排层**；
- conf 不存在时 dump 输出 = ``{}`` + stderr NOTICE（合并后 JSON 前带日志行）；
- **单文件 bind mount：退出码 0 但写入静默丢失** → 写后必须 dump 复核（见
  ``test_silent_write_loss_detected_and_reported``）。

断言覆盖：生成器白名单/默认值/布尔归一/注入字符/必填、编排 argv 与语义、查询
managed 过滤与凭据零回显、降级（503/422）与脱敏、主机档路径回落。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.services.rclone_config import (
    GENERATORS,
    CryptGenerator,
    RcloneConfigConflict,
    RcloneConfigInvalid,
    RcloneConfigNotFound,
    RcloneConfigService,
    RcloneConfigUnavailable,
    WebDAVGenerator,
    resolve_conf_path,
)
from app.settings import Settings

# ---------------------------------------------------------------- 测试夹具

CONF = "/etc/rclone/rclone.conf"

WEBDAV_SECTION: dict[str, str] = {
    "type": "webdav",
    "url": "https://dav.example.com",
    "vendor": "other",
    "user": "u",
    "pass": "obscured-webdav-pass",
}
CRYPT_SECTION: dict[str, str] = {
    "type": "crypt",
    "remote": "mywebdav:backup/restic",
    "password": "obscured-secret-value",
    "password2": "obscured-salt",
    "filename_encryption": "standard",
    "directory_name_encryption": "true",
    "filename_encoding": "base32",
}
# OAuth 段：永不进注册表（不可编辑），但可删除
GDRIVE_SECTION: dict[str, str] = {"type": "drive", "token": "oauth-token", "client_id": "cid"}


class FakeExec:
    """带 conf 状态机的 CLI 假实现（模拟真实增删改结果，供写后校验断言）。"""

    def __init__(
        self,
        state: dict[str, dict[str, Any]] | None = None,
        failures: dict[str, tuple[int, str] | BaseException] | None = None,
        *,
        apply_writes: bool = True,
    ) -> None:
        self.state: dict[str, dict[str, Any]] = {k: dict(v) for k, v in (state or {}).items()}
        self._failures = failures or {}
        self._apply_writes = apply_writes
        self.calls: list[list[str]] = []

    async def __call__(self, argv: list[str]) -> tuple[int, str]:
        self.calls.append(list(argv))
        for token, item in self._failures.items():
            if token in argv:
                if isinstance(item, BaseException):
                    raise item
                return item
        if "dump" in argv:
            return 0, json.dumps(self.state)
        if "listremotes" in argv:
            return 0, "".join(f"{name}:\n" for name in sorted(self.state))
        # 下标按子命令定位（argv 含 --config <path>，位置会随前缀变化）
        if "create" in argv:
            i = argv.index("create")
            if self._apply_writes:
                self.state[argv[i + 1]] = {"type": argv[i + 2], **_kv(argv[i + 3 :])}
            return 0, ""
        if "update" in argv:
            i = argv.index("update")
            if self._apply_writes:
                self.state.setdefault(argv[i + 1], {}).update(_kv(argv[i + 2 :]))
            return 0, ""
        if "delete" in argv:
            if self._apply_writes:
                self.state.pop(argv[argv.index("delete") + 1], None)
            return 0, ""
        return 0, ""

    def argv_for(self, token: str) -> list[str]:
        hit = [c for c in self.calls if token in c]
        assert hit, f"未执行过含 {token} 的命令：{self.calls}"
        return hit[-1]


def _kv(args: list[str]) -> dict[str, str]:
    return dict(a.split("=", 1) for a in args if "=" in a and not a.startswith("--"))


def _service(
    state: dict[str, dict[str, Any]] | None = None,
    failures: dict[str, tuple[int, str] | BaseException] | None = None,
    **kwargs: bool,
):
    exec_ = FakeExec(state, failures, **kwargs)
    return RcloneConfigService(Settings(data_dir="/tmp/rclone-test", rclone_config=CONF), executor=exec_), exec_


CRYPT_FULL: dict[str, Any] = {
    "remote": "mywebdav:backup/restic",
    "password": "plain-password",
    "password2": "plain-salt",
    "filename_encryption": "off",
    "directory_name_encryption": "false",
    "filename_encoding": "base32768",
}
WEBDAV_FULL: dict[str, Any] = {
    "url": "https://dav.example.com",
    "vendor": "nextcloud",
    "user": "alice",
    "pass": "webdav-pass",
}


# ---------------------------------------------------------------- 生成器：crypt


def test_crypt_build_args_defaults_and_order():
    """只给必填 → 套用三处默认值，键序按 display_keys + credential_keys。"""
    args = GENERATORS["crypt"].build_args({"remote": "mywebdav:bucket", "password": "p"})
    assert args == [
        "remote=mywebdav:bucket",
        "filename_encryption=standard",
        "directory_name_encryption=true",
        "filename_encoding=base64",
        "password=p",
    ]


def test_crypt_build_args_explicit_values():
    assert GENERATORS["crypt"].build_args(CRYPT_FULL) == [
        "remote=mywebdav:backup/restic",
        "filename_encryption=off",
        "directory_name_encryption=false",
        "filename_encoding=base32768",
        "password=plain-password",
        "password2=plain-salt",
    ]


def test_crypt_bool_normalization_accepts_yes_no():
    built = GENERATORS["crypt"].build_args({**CRYPT_FULL, "directory_name_encryption": "yes"})
    assert dict(a.split("=", 1) for a in built)["directory_name_encryption"] == "true"


@pytest.mark.parametrize(
    ("key", "value"),
    [("filename_encryption", "rot13"), ("filename_encoding", "base128")],
)
def test_crypt_whitelist_rejects_unknown_choice(key, value):
    with pytest.raises(RcloneConfigInvalid) as exc:
        GENERATORS["crypt"].build_args({**CRYPT_FULL, key: value})
    assert key in str(exc.value)


def test_crypt_missing_required_password_on_create():
    with pytest.raises(RcloneConfigInvalid) as exc:
        GENERATORS["crypt"].build_args({"remote": "mywebdav:bucket"})
    assert "password" in str(exc.value)


def test_crypt_update_allows_blank_password():
    """编辑时凭据留空 = 保持不变（password/password2 不出现在参数里）。"""
    args = GENERATORS["crypt"].build_args({"remote": "mywebdav:bucket"}, update=True)
    assert "password" not in [a.split("=", 1)[0] for a in args]


def test_crypt_rejects_malformed_underlying_remote():
    with pytest.raises(RcloneConfigInvalid) as exc:
        GENERATORS["crypt"].build_args({"remote": "no-colon-path", "password": "p"})
    assert "远端名:桶内路径" in str(exc.value)


# ---------------------------------------------------------------- 生成器：webdav


def test_webdav_build_args_defaults():
    assert GENERATORS["webdav"].build_args({"url": "https://dav.example.com", "user": "u", "pass": "p"}) == [
        "url=https://dav.example.com",
        "vendor=other",
        "user=u",
        "pass=p",
    ]


def test_webdav_rejects_non_http_url():
    with pytest.raises(RcloneConfigInvalid) as exc:
        GENERATORS["webdav"].build_args({**WEBDAV_FULL, "url": "ftp://dav.example.com"})
    assert "http" in str(exc.value)


def test_webdav_vendor_whitelist():
    with pytest.raises(RcloneConfigInvalid):
        GENERATORS["webdav"].build_args({**WEBDAV_FULL, "vendor": "dropbox"})


def test_unknown_param_rejected_no_free_form_editing():
    """自由改键口不可开：注册表外参数一律 422（真机实测 rclone 会**静默接受**未知键）。"""
    with pytest.raises(RcloneConfigInvalid) as exc:
        GENERATORS["webdav"].build_args({**WEBDAV_FULL, "token": "oauth-xxx"})
    assert "token" in str(exc.value)


def test_control_chars_rejected_ini_injection():
    with pytest.raises(RcloneConfigInvalid) as exc:
        GENERATORS["webdav"].build_args({**WEBDAV_FULL, "user": "alice\n[evil]\ntype = local"})
    assert "控制字符" in str(exc.value)


# ---------------------------------------------------------------- 编排：create / update / delete


async def test_create_argv_and_obscure_flag():
    svc, exec_ = _service({"mywebdav": WEBDAV_SECTION})
    result = await svc.create("mycrypt", "crypt", CRYPT_FULL)
    argv = exec_.argv_for("create")
    # 显式 --config：避免 rclone 落到 $HOME 默认 conf（与回报的 conf_path 不一致）
    assert argv[:6] == ["rclone", "--config", CONF, "config", "create", "mycrypt"]
    assert argv[6] == "crypt"
    assert argv[-1] == "--obscure"
    assert result.action == "create" and result.type == "crypt"
    assert "mycrypt" in result.remotes_after


async def test_create_already_obscured_skips_obscure_flag():
    svc, exec_ = _service({"mywebdav": WEBDAV_SECTION})
    await svc.create("mycrypt", "crypt", CRYPT_FULL, already_obscured=True)
    assert "--obscure" not in exec_.argv_for("create")


async def test_create_duplicate_remote_name_conflict():
    """真机实测：重名 create 会被 rclone 静默覆盖 → 409 只能靠编排层预检。"""
    svc, exec_ = _service({"mycrypt": CRYPT_SECTION})
    with pytest.raises(RcloneConfigConflict) as exc:
        await svc.create("mycrypt", "crypt", CRYPT_FULL)
    assert "mycrypt" in str(exc.value)
    assert "create" not in " ".join(" ".join(c) for c in exec_.calls)


async def test_create_rejects_unsupported_type():
    svc, exec_ = _service()
    with pytest.raises(RcloneConfigInvalid):
        await svc.create("gd", "drive", {"token": "x"})
    assert exec_.calls == []


async def test_create_checks_underlying_remote_in_registry():
    svc, _ = _service({"mywebdav": WEBDAV_SECTION})
    with pytest.raises(RcloneConfigInvalid) as exc:
        await svc.create("mycrypt", "crypt", CRYPT_FULL, available_remotes=("other",))
    assert "mywebdav" in str(exc.value)


async def test_update_password_blank_keeps_existing():
    svc, exec_ = _service({"mycrypt": CRYPT_SECTION, "mywebdav": WEBDAV_SECTION})
    await svc.update("mycrypt", "crypt", {"remote": "mywebdav:backup/restic"})
    argv = exec_.argv_for("update")
    assert argv[:6] == ["rclone", "--config", CONF, "config", "update", "mycrypt"]
    assert not any(a.startswith("password") for a in argv)


async def test_update_type_mismatch_rejected():
    svc, _ = _service({"mywebdav": WEBDAV_SECTION})
    with pytest.raises(RcloneConfigInvalid) as exc:
        await svc.update("mywebdav", "crypt", CRYPT_FULL)
    assert "webdav" in str(exc.value)


async def test_update_missing_remote_not_found():
    svc, _ = _service()
    with pytest.raises(RcloneConfigNotFound):
        await svc.update("ghost", "crypt", CRYPT_FULL)


async def test_update_requires_non_credential_required_keys():
    """全量更新语义：非凭据必填项（crypt 的 remote / webdav 的 url·user）不可留空。"""
    svc, exec_ = _service({"mycrypt": CRYPT_SECTION})
    with pytest.raises(RcloneConfigInvalid) as exc:
        await svc.update("mycrypt", "crypt", {"password": "new-pass"})
    assert "remote" in str(exc.value)
    assert all("update" not in c for c in exec_.calls)


async def test_delete_any_section_including_oauth():
    """裁决 8：删除对全部段开放（含 OAuth 网盘段）。"""
    svc, exec_ = _service({"gdrive": GDRIVE_SECTION})
    result = await svc.delete("gdrive")
    assert exec_.argv_for("delete") == ["rclone", "--config", CONF, "config", "delete", "gdrive"]
    assert result.type == "drive"


async def test_delete_missing_remote_not_found():
    """真机实测：delete 不存在的段 exit 0（静默）→ 404 只能靠编排层预检。"""
    svc, exec_ = _service()
    with pytest.raises(RcloneConfigNotFound):
        await svc.delete("ghost")
    assert all("delete" not in c for c in exec_.calls)  # 预检挡在删命令之前


@pytest.mark.parametrize("name", ["with space", "a/b", "", "x" * 65, "ok:no"])
async def test_remote_name_whitelist_rejected_before_cli(name):
    svc, exec_ = _service({"mycrypt": CRYPT_SECTION})
    with pytest.raises(RcloneConfigInvalid):
        await svc.delete(name)
    assert exec_.calls == []


# ---------------------------------------------------------------- 写后校验（单文件挂载静默丢失）


@pytest.mark.parametrize("action", ["create", "update", "delete"])
async def test_silent_write_loss_detected_and_reported(action):
    """真机 A/B 实证：单文件 bind mount 下 rclone exit 0 但写入不落地。

    写后 dump 复核是唯一可靠检测手段——必须以 503 + 可操作 reason 暴露，不能让 UI 误报成功。
    """
    state = {"mycrypt": CRYPT_SECTION, "mywebdav": WEBDAV_SECTION}
    svc, _ = _service(state, apply_writes=False)
    with pytest.raises(RcloneConfigUnavailable) as exc:
        if action == "create":
            await svc.create("fresh", "crypt", CRYPT_FULL)
        elif action == "update":
            await svc.update("mycrypt", "crypt", CRYPT_FULL)
        else:
            await svc.delete("mycrypt")
    assert "目录挂载" in str(exc.value)


# ---------------------------------------------------------------- 查询


async def test_list_configs_managed_params_and_credential_flags():
    svc, _ = _service({"mycrypt": CRYPT_SECTION, "mywebdav": WEBDAV_SECTION, "gdrive": GDRIVE_SECTION})
    listing = await svc.list_configs()
    by_name = {item.remote: item for item in listing.configs}
    crypt = by_name["mycrypt"]
    assert crypt.managed and crypt.type == "crypt"
    # 回显口径 = conf 里的实际值（原样透出，不套默认）；默认值由
    # test_crypt_build_args_defaults_and_order 覆盖
    assert crypt.params["filename_encoding"] == "base32"
    assert crypt.has_credentials == {"password": True, "password2": True}
    # 凭据值永不回显
    assert "obscured-secret-value" not in json.dumps(crypt.params, ensure_ascii=False)
    assert "password" not in crypt.params

    gdrive = by_name["gdrive"]
    assert not gdrive.managed and gdrive.params == {} and gdrive.has_credentials == {}


async def test_list_configs_dump_with_notice_noise_parses_empty():
    """conf 不存在：dump 输出 = {} + stderr NOTICE（合并后 JSON 前带日志行）。"""
    svc, _ = _service(
        failures={"dump": (0, '{}\n2026/10/26 NOTICE: Config file "/x" not found - using defaults\n')},
    )
    listing = await svc.list_configs()
    assert listing.available is True and listing.configs == ()


async def test_list_configs_unavailable_returns_reason_not_exception():
    svc, _ = _service(failures={"dump": FileNotFoundError()})
    listing = await svc.list_configs()
    assert listing.available is False and listing.configs == ()
    assert "rclone" in (listing.reason or "")


async def test_list_configs_conf_present_flag():
    svc, _ = _service()
    listing = await svc.list_configs()
    assert listing.conf_path == CONF
    assert listing.conf_present is False  # 测试环境该文件不存在


# ---------------------------------------------------------------- 降级与脱敏


@pytest.mark.parametrize("raised", [FileNotFoundError(), TimeoutError(), NotImplementedError()])
async def test_cli_unavailable_maps_to_503(raised):
    svc, _ = _service({"mywebdav": WEBDAV_SECTION}, {"create": raised})
    with pytest.raises(RcloneConfigUnavailable):
        await svc.create("mycrypt", "crypt", CRYPT_FULL)


async def test_permission_denied_maps_to_503():
    svc, _ = _service({"mywebdav": WEBDAV_SECTION}, {"create": (1, "open /etc/rclone: permission denied")})
    with pytest.raises(RcloneConfigUnavailable) as exc:
        await svc.create("mycrypt", "crypt", CRYPT_FULL)
    assert exc.value.status_code == 503


async def test_other_cli_failure_maps_to_422_with_reason():
    svc, _ = _service({"mywebdav": WEBDAV_SECTION}, {"create": (1, "unknown option")})
    with pytest.raises(RcloneConfigInvalid) as exc:
        await svc.create("mycrypt", "crypt", CRYPT_FULL)
    assert "unknown option" in str(exc.value)


async def test_failure_reason_redacts_submitted_credentials():
    """rclone 异常可能回显参数——凭据原文必须被抹掉（S3）。"""
    svc, _ = _service(
        {"mywebdav": WEBDAV_SECTION},
        {"create": (1, "bad value password=plain-password for key")},
    )
    with pytest.raises(RcloneConfigInvalid) as exc:
        await svc.create("mycrypt", "crypt", CRYPT_FULL)
    assert "plain-password" not in str(exc.value) and "***" in str(exc.value)


async def test_malformed_dump_output_degrades_with_reason():
    svc, _ = _service(failures={"dump": (0, "not-json")})
    listing = await svc.list_configs()
    assert listing.available is False and listing.configs == ()
    assert "无法解析" in (listing.reason or "")


# ---------------------------------------------------------------- 主机档路径回落


def test_conf_path_falls_back_to_home_default(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    settings = Settings(data_dir=str(tmp_path / "data"))
    assert resolve_conf_path(settings).endswith("/.config/rclone/rclone.conf")


def test_conf_path_prefers_settings_value():
    settings = Settings(data_dir="/tmp/x", rclone_config="/etc/rclone/rclone.conf")
    assert resolve_conf_path(settings) == "/etc/rclone/rclone.conf"


def test_generators_registry_shape():
    assert set(GENERATORS) == {"crypt", "webdav"}
    assert isinstance(GENERATORS["crypt"], CryptGenerator)
    assert isinstance(GENERATORS["webdav"], WebDAVGenerator)
