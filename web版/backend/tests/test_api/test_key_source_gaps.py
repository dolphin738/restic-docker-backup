"""密钥源归属缺口（诊断期红测试，2026-11-01）。

三条用例各自锁定一个「代码承诺 ≠ 实际行为」的缺口。**它们是 red-capable 的**：
在修复前必然失败，修复后必然通过。写在这里而不是先修代码，是因为上一轮的结论
全部来自静态阅读，没有一条被真实执行证实过。

① 轮换 default 口令 → 全局敏感键未重加密（ops.py 只匹配 `cred.<repo>.` 前缀，
   而 dump.password / notify.* / metrics.token 恒由 default 实例加密）；
② force 覆盖口令文件 → 旧密文残留（rebuild 分支先清后写，force 分支没有）；
③ 口令文件缺失 + 库里有明文敏感键 → 启动期 migrate_encryption 抛异常
   （main.py 裸调用，无 try；只有 decrypt 分支有 try）。
"""

from __future__ import annotations

from pathlib import Path

from conftest import ORIGIN, finish_bootstrap

from app.db.repositories.repo_repo import DEFAULT_REPO_ID
from app.schemas.api_schemas import RepoStatusResponse
from app.services.crypto import SENSITIVE_EXACT_KEYS, keys_of


class _FakeProc:
    """复刻 ResticCmd.spawn 契约的最小桩（日志文件由 consume 生命周期持有）。"""

    def __init__(self, log_path: Path, exit_code: int = 0) -> None:
        self._exit_code = exit_code
        self.log_path = log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.touch(exist_ok=True)

    async def lines(self):  # noqa: ANN202
        return
        yield  # pragma: no cover

    async def wait(self) -> int:
        return self._exit_code

    def terminate(self) -> None:  # pragma: no cover
        pass

    def kill(self) -> None:  # pragma: no cover
        pass


def _passfile(ns, repo_id: str = "default") -> Path:
    return ns.app.state.settings.passfile_path(repo_id)


# ---------------------------------------------------------------- ① 轮换漏全局键


async def test_rotate_default_keeps_global_sensitive_keys_readable(app_client):
    """轮换 default 口令后，**全局敏感键**（不属于任何 cred.<repo>.* 前缀）必须仍可读。

    缺口：ops.py 的收集条件是 `row.key.startswith(f"cred.{payload.repo_id}.")`，
    故 dump.password / notify.healthchecks_url / notify.webhook.url /
    notify.mail.password / metrics.token 五个键被跳过。它们恒由 default 实例加密
    （crypto.py 的 _repo_id_for_cred_key 对非 cred.* 键 return DEFAULT_REPO_ID），
    口令一换即永久解不开，且没有任何启动期告警（v2 密文既非明文也非 v1，
    migrate_encryption 两个分支都不进）。

    用户可见后果：快照导出 500、通知渠道读不出（告警全哑）、metrics 鉴权失效。
    """
    ns = app_client
    await finish_bootstrap(ns)
    await ns.app.state.repos_repo.update("default", url="local:/main")
    _passfile(ns).write_text("old-pw", encoding="utf-8")

    # 旧密钥下经 E07 录入全局敏感键（落库应为 enc:v2 旧密钥密文）
    resp = await ns.client.put(
        "/api/config",
        json={"patch": {"dump.password": "pg-secret-1"}},
        headers=ORIGIN,
    )
    assert resp.status_code == 200, resp.text
    raw = {r.key: r.value for r in await ns.app.state.config_repo.list_all_raw()}
    assert raw["dump.password"].startswith("enc:v2:")

    async def _fake_spawn(argv, log_path, *, env=None):  # noqa: ANN001, ANN202
        return _FakeProc(Path(log_path), 0)

    ns.app.state.restic.spawn = _fake_spawn  # type: ignore[assignment]
    resp = await ns.client.post(
        "/api/repos/rotate-key",
        json={"repo_id": "default", "new_password": "brand-new-pw"},
        headers=ORIGIN,
    )
    assert resp.status_code == 200, resp.text
    # 口令文件确实已换（⇒ default 实例的派生密钥已切换）
    assert _passfile(ns).read_text(encoding="utf-8") == "brand-new-pw"

    # 断言：全局敏感键必须仍可读。修复前此处抛 CredentialDecryptError。
    assert await ns.app.state.config_repo.get_value("dump.password") == "pg-secret-1"


# ---------------------------------------------------------------- ② force 覆盖残留


