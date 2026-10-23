"""批次① 部署形态检测（设计方案 §3.1–§3.3）：单元 + API 覆盖。

- 检测链：显式 ``DEPLOY_MODE`` 优先、非法值回落 auto、``/.dockerenv``、根分区 overlay 兜底；
- 单一事实源：``effective_browse_root``（docker→``/sources``、host→``/``）；
- host deny：``host_deny_prefixes`` + ``PathMapper.validate`` 拦截（防递归备份 / 泄露应用数据）；
- 内置排除集前缀随 ``browse_root`` 派生（docker→``/sources/proc``、host→``/proc``）；
- ``GET /api/system/runtime`` 下发（鉴权门 + 双形态）。
"""

from __future__ import annotations

import pytest
from conftest import finish_bootstrap

from app.constants import DEPLOY_MODE_DOCKER, DEPLOY_MODE_HOST
from app.services import deploy_mode
from app.services.path_mapper import PathMapper, PathValidationError
from app.settings import Settings


@pytest.fixture(autouse=True)
def _clear_deploy_cache():
    """检测结果是进程级缓存；每用例前后清空，避免 (mode, sources_root) 键串味。"""
    deploy_mode.invalidate_deploy_mode_cache()
    yield
    deploy_mode.invalidate_deploy_mode_cache()


# ---------------------------------------------------------------- 检测链


def test_explicit_docker():
    s = Settings(deploy_mode="docker")
    assert deploy_mode.detect_deploy_mode(s) == DEPLOY_MODE_DOCKER
    assert deploy_mode.effective_browse_root(s) == "/sources"


def test_explicit_host():
    s = Settings(deploy_mode="host")
    assert deploy_mode.detect_deploy_mode(s) == DEPLOY_MODE_HOST
    assert deploy_mode.effective_browse_root(s) == "/"


def test_invalid_value_falls_back_to_auto(monkeypatch):
    """非法显式值降级 auto（留痕），不抛异常。"""
    monkeypatch.setattr(deploy_mode, "_auto_detect", lambda: DEPLOY_MODE_DOCKER)
    s = Settings(deploy_mode="bogus")
    assert deploy_mode.detect_deploy_mode(s) == DEPLOY_MODE_DOCKER


def test_auto_dockerenv_present(monkeypatch, tmp_path):
    env_file = tmp_path / ".dockerenv"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(deploy_mode, "DOCKER_ENV_FILE", str(env_file))
    s = Settings(deploy_mode="auto")
    assert deploy_mode.detect_deploy_mode(s) == DEPLOY_MODE_DOCKER


def test_auto_overlay_root(monkeypatch, tmp_path):
    mountinfo = tmp_path / "mountinfo"
    # 字段索引：4=挂载点("/")、8=文件系统类型("overlay")——见 _root_is_overlay 文档
    mountinfo.write_text("36 35 98:0 / / rw,relatime - overlay overlay rw\n", encoding="utf-8")
    monkeypatch.setattr(deploy_mode, "DOCKER_ENV_FILE", str(tmp_path / "nope"))
    monkeypatch.setattr(deploy_mode, "MOUNTINFO_PATH", str(mountinfo))
    s = Settings(deploy_mode="auto")
    assert deploy_mode.detect_deploy_mode(s) == DEPLOY_MODE_DOCKER


def test_auto_defaults_to_host_without_signal(monkeypatch, tmp_path):
    monkeypatch.setattr(deploy_mode, "DOCKER_ENV_FILE", str(tmp_path / "nope"))
    monkeypatch.setattr(deploy_mode, "MOUNTINFO_PATH", str(tmp_path / "nope-mountinfo"))
    s = Settings(deploy_mode="auto")
    assert deploy_mode.detect_deploy_mode(s) == DEPLOY_MODE_HOST
    assert deploy_mode.effective_browse_root(s) == "/"


# ---------------------------------------------------------------- host deny


def test_host_deny_prefixes_include_base_and_app_paths():
    s = Settings(deploy_mode="host", repo_root="/repo", data_dir="/data", passfile_root="/data/repos")
    denies = deploy_mode.host_deny_prefixes(s)
    for expected in ("/proc", "/sys", "/dev", "/run", "/repo", "/data"):
        assert expected in denies


def test_validate_rejects_repo_and_data_in_host_mode():
    """host 模式：仓库存储位置 / 应用数据 / 伪目录不可选作备份源；普通路径放行。"""
    s = Settings(deploy_mode="host", repo_root="/repo", data_dir="/data", passfile_root="/data/repos")
    mapper = PathMapper(s)
    allowed = (s.browse_root,)  # ("/",)
    for bad in ("/repo", "/repo/restic", "/data", "/data/restic.db", "/proc/uptime"):
        with pytest.raises(PathValidationError):
            mapper.validate(bad, allowed_prefixes=allowed)
    assert mapper.validate("/home/user/data", allowed_prefixes=allowed) == "/home/user/data"


def test_validate_docker_outside_sources_rejected_by_prefix():
    """docker 模式：/repo 本就在 /sources 之外 → 前缀校验即拒（deny 只是额外一层）。"""
    s = Settings(deploy_mode="docker", sources_root="/sources", repo_root="/repo")
    mapper = PathMapper(s)
    allowed = (s.browse_root,)  # ("/sources",)
    assert mapper.validate("/sources/etc", allowed_prefixes=allowed) == "/sources/etc"
    with pytest.raises(PathValidationError):
        mapper.validate("/repo", allowed_prefixes=allowed)


# ---------------------------------------------------------------- 内置排除集前缀派生


def test_builtin_excludes_follow_browse_root_docker():
    from app.exclusions import ExclusionRules

    rules = ExclusionRules(Settings(deploy_mode="docker", sources_root="/sources")).built_in()
    assert "/sources/proc" in rules
    assert "/proc" not in rules


def test_builtin_excludes_follow_browse_root_host():
    from app.exclusions import ExclusionRules

    rules = ExclusionRules(Settings(deploy_mode="host")).built_in()
    assert "/proc" in rules
    assert "/sources/proc" not in rules


def test_normalize_user_excludes_host_mode_keeps_absolute_path():
    from app.exclusions import normalize_user_excludes

    s = Settings(deploy_mode="host", repo_root="/repo", data_dir="/data")
    mapper = PathMapper(s)
    out = normalize_user_excludes(
        ["/etc/*.conf", "*.tmp"], mapper, source_prefixes=(s.browse_root,)
    )
    assert "/etc/*.conf" in out
    assert "*.tmp" in out


# ---------------------------------------------------------------- API 下发


async def test_runtime_requires_session(make_app):
    ns = await make_app()
    resp = await ns.client.get("/api/system/runtime")
    assert resp.status_code in (401, 403)


async def test_runtime_docker(make_app):
    ns = await make_app()  # api_env 已钉 DEPLOY_MODE=docker
    await finish_bootstrap(ns)
    resp = await ns.client.get("/api/system/runtime")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deploy_mode"] == "docker"
    assert body["browse_root"] == "/sources"
    assert body["sources_root"] == "/sources"
    assert body["restore_root"] == "/restore"
    assert body["deny_prefixes"] == []


async def test_runtime_host(make_app):
    ns = await make_app({"DEPLOY_MODE": "host"})
    await finish_bootstrap(ns)
    resp = await ns.client.get("/api/system/runtime")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deploy_mode"] == "host"
    assert body["browse_root"] == "/"
    assert "/proc" in body["deny_prefixes"]
    assert "/repo" in body["deny_prefixes"]
