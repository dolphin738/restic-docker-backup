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
import tempfile
from typing import Any

import pytest

from app.db.database import close_database, init_database
from app.db.repositories.rclone_form_repo import RcloneFormRepo
from app.services.rclone_config import (
    GENERATORS,
    CryptGenerator,
    RcloneConfigConflict,
    RcloneConfigInvalid,
    RcloneConfigNotFound,
    RcloneConfigService,
    RcloneConfigUnavailable,
    WebDAVGenerator,
    classify_crypt_remote,
    resolve_conf_path,
)
from app.services.rclone_forms import (
    LEGACY_META_FILENAME,
    LegacySidecarStore,
    import_legacy_sidecar,
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

    CRED_KEYS = ("password", "password2", "pass", "bearer_token")

    def _obscured_kv(self, args: list[str]) -> dict[str, str]:
        """应用 key=value；带 ``--obscure`` 时模拟 rclone 混淆（凭据前缀 obscured-）。"""
        kv = _kv(args)
        if "--obscure" in args:
            kv = {k: ("obscured-" + v if k in self.CRED_KEYS else v) for k, v in kv.items()}
        return kv

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
        # reveal <混淆值>：只有「混淆形态」（本 fake 用 obscured- 前缀表示）能解开；
        # 明文 → 非零退出（对齐真机 rclone：input too short / base64 decode failed）
        if "reveal" in argv:
            value = argv[-1]
            if value.startswith("obscured-"):
                return 0, value[len("obscured-"):]
            return 1, "Failed to reveal: base64 decode failed when revealing password - is it obscured?"
        # 下标按子命令定位（argv 含 --config <path>，位置会随前缀变化）
        if "create" in argv:
            i = argv.index("create")
            if self._apply_writes:
                self.state[argv[i + 1]] = {"type": argv[i + 2], **self._obscured_kv(argv[i + 3 :])}
            return 0, ""
        if "update" in argv:
            i = argv.index("update")
            if self._apply_writes:
                self.state.setdefault(argv[i + 1], {}).update(self._obscured_kv(argv[i + 2 :]))
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
    # 每个服务实例独立 data_dir：sidecar 形态记录（rclone_meta.json）互不串扰
    data_dir = tempfile.mkdtemp(prefix="rclone-meta-test-")
    return RcloneConfigService(Settings(data_dir=data_dir, rclone_config=CONF), executor=exec_), exec_


CRYPT_FULL: dict[str, Any] = {
    "remote": "mywebdav:backup/restic",
    "password": "plain-password",
    "password2": "plain-salt",
    "filename_encryption": "off",
    "directory_name_encryption": "false",
    "filename_encoding": "base32768",
}
# 仅非凭据键（编辑态凭据留空 = 保持/就地转换的用例走这份，全量更新语义仍要求 display 键）
CRYPT_DISPLAY: dict[str, str] = {k: v for k, v in CRYPT_FULL.items() if k not in ("password", "password2")}
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


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # ① 远端指针
        ("mywebdav:backup/restic", ("remote", "mywebdav")),
        ("mywebdav:", ("remote", "mywebdav")),
        ("s3-bucket:bucket/x", ("remote", "s3-bucket")),
        # ② 本地绝对路径（官方文档 §Specifying the remote 明确支持）
        ("/repo/encrypted", ("local", None)),
        ("/", ("local", None)),
        ("C:/backup/crypt", ("local", None)),
        ("C:\\backup\\crypt", ("local", None)),
        ("\\\\nas\\share\\crypt", ("local", None)),
        # ③ 相对路径：rclone 按进程 cwd 下的子目录处理，容器内不可控 → 判为 relative 并拒绝
        ("backup/restic", ("relative", None)),
        ("mycrypt", ("relative", None)),
        ("", ("relative", None)),
    ],
)
def test_classify_crypt_remote_three_kinds(value, expected):
    """三态口径：远端指针 / 本地绝对路径 / 相对路径（后者拒绝）。

    重点回归：Windows 盘符 ``C:/x``、``C:\\x`` 必须判 local——旧实现会把它当成
    名为 ``C`` 的远端而报「远端不在册」。
    """
    assert classify_crypt_remote(value) == expected


@pytest.mark.parametrize("local_path", ["/repo/encrypted", "C:/backup/crypt", "C:\\backup\\crypt"])
def test_crypt_accepts_local_absolute_path(local_path):
    args = GENERATORS["crypt"].build_args({"remote": local_path, "password": "p"})
    assert args[0] == f"remote={local_path}"


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


