"""A1 仓库后端扩展：类型派生 / URL 严格校验（Q20）/ rclone 只读探测 / 凭据通道。

事实基线（restic 0.19.1 源码核验，2026-10-17；非猜测）：
- ``sftp:`` ParseConfig 双形态（``sftp://user@host[:port]/path`` 与 ``sftp:user@host:path``），
  ``location.NoPassword`` + 外部 ``ssh`` 子进程（免密登录假设，URL 密码会被静默误解）；
- ``azure:`` = ``azure:container:/prefix``；env ``AZURE_ACCOUNT_NAME/KEY/SAS/ENDPOINT_SUFFIX``；
- ``gs:`` = ``gs:bucket:/prefix``（restic 前缀是 ``gs:`` 不是 ``gcs:``）；env ``GOOGLE_PROJECT_ID``
  + Google 客户端库的 ``GOOGLE_APPLICATION_CREDENTIALS``/``GOOGLE_ACCESS_TOKEN``；
- ``rest:`` = ``rest:http(s)://host[:port][/path]``；env ``RESTIC_REST_USERNAME/PASSWORD``
  （**URL 带用户信息时 env 不生效**——故 UI 拒 userinfo、引导走加密凭据字段）；
- ``rclone:`` = ``rclone:remote:path``；restic 按需拉起 ``rclone serve restic --stdio``
  子进程，rclone 读 ``RCLONE_*``；conf 宿主机挂载（Q17 只读托管，凭据零落库）。

Q20 裁定：s3/b2/local **不校验**（零回归）；新类型严格校验 + rclone remote 存在性校验。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import ORIGIN, finish_bootstrap

from app.db.repositories.repo_repo import derive_repo_type
from app.errors import ValidationError
from app.routers.repos import _validate_repo_url, _validate_repo_url_syntax
from app.services.env_sync import load_credentials
from app.services.rclone import RcloneProbe, RcloneRemotes
from app.services.restic import ResticCmd
from app.settings import Settings

# ---------------------------------------------------------------- 类型派生


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("s3:https://s3.endpoint/bucket", "s3"),
        ("b2:mybucket", "b2"),
        ("sftp:user@host:/srv/restic-repo", "sftp"),
        ("azure:mycontainer:/prefix", "azure"),
        ("gs:mybucket:/prefix", "gcs"),
        ("rest:https://rest.example.com:8000/", "rest"),
        ("rclone:myremote:mybucket/restic", "rclone"),
        ("/repo", "local"),
        ("relative/repo", "local"),
        ("", "local"),
        (None, "local"),
        # 大小写/空白不敏感（restic 侧同样大小写不敏感地解析前缀）
        ("S3:Https://Endpoint/Bucket", "s3"),
        ("  gs:Bucket:/  ", "gcs"),
        ("RCLONE:remote:path", "rclone"),
    ],
)
def test_derive_repo_type_matrix(url, expected):
    """前缀 → 类型逐一映射（gs:→gcs 是易错点：restic 历史命名，勿按名前缀推断）。"""
    assert derive_repo_type(url) == expected


# ---------------------------------------------------------------- URL 语法校验（Q20）


@pytest.mark.parametrize(
    "url",
    [
        # sftp 双形态 + 域限定用户 + IPv6/端口（URL 形态）
        "sftp:user@host:/srv/restic-repo",
        "sftp:user@host:rel/dir",
        "sftp://user@host//srv/restic-repo",
        "sftp://user@[::1]:2222//srv/restic-repo",
        "sftp:user@domain@host:/srv/restic-repo",
        # azure / gs（冒号必填，前缀可空）
        "azure:mycontainer:/prefix",
        "azure:mycontainer:",
        "gs:mybucket:/prefix",
        "gs:mybucket:",
        # rest：http/https + unix socket 形态
        "rest:http://host:8000/",
        "rest:https://host",
        "rest:http+unix:///tmp/rest.socket:/my_backup_repo/",
        # s3/b2/local：Q20 裁定不校验——任意形态均放行（零回归）
        "s3:https://anything.goes/bucket",
        "b2:whatever",
        "/repo",
    ],
)
def test_repo_url_syntax_valid(url):
    """合法 URL 一律放行（含 s3/b2/local 的任意形态——不校验即不拒绝）。"""
    _validate_repo_url_syntax(url)


@pytest.mark.parametrize(
    "url",
    [
        # sftp：密码内嵌（restic 无密码认证，走了也是静默误解）
        "sftp:user:pass@host:/srv/repo",
        "sftp://user:pass@host/srv/repo",
        # sftp：缺目录 / scp 形态带端口（restic 会把 2222:/path 当目录名，静默建错目录）
        "sftp:user@host",
        "sftp://user@host",
        "sftp:user@host:2222:/srv/repo",
        # azure / gs：缺冒号或容器名非法
        "azure:mycontainer",
        "azure:MyContainer:/prefix",
        "gs:mybucket",
        # rest：协议非 http(s)、缺主机、内嵌用户密码
        "rest:ftp://host:8000/",
        "rest:https:///path-only",
        "rest:https://user:pass@host:8000/",
    ],
)
def test_repo_url_syntax_invalid_422(url):
    """非法 URL → ValidationError（422 形态由路由层统一映射）。"""
    with pytest.raises(ValidationError):
        _validate_repo_url_syntax(url)


def test_rest_userinfo_error_points_to_credential_fields():
    """rest 拒 userinfo 的错误信息必须指向加密凭据字段（可操作，禁止静默）。"""
    with pytest.raises(ValidationError) as excinfo:
        _validate_repo_url_syntax("rest:https://user:pass@host:8000/")
    message = str(excinfo.value)
    assert "凭据字段" in message and "RESTIC_REST" in message


# ---------------------------------------------------------------- rclone remote 存在性校验


class _FakeProbe:
    """list_remotes 替身：返回预录结果（不 spawn 真子进程）。"""

    def __init__(self, result: RcloneRemotes) -> None:
        self._result = result
        self.calls = 0

    async def list_remotes(self) -> RcloneRemotes:
        self.calls += 1
        return self._result


def _remotes(*names: str, available: bool = True) -> RcloneRemotes:
    return RcloneRemotes(
        available=available,
        conf_present=bool(names),
        conf_path="/etc/rclone/rclone.conf",
        remotes=tuple(names),
        reason=None if available else "探测失败",
    )


async def test_rclone_remote_in_list_passes():
    """remote 在 listremotes 内 → 放行（语法 + 存在性双过）。"""
    probe = _FakeProbe(_remotes("myremote", "other"))
    await _validate_repo_url("rclone:myremote:bucket/restic", probe)  # 不抛即通过
    assert probe.calls == 1


async def test_rclone_remote_not_in_list_422():
    """remote 不在册 → 422，且错误 data 带可用清单（可操作）。"""
    probe = _FakeProbe(_remotes("myremote"))
    with pytest.raises(ValidationError) as excinfo:
        await _validate_repo_url("rclone:typo:bucket/restic", probe)
    assert excinfo.value.data["remote"] == "typo"
    assert excinfo.value.data["available_remotes"] == ["myremote"]


async def test_rclone_probe_unavailable_falls_through_to_e29():
    """探测不可用（缺二进制/超时）→ 放行语法正确的 URL，由 E29 连通性测试终判（不静默）。"""
    probe = _FakeProbe(_remotes(available=False))
    await _validate_repo_url("rclone:myremote:bucket/restic", probe)
    assert probe.calls == 1


async def test_rclone_syntax_checked_before_probe():
    """语法先于探测：坏形态直接 422，不浪费一次子进程调用。"""
    probe = _FakeProbe(_remotes("myremote"))
    with pytest.raises(ValidationError):
        await _validate_repo_url("rclone:myremote", probe)
    assert probe.calls == 0


async def test_empty_url_skips_validation():
    """空 url（允许日后再补）→ 完全跳过校验（含探测）。"""
    probe = _FakeProbe(_remotes("myremote"))
    await _validate_repo_url("  ", probe)
    assert probe.calls == 0


# ---------------------------------------------------------------- 凭据通道（cred.* → env）


class _FakeConfigRepo:
    """load_credentials 最小替身：只提供 list_all（键值行）。"""

    def __init__(self, keys: dict[str, str]) -> None:
        self._rows = [SimpleNamespace(key=k, value=v) for k, v in keys.items()]

    async def list_all(self) -> list[SimpleNamespace]:
        return self._rows


async def test_load_credentials_maps_all_backends():
    """各后端官方 env 名逐一还原（restic_rest_ 前缀整体搬迁到 RESTIC_REST_）。"""
    repo = _FakeConfigRepo(
        {
            "cred.default.aws_access_key_id": "ak",
            "cred.default.aws_session_token": "st",  # 未登记 ENV_KEY_MAP 的键同样可还原
            "cred.default.b2_account_key": "bk",
            "cred.default.azure_account_name": "an",
            "cred.default.azure_account_key": "ak2",
            "cred.default.google_project_id": "gp",
            "cred.default.google_application_credentials": "/keys/sa.json",
            "cred.default.rclone_config_pass": "cp",
            "cred.default.ssh_auth_sock": "/ssh agent placeholder",
            "cred.default.restic_rest_username": "ru",
            "cred.default.restic_rest_password": "rp",
            # 噪声：其它仓库命名空间与未知前缀必须被丢弃（宁可不注入）
            "cred.other.aws_access_key_id": "nope",
            "cred.default.typo_prefix_key": "nope",
            "ui.something": "nope",
        }
    )
    env = await load_credentials(repo)
    assert env == {
        "AWS_ACCESS_KEY_ID": "ak",
        "AWS_SESSION_TOKEN": "st",
        "B2_ACCOUNT_KEY": "bk",
        "AZURE_ACCOUNT_NAME": "an",
        "AZURE_ACCOUNT_KEY": "ak2",
        "GOOGLE_PROJECT_ID": "gp",
        "GOOGLE_APPLICATION_CREDENTIALS": "/keys/sa.json",
        "RCLONE_CONFIG_PASS": "cp",
        "SSH_AUTH_SOCK": "/ssh agent placeholder",
        "RESTIC_REST_USERNAME": "ru",
        "RESTIC_REST_PASSWORD": "rp",
    }


def test_build_env_injects_new_prefixes_and_blocks_others():
    """build_env：新前缀可注入；未登记前缀（PATH 等）依旧拦截（S2 纵深防御）。"""
    settings = Settings(data_dir="/tmp/restic-backends")
    cmd = ResticCmd(settings, None)  # type: ignore[arg-type]
    env = cmd.build_env(
        repo_url="rclone:myremote:bucket",
        env_extras={
            "AZURE_ACCOUNT_NAME": "an",
            "GOOGLE_PROJECT_ID": "gp",
            "RCLONE_BWLIMIT": "1M",
            "SSH_AUTH_SOCK": "/ssh-agent",
            "PATH": "/attacker/bin",  # 越界 → 不得注入
            "LD_PRELOAD": "/evil.so",  # 越界 → 不得注入
        },
    )
    assert env["AZURE_ACCOUNT_NAME"] == "an"
    assert env["GOOGLE_PROJECT_ID"] == "gp"
    assert env["RCLONE_BWLIMIT"] == "1M"
    assert env["SSH_AUTH_SOCK"] == "/ssh-agent"
    assert env["PATH"] != "/attacker/bin"
    assert "LD_PRELOAD" not in env
    assert env["RESTIC_REPOSITORY"] == "rclone:myremote:bucket"


# ---------------------------------------------------------------- RcloneProbe


class _ExecProbe(RcloneProbe):
    """把 _exec 换成预录行为的替身（不 spawn 真子进程）。"""

    def __init__(self, settings: Settings, behavior) -> None:
        super().__init__(settings)
        self._behavior = behavior

    async def _exec(self, argv: list[str]) -> tuple[int, str]:
        self.last_argv = argv
        return self._behavior()


def _probe_settings(tmp_path: Path, conf: str | None = None) -> Settings:
    return Settings(data_dir=str(tmp_path / "data"), rclone_config=conf)


async def test_probe_parses_listremotes_output(tmp_path):
    """listremotes 输出逐行解析（去行尾冒号）；argv 精确为 rclone listremotes。"""
    conf = tmp_path / "rclone.conf"
    conf.write_text("[myremote]\ntype = b2\n", encoding="utf-8")
    probe = _ExecProbe(_probe_settings(tmp_path, str(conf)), lambda: (0, "myremote:\nother:\n"))
    result = await probe.list_remotes()
    assert result.available is True
    assert result.remotes == ("myremote", "other")
    assert result.conf_present is True
    assert result.conf_path == str(conf)
    assert result.reason is None
    assert probe.last_argv == ["rclone", "listremotes"]


async def test_probe_missing_binary_degrades(tmp_path):
    """缺 rclone 二进制 → available=False + 可操作原因（不抛异常）。"""
    probe = _ExecProbe(_probe_settings(tmp_path), lambda: (_ for _ in ()).throw(FileNotFoundError()))
    result = await probe.list_remotes()
    assert result.available is False
    assert result.remotes == ()
    assert "rclone" in (result.reason or "")


async def test_probe_timeout_degrades(tmp_path):
    """超时 → available=False + 超时原因（kill 由 _exec 负责，探测层只降级）。"""
    probe = _ExecProbe(_probe_settings(tmp_path), lambda: (_ for _ in ()).throw(TimeoutError()))
    result = await probe.list_remotes()
    assert result.available is False
    assert "超时" in (result.reason or "")


async def test_probe_nonzero_exit_reports_output(tmp_path):
    """退出码非零 → available=False，原因取合并输出前 200 字（禁止静默）。"""
    probe = _ExecProbe(_probe_settings(tmp_path), lambda: (1, "Failed to configure: boom\n"))
    result = await probe.list_remotes()
    assert result.available is False
    assert result.reason == "Failed to configure: boom"


async def test_probe_empty_conf_hint(tmp_path):
    """conf 缺失 + 空清单 → 补一条挂载指引原因（Q17 部署入口；available 仍为 True）。"""
    missing = tmp_path / "nope.conf"
    probe = _ExecProbe(_probe_settings(tmp_path, str(missing)), lambda: (0, "\n"))
    result = await probe.list_remotes()
    assert result.available is True
    assert result.conf_present is False
    assert result.remotes == ()
    assert "挂载" in (result.reason or "")


async def test_probe_default_conf_path_when_unset(tmp_path, monkeypatch):
    """未设 RCLONE_CONFIG → conf_present 回落 rclone 默认位置（$HOME/.config/rclone）。"""
    fake_home = tmp_path / "home"
    (fake_home / ".config" / "rclone").mkdir(parents=True)
    (fake_home / ".config" / "rclone" / "rclone.conf").write_text("", encoding="utf-8")
    monkeypatch.setenv("HOME", str(fake_home))
    probe = _ExecProbe(_probe_settings(tmp_path, None), lambda: (0, ""))
    result = await probe.list_remotes()
    assert result.conf_path is None
    assert result.conf_present is True


# ---------------------------------------------------------------- API 端点与写入路径校验


async def test_api_create_repo_rejects_bad_sftp_url(app_client):
    """POST /api/repos 带非法 sftp URL → 422（校验先于落库）。"""
    ns = app_client
    await finish_bootstrap(ns)
    resp = await ns.client.post(
        "/api/repos",
        json={"id": "r-sftp", "name": "sftp 仓库", "url": "sftp:user:pass@host:/srv/repo"},
        headers=ORIGIN,
    )
    assert resp.status_code == 422, resp.text
    assert await ns.app.state.repos_repo.get("r-sftp") is None  # 未落库


async def test_api_create_repo_rclone_checks_remote(app_client, monkeypatch):
    """POST /api/repos 带 rclone URL → remote 存在性校验（探测替身）+ type 派生 rclone。"""
    ns = app_client
    await finish_bootstrap(ns)
    monkeypatch.setattr(ns.app.state, "rclone_probe", _FakeProbe(_remotes("myremote")))
    resp = await ns.client.post(
        "/api/repos",
        json={"id": "r-rclone", "name": "rclone 仓库", "url": "rclone:myremote:bucket/restic"},
        headers=ORIGIN,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["type"] == "rclone"

    # remote 不在册 → 422
    resp = await ns.client.post(
        "/api/repos",
        json={"id": "r-rclone2", "name": "坏 remote", "url": "rclone:typo:bucket"},
        headers=ORIGIN,
    )
    assert resp.status_code == 422
    assert await ns.app.state.repos_repo.get("r-rclone2") is None


async def test_api_update_repo_url_validated(app_client):
    """PUT /api/repos/{id} 的 url 同样过校验（坏值不进库）。"""
    ns = app_client
    await finish_bootstrap(ns)
    await ns.app.state.repos_repo.create("r-upd", "更新用例")
    resp = await ns.client.put(
        "/api/repos/r-upd",
        json={"url": "azure:MyContainer:/prefix"},
        headers=ORIGIN,
    )
    assert resp.status_code == 422
    repo = await ns.app.state.repos_repo.get("r-upd")
    assert repo.url is None


async def test_api_rclone_remotes_endpoint(app_client, monkeypatch):
    """GET /api/rclone/remotes：只读探测结果原样出口（Q17：无凭据字段）。"""
    ns = app_client
    await finish_bootstrap(ns)
    monkeypatch.setattr(
        ns.app.state,
        "rclone_probe",
        _FakeProbe(
            RcloneRemotes(
                available=True,
                conf_present=True,
                conf_path="/etc/rclone/rclone.conf",
                remotes=("myremote", "other"),
                reason=None,
            )
        ),
    )
    resp = await ns.client.get("/api/rclone/remotes", headers=ORIGIN)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["available"] is True
    assert body["conf_present"] is True
    assert body["remotes"] == ["myremote", "other"]
    assert body["reason"] is None
    # 契约冻结：响应绝不携带 conf 内容/凭据形态字段
    assert set(body) == {"available", "conf_present", "conf_path", "remotes", "reason"}