async def test_force_overwrite_password_leaves_no_undecryptable_ciphertext(app_client, monkeypatch):
    """force 覆盖口令文件后，该仓库的旧密文必须已被清理，读取不得整表抛错。

    缺口：rebuild 分支（repo_lifecycle）先调 _clear_encrypted_credentials 再写新
    口令文件，注释明写「留到 init 之后会 500」；而 force 覆盖分支
    （repos_status.set_repo_password）写完文件即返回，没有对应的清理调用。

    后果：库里留下永久解不开的密文 → ConfigRepo.list_all() 是**全表逐行解密**，
    于是 GET /config、load_credentials（每个备份任务的必经之路）、配置导出
    全部抛错。一个仓库的口令文件问题放大成全站故障。
    """
    from app.routers import repos_status as repos_router

    async def unreachable(_request, _repo_id):
        return RepoStatusResponse(
            connected=False,
            failure_kind="password",
            last_error="仓库密码错误（请检查仓库密码文件挂载）",
        )

    monkeypatch.setattr(repos_router, "_probe_repo_status", unreachable)

    ns = app_client
    await finish_bootstrap(ns)
    _passfile(ns).write_text("old-pw", encoding="utf-8")

    resp = await ns.client.put(
        "/api/config",
        json={"patch": {"cred.default.aws_secret_access_key": "sekret-1"}},
        headers=ORIGIN,
    )
    assert resp.status_code == 200, resp.text

    resp = await ns.client.post(
        "/api/repos/default/password",
        json={"password": "brand-new-secret", "force": True},
        headers=ORIGIN,
    )
    assert resp.status_code == 200, resp.text
    assert _passfile(ns).read_text(encoding="utf-8") == "brand-new-secret"

    # 断言：全表读取不得抛错（残留密文须已被清理）。修复前抛 CredentialDecryptError。
    rows = await ns.app.state.config_repo.list_all()
    assert isinstance(rows, list)
    # 且凭据确实已被清理（读回 None），而不是"侥幸能解开"。
    # 2026-11-02 第 2 批修正：原断言写的是 raises(CredentialDecryptError)，但方案 §9.3
    # 要求的终态是「旧密文均已被清」——键被删后读回 None，不会再抛。两者互斥，
    # 以方案 §9.3 为准（残留密文正是缺口 2 的放大器，不能为了断言而保留它）。
    assert await ns.app.state.config_repo.get_value("cred.default.aws_secret_access_key") is None


# ---------------------------------------------------------------- ③ 启动期硬崩


async def test_startup_migration_with_missing_passfile_does_not_crash(app_client):
    """口令文件缺失 + 库里有明文敏感键 → 启动期加密迁移不得抛异常。

    缺口：main.py 裸调 `await config_repo.migrate_encryption(encrypt=True)`，无 try；
    而 migrate_encryption 的「明文 → 加密」分支直接 cr.encrypt(...)，不在任何
    try/except 里（只有 decrypt 分支有 try）。口令文件缺失时 _derive_key 抛
    CredentialKeyUnavailableError → lifespan 抛 → 启动崩溃循环。
    而 main.py 对「口令文件缺失」只 logger.warning，不阻止启动，于是每次重启都崩。

    断言：迁移应**降级跳过**并计数，而不是让进程起不来。
    """
    ns = app_client
    await finish_bootstrap(ns)

    # 直接落一条明文敏感键（模拟「加密开关关闭期间写入」的存量形态）
    await ns.app.state.db.write(
        "INSERT INTO config (key, value, source, updated_at) VALUES (?, ?, 'ui', 'now') "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, source = excluded.source",
        ("cred.default.aws_secret_access_key", "plaintext-left-behind"),
    )

    # 口令文件丢失（挂载盘未就位 / 误删）
    _passfile(ns).unlink()

    # 断言：不得抛异常。修复前抛 CredentialKeyUnavailableError。
    migrated = await ns.app.state.config_repo.migrate_encryption(encrypt=True)
    assert isinstance(migrated, int)


# ---------------------------------------------------------------- 结构性护栏


def test_keys_of_covers_all_global_sensitive_keys():
    """护栏：``keys_of(DEFAULT_REPO_ID)`` 必须覆盖全部全局敏感键，且不含非敏感键。

    缺口 1/2/4 的共同根因是「键归属被手抄在四处」。本用例锁死单一出口的覆盖面：
    未来谁再加一个全局敏感键，只要它进 ``SENSITIVE_EXACT_KEYS``，就自动归 default
    所有——轮换/清理/回收三处不必再各改一遍（改了也不会漏）。
    """
    all_keys = list(SENSITIVE_EXACT_KEYS) + [
        "log.level",
        "notify.on_success",
        "cred.default.aws_secret_access_key",
        "cred.other.aws_secret_access_key",
    ]
    owned = keys_of(DEFAULT_REPO_ID, all_keys)
    # 5 个全局敏感键一个不能少（dump.password / notify.* ×3 / metrics.token）
    assert len(SENSITIVE_EXACT_KEYS) == 5
    assert set(SENSITIVE_EXACT_KEYS) <= owned
    # 非敏感明文键不得进入结果（调用方的 is_encrypted 过滤不是唯一保险）
    assert "log.level" not in owned
    assert "notify.on_success" not in owned
    # 归属判定的另一侧：其他仓库的凭据键不归 default
    assert "cred.other.aws_secret_access_key" not in owned
    assert "cred.default.aws_secret_access_key" in owned