def test_webdav_bearer_token_sufficient():
    """bearer_token 可替代 user/pass：仅提供 bearer 也能建段。"""
    args = GENERATORS["webdav"].build_args({"url": "https://dav.example.com", "bearer_token": "tok"})
    assert "bearer_token=tok" in args
    assert "pass" not in " ".join(args)


def test_webdav_requires_user_pass_or_bearer():
    """仅提供 user（无 pass、无 bearer）应被拒。"""
    with pytest.raises(RcloneConfigInvalid):
        GENERATORS["webdav"].build_args({"url": "https://dav.example.com", "user": "u"})


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


async def test_create_value_obscured_passes_no_obscure_flag():
    """提交的值已是混淆产物 → 必须显式 ``--no-obscure``。

    真机缺陷：旧实现「已混淆 → 不加 flag」，而 rclone 默认会自行判断，导致开关
    开/关生成的 conf 逐字节相同（开关形同摆设）。
    """
    svc, exec_ = _service({"mywebdav": WEBDAV_SECTION})
    pasted = {**CRYPT_FULL, "password": "obscured-pasted", "password2": "obscured-pasted2"}
    await svc.create("mycrypt", "crypt", pasted, value_obscured=True)
    argv = exec_.argv_for("create")
    assert "--no-obscure" in argv
    assert "--obscure" not in argv


async def test_update_value_obscured_passes_no_obscure_flag():
    svc, exec_ = _service({"mycrypt": CRYPT_SECTION, "mywebdav": WEBDAV_SECTION})
    pasted = {**CRYPT_FULL, "password": "obscured-pasted", "password2": "obscured-pasted2"}
    await svc.update("mycrypt", "crypt", pasted, value_obscured=True)
    argv = exec_.argv_for("update")
    assert "--no-obscure" in argv
    assert "--obscure" not in argv


async def test_obscure_flags_are_mutually_exclusive():
    """两个 flag 永不同时出现（rclone 会按最后一个生效，静默错）。"""
    svc, exec_ = _service({"mywebdav": WEBDAV_SECTION})
    await svc.create("c1", "crypt", CRYPT_FULL, value_obscured=False)
    pasted = {**CRYPT_FULL, "password": "obscured-pasted", "password2": "obscured-pasted2"}
    await svc.create("c2", "crypt", pasted, value_obscured=True)
    creates = [c for c in exec_.calls if "create" in c]
    assert len(creates) == 2
    for argv in creates:
        assert ("--obscure" in argv) != ("--no-obscure" in argv)
    assert "--obscure" in creates[0] and "--no-obscure" in creates[1]


# ---------------------------------------------------------------- 形态记录 / 明文段自愈（裁决 17）


async def test_create_records_obscured_forms_sidecar():
    """写入后记录形态；查询返回 credential_obscured（布尔，仍零回显）。"""
    svc, _ = _service({"mywebdav": WEBDAV_SECTION})
    await svc.create("mycrypt", "crypt", CRYPT_FULL)
    listing = await svc.list_configs()
    item = next(c for c in listing.configs if c.remote == "mycrypt")
    assert item.credential_obscured == {"password": True, "password2": True}
    snapshot = await svc._load_forms()
    obscured, digest = snapshot["mycrypt"]["password"]
    assert obscured is True and len(digest) == 64


async def test_list_form_unknown_when_conf_edited_externally():
    """conf 值被外部改过（哈希对不上）→ 形态未知（None），不做任何自动转换。"""
    svc, exec_ = _service({"mywebdav": WEBDAV_SECTION})
    await svc.create("mycrypt", "crypt", CRYPT_FULL)
    exec_.state["mycrypt"]["password"] = "externally-edited-value"
    listing = await svc.list_configs()
    item = next(c for c in listing.configs if c.remote == "mycrypt")
    assert item.credential_obscured["password"] is None
    assert item.credential_obscured["password2"] is True


async def test_update_blank_credential_on_obscured_section_is_noop():
    """已存=混淆 + 凭据留空 → 零凭据命令、零 reveal（保持不变）。"""
    svc, exec_ = _service({"mywebdav": WEBDAV_SECTION})
    await svc.create("mycrypt", "crypt", CRYPT_FULL)
    exec_.calls.clear()
    await svc.update("mycrypt", "crypt", CRYPT_DISPLAY)
    updates = [c for c in exec_.calls if "update" in c]
    assert len(updates) == 1, exec_.calls
    assert "remote=mywebdav:backup/restic" in updates[0]
    assert not any(a.startswith(("password=", "password2=")) for a in updates[0])
    assert not any("reveal" in c for c in exec_.calls)


async def test_update_unknown_form_keeps_value_silent():
    """形态未知（非本服务写入/外部改过）→ 宁缺勿错：不 reveal、不改值。"""
    svc, exec_ = _service({"mycrypt": CRYPT_SECTION, "mywebdav": WEBDAV_SECTION})
    await svc.update("mycrypt", "crypt", CRYPT_DISPLAY)
    assert not any("reveal" in c for c in exec_.calls)
    updates = [c for c in exec_.calls if "update" in c]
    assert len(updates) == 1
    assert not any(a.startswith(("password=", "password2=")) for a in updates[0])
    assert exec_.state["mycrypt"]["password"] == CRYPT_SECTION["password"]


async def test_update_repairs_legacy_plain_credential_in_place():
    """历史明文段（记录 False）+ 凭据留空 → 就地 --obscure 修正，无需重填密码。"""
    legacy = {**CRYPT_SECTION, "password": "legacy-plain", "password2": "legacy-salt"}
    svc, exec_ = _service({"mycrypt": legacy, "mywebdav": WEBDAV_SECTION})
    await svc._forms.replace_remote(
        "mycrypt",
        {
            "password": (False, svc._value_hash("legacy-plain")),
            "password2": (False, svc._value_hash("legacy-salt")),
        },
    )
    exec_.calls.clear()
    await svc.update("mycrypt", "crypt", CRYPT_DISPLAY)
    cred = [
        c
        for c in exec_.calls
        if "update" in c and any(a.startswith(("password=", "password2=")) for a in c)
    ]
    assert len(cred) == 2, exec_.calls
    assert all("--obscure" in c for c in cred)
    # reveal 只做「现值确实解不开」的一致性探测（每键一次），转换本身走 --obscure
    reveals = [c for c in exec_.calls if "reveal" in c]
    assert len(reveals) == 2
    listing = await svc.list_configs()
    item = next(c for c in listing.configs if c.remote == "mycrypt")
    assert item.credential_obscured == {"password": True, "password2": True}


async def test_update_repair_aborts_when_value_looks_obscured():
    """记录说明文、但现值能被 reveal 解开 → 中止（防二次混淆把仓库变成空）。"""
    svc, exec_ = _service({"mycrypt": CRYPT_SECTION, "mywebdav": WEBDAV_SECTION})
    await svc._forms.replace_remote(
        "mycrypt",
        {"password": (False, svc._value_hash(CRYPT_SECTION["password"]))},
    )
    exec_.calls.clear()
    with pytest.raises(RcloneConfigInvalid) as exc:
        await svc.update("mycrypt", "crypt", CRYPT_DISPLAY)
    assert "二次混淆" in str(exc.value)
    assert not any("update" in c or "create" in c for c in exec_.calls), exec_.calls


async def test_update_submitted_password_records_obscured_form():
    """提交新密码 → 落盘必为混淆（--obscure 混淆一次），记录恒 True。"""
    svc, _ = _service({"mycrypt": CRYPT_SECTION, "mywebdav": WEBDAV_SECTION})
    await svc.update("mycrypt", "crypt", {**CRYPT_DISPLAY, "password": "brand-new-pass"})
    listing = await svc.list_configs()
    item = next(c for c in listing.configs if c.remote == "mycrypt")
    assert item.credential_obscured["password"] is True


async def test_delete_clears_sidecar_entry():
    svc, _ = _service({"mywebdav": WEBDAV_SECTION})
    await svc.create("mycrypt", "crypt", CRYPT_FULL)
    await svc.delete("mycrypt")
    snapshot = await svc._load_forms()
    assert "mycrypt" not in snapshot


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


async def test_create_local_path_skips_registry_check():
    """本地绝对路径无远端名可查 → 跳过在册校验（旧实现会误报「远端 C 不在册」）。"""
    svc, exec_ = _service({})
    result = await svc.create(
        "mycrypt", "crypt", {**CRYPT_FULL, "remote": "C:/backup/crypt"}, available_remotes=()
    )
    assert result.action == "create"
    assert exec_.argv_for("create")[exec_.argv_for("create").index("create") + 1] == "mycrypt"


async def test_update_checks_underlying_remote_in_registry():
    """编辑把底层远端改成不在册的 → 422 且不执行 CLI（此前只有 create 校验，属缺陷）。"""
    svc, exec_ = _service({"mycrypt": CRYPT_SECTION, "mywebdav": WEBDAV_SECTION})
    with pytest.raises(RcloneConfigInvalid) as exc:
        await svc.update("mycrypt", "crypt", {**CRYPT_FULL, "remote": "nope:bucket"})
    assert "nope" in str(exc.value)
    assert all("update" not in c for c in exec_.calls)


async def test_update_allows_local_path():
    svc, exec_ = _service({"mycrypt": CRYPT_SECTION})
    await svc.update("mycrypt", "crypt", {**CRYPT_FULL, "remote": "/repo/encrypted"})
    assert "update" in exec_.argv_for("update")


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


# ---------------------------------------------------------------- 落盘形态（裁决 17：恒为混淆）


async def test_value_obscured_flag_mapping():
    """提交值形态 → flag 二分支；两条分支落盘结果都是混淆（rclone 才认）。"""
    svc, exec_ = _service({"mywebdav": WEBDAV_SECTION})
    await svc.create("c1", "crypt", CRYPT_FULL, value_obscured=False)
    assert "--obscure" in exec_.argv_for("create")

    svc, exec_ = _service({"mywebdav": WEBDAV_SECTION})
    pasted = {**CRYPT_FULL, "password": "obscured-pasted", "password2": "obscured-pasted2"}
    await svc.create("c1", "crypt", pasted, value_obscured=True)
    argv = exec_.argv_for("create")
    assert "--no-obscure" in argv and "--obscure" not in argv
    # 原样落盘（不再混淆一次）
    assert exec_.state["c1"]["password"] == "obscured-pasted"


async def test_value_obscured_rejects_plaintext_looking_value():
    """勾了「已是混淆值」但填的是明文 → 422（否则 conf 里就是明文，rclone 读不了）。"""
    svc, exec_ = _service({"mywebdav": WEBDAV_SECTION})
    with pytest.raises(RcloneConfigInvalid) as exc:
        await svc.create("c1", "crypt", CRYPT_FULL, value_obscured=True)
    assert "混淆值" in str(exc.value)
    assert "create" not in " ".join(" ".join(c) for c in exec_.calls)


# ---------------------------------------------------------------- 形态记录落 DB（裁决 16 迁库）


async def test_db_backed_form_store_records_and_clears(tmp_path):
    """DB 仓储路径：create → 记录落表；delete → 同事务清账。"""
    db = await init_database(tmp_path / "forms.db")
    try:
        repo = RcloneFormRepo(db)
        svc, _ = _service({"mywebdav": WEBDAV_SECTION})
        svc._forms = repo
        await svc.create("mycrypt", "crypt", CRYPT_FULL)
        rows = await db.fetch_all("SELECT remote, field, obscured FROM rclone_credential_forms")
        assert {str(r["remote"]) for r in rows} == {"mycrypt"}
        assert all(int(r["obscured"]) == 1 for r in rows)
        await svc.delete("mycrypt")
        rows = await db.fetch_all("SELECT remote FROM rclone_credential_forms")
        assert rows == []
    finally:
        await close_database()


async def test_orphan_form_records_cleaned_on_list(tmp_path):
    """宿主机在外部删段 → 孤儿记录在查询时惰性对齐清理。"""
    db = await init_database(tmp_path / "forms.db")
    try:
        repo = RcloneFormRepo(db)
        svc, _ = _service({"mywebdav": WEBDAV_SECTION})
        svc._forms = repo
        await svc.create("mycrypt", "crypt", CRYPT_FULL)
        # 模拟外部删除：conf 里段没了，DB 记录还在
        svc._forms = repo
        await repo.replace_remote("ghost", {"password": (True, "0" * 64)})
        await svc.list_configs()
        rows = await db.fetch_all("SELECT remote FROM rclone_credential_forms")
        assert {str(r["remote"]) for r in rows} == {"mycrypt"}
    finally:
        await close_database()


async def test_legacy_sidecar_imported_then_removed(tmp_path):
    """旧 sidecar 文件一次性导入目标存储后删除文件（升级迁移）。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    legacy = LegacySidecarStore(data_dir / LEGACY_META_FILENAME)
    await legacy.replace_remote("mycrypt", {"password": (True, "a" * 64)})
    db = await init_database(tmp_path / "forms.db")
    try:
        repo = RcloneFormRepo(db)
        imported = await import_legacy_sidecar(repo, data_dir)
        assert imported == 1
        assert not (data_dir / LEGACY_META_FILENAME).exists()
        snapshot = await repo.load()
        assert snapshot["mycrypt"]["password"] == (True, "a" * 64)
        # 重复导入：文件已删 → 0
        assert await import_legacy_sidecar(repo, data_dir) == 0
    finally:
        await close_database()
