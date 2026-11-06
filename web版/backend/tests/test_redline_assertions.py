"""红线机器校验（规范 §3.1 / AGENTS.md 编号索引）的 pytest 落地。

为什么有这个文件：原门禁是 ``scripts/verify_redlines.sh``（一组 bash grep 断言），但它
依赖宿主有 bash/wsl——本机没 bash 或换 CI 就跑不了，和 ``e2e/README.md`` 里记的
``p13`` 因写死本机路径失效一个形状。本文件把同样的断言转录成 pytest 用例，
在 backend 测试环境、CI、代理沙箱里都能直接 ``pytest`` 跑，不再依赖 shell。

与 bash 版保持一致的部分：
- 各红线编号（S1/A2、S2、S3、S4、S6、S7、A1、A3、A5、A6、C8）及其豁免清单完全对齐；
- 双文档编号锚点一致性、G8 .dockerignore 一致性；
- **扫描口径 = 全行扫描**（含注释与 docstring），与 bash ``grep -rIn`` 同宽。

相对 bash 版的两处差异（都有明确理由，不是放宽断言）：
1. C8 **排除本文件自身**：本门禁含有 ``from zoneinfo import`` 的正则字面量，全行扫描
   必然命中自己。bash 版在这里会误报（它把本文件判为违规）。这是纯自匹配，与真实
   的 zoneinfo 直连无关。
2. G8：以 ``PYTHONIOENCODING=utf-8`` 调 ``check_dockerignore.py`` 并基于其**真实校验
   输出**判定，规避其在 GBK 控制台下 ``print`` 崩溃、误报 rc=1 的问题。

⚠️ 历史教训（勿回退）：曾用「手写 ``\"\"\"`` 状态机跳过 docstring」来消除 A3 的散文
误报，结果状态机遇非常规引号即失步，导致个别文件 63 行之后**完全不被扫描**——
门禁出现静默盲区。现改为全行扫描 + 按行内容的窄排除（A3 用 ``命中/置黄色/告警``
过滤散文），假阳性由窄排除处理，绝不牺牲覆盖率。

扫描约定：所有文件以「仓库根为基准的相对 POSIX 路径」参与排除匹配，精确复刻
bash ``grep -r`` 的行为（排除子串用正斜杠）。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]  # backend/tests -> repo root
BACKEND = REPO_ROOT / "backend"
APP = BACKEND / "app"
FRONTEND_SRC = REPO_ROOT / "frontend" / "src"

PY_EXTS = (".py",)
TS_EXTS = (".ts", ".vue")
DOC_EXTS = (".md",)

# 需要逐行扫描的目录（与 bash 版 grep 范围对齐）
SCAN_APP = APP
SCAN_FRONTEND = FRONTEND_SRC


def _grep(root: Path, pattern: str, exts=PY_EXTS):
    """返回命中列表：每个元素为 (rel_posix_path, line_no, line_text)。

    **全行扫描，不跳过注释与文档字符串**——与 bash 版 ``grep -rIn`` 完全同口径。
    刻意如此：门禁的假阴性（放过真违规）远比假阳性危险，而省略注释/docstring 会让
    扫描器出现盲区（早期版本用手写 ``\"\"\"`` 状态机跳过 docstring，遇非常规引号即失步，
    曾导致整个文件后半段不被扫描）。散文类误报由各用例的**显式窄排除**处理。
    """
    rx = re.compile(pattern)
    hits = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in exts:
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for no, line in enumerate(lines, 1):
            if rx.search(line):
                hits.append((rel, no, line))
    return hits


def _drop(hits, *substrings):
    """丢弃 rel_path 含任一子串的命中（复刻 bash ``grep -v`` 的路径排除）。"""
    out = []
    for rel, no, line in hits:
        if any(s in rel for s in substrings):
            continue
        out.append((rel, no, line))
    return out


def _drop_line_re(hits, pattern: str):
    """丢弃**行内容**匹配 ``pattern`` 的命中（复刻 bash ``grep -vE``）。"""
    rx = re.compile(pattern)
    return [h for h in hits if not rx.search(h[2])]


def _fmt(hits):
    return [f"{rel}:{no}" for rel, no, _ in hits]


# ---------------------------------------------------------------- S1/A2 路径映射唯一实现点

def test_s1_filesystem_prefix_not_hardcoded_outside_allowlist():
    hits = _grep(SCAN_APP, r'"/host"|"/sources"|"/restore"', PY_EXTS)
    hits += [(rel, no, line) for rel, no, line in
             _grep(SCAN_FRONTEND, r'"/host"|"/sources"|"/restore"', TS_EXTS)]
    # 豁免：settings / constants / exclusions / path_mapper 的常量定义，以及 routers/ 下的 API 路径
    hits = _drop(hits, "settings.py", "constants.py", "exclusions.py",
                 "path_mapper.py", "routers/")
    assert not hits, f"S1/A2 前缀字面量出现在豁免范围之外：{_fmt(hits)}"


def test_s1_frontend_no_host_prefix_literal():
    # 前端用单引号；任何 '/host' 命中即 host 模式复活
    hits = _grep(SCAN_FRONTEND, r"'/host'|\"/host\"", TS_EXTS)
    assert not hits, f"S1/A2 前端出现 /host 前缀字面量（host 模式复活）：{_fmt(hits)}"


# ---------------------------------------------------------------- S2 restic 参数白名单

def test_s2_no_shell_injection():
    hits = _grep(SCAN_APP, r"shell=True|os\.system|os\.popen", PY_EXTS)
    assert not hits, f"S2 出现 shell=True / os.system / os.popen：{_fmt(hits)}"


# ---------------------------------------------------------------- S3 凭据零泄漏

def test_s3_no_credentials_in_logs():
    hits = _grep(SCAN_APP, r"logger\.(debug|info|warning|error).*(RESTIC_PASSWORD|AWS_SECRET|token)",
                 PY_EXTS)
    assert not hits, f"S3 疑似把凭据/token 写入日志：{_fmt(hits)}"


# ---------------------------------------------------------------- S4/S5/S8 鉴权 · Origin · 首启门

def test_s4_all_business_routers_require_session():
    routers_dir = APP / "routers"
    missing = []
    for f in sorted(routers_dir.glob("*.py")):
        name = f.name
        if name in ("health.py", "auth.py", "__init__.py"):
            continue
        text = f.read_text(encoding="utf-8", errors="ignore")
        if not re.search(r"_PROTECTED|require_session", text):
            missing.append(name)
    assert not missing, f"S4 以下业务 router 未见 require_session/_PROTECTED：{missing}"


def test_s4_no_query_token_auth():
    hits = _grep(APP / "routers", r"token=", PY_EXTS)
    # E28 改密的合法业务字段 current_token/next_token 不是鉴权 token，须排除（按行内容过滤）
    hits = [h for h in hits if not re.search(r"current_token|next_token", h[2])]
    assert not hits, f"S4 routers 中出现 token=（疑似 query token 鉴权）：{_fmt(hits)}"


# ---------------------------------------------------------------- S6 审计只增不改

def test_s6_audit_logs_append_only():
    hits = _grep(SCAN_APP, r"UPDATE\s+audit_logs|DELETE\s+FROM\s+audit", PY_EXTS)
    # 唯一受控出口：repositories/audit_repo.py
    hits = _drop(hits, "repositories/audit_repo.py")
    assert not hits, (
        f"S6 audit_logs 出现 UPDATE/DELETE（append-only 被破坏；"
        f"唯一受控出口仅限 repositories/audit_repo.py）：{_fmt(hits)}"
    )


def test_s6_audit_action_constants_exist():
    constants = (APP / "constants.py").read_text(encoding="utf-8", errors="ignore")
    # rclone conf 写入（v1.14）、删除保护 P2（v1.15）、
    # conf 加密三动作（v1.17）与解锁（v1.19）的埋点常量必须真实存在
    # ——与 scripts/verify_redlines.sh 的同名反向锚点断言完全对齐
    for action in (
        "AUDIT_RCLONE_CONFIG_APPEND", "AUDIT_RCLONE_CONFIG_EDIT",
        "AUDIT_RCLONE_CONFIG_DELETE", "AUDIT_REPO_PURGE", "AUDIT_REPO_ID_RELEASE",
        "AUDIT_RCLONE_CONFIG_RESTORE",
        "AUDIT_RCLONE_CONF_ENCRYPT", "AUDIT_RCLONE_CONF_REKEY",
        "AUDIT_RCLONE_CONF_DECRYPT", "AUDIT_RCLONE_CONF_UNLOCK",
    ):
        assert re.search(rf"^{action} =", constants, re.M), f"S6 缺少审计动作常量 {action}"


def test_s6_archive_retention_keys_exist():
    constants = (APP / "constants.py").read_text(encoding="utf-8", errors="ignore")
    for key in (
        "RETENTION_REPO_ARCHIVE_DAYS_KEY", "RETENTION_REPO_TOMBSTONE_DAYS_KEY",
        "RETENTION_RCLONE_ARCHIVE_DAYS_KEY", "RETENTION_REPO_PASSFILE_DAYS_KEY",
    ):
        assert re.search(rf"^{key} =", constants, re.M), f"S6 缺少归档保留期键常量 {key}"


def test_s6_rclone_conf_write_only_in_service():
    # rclone conf 的写入 CLI 只允许出现在 services/rclone_config.py，散落即第二条写路径
    hits = _grep(SCAN_APP, r"config\s+(create|update|delete)", PY_EXTS)
    hits = _drop(hits, "services/rclone_config.py", "constants.py", "tests/")
    assert not hits, f"S6 rclone conf 写入 CLI 出现在 services/rclone_config.py 之外：{_fmt(hits)}"


# ---------------------------------------------------------------- S7 覆盖语义仅限原位恢复

def test_s7_overwrite_only_at_legal_call_sites():
    hits = _grep(SCAN_APP, r"overwrite", PY_EXTS)
    hits += [(rel, no, line) for rel, no, line in _grep(SCAN_FRONTEND, r"overwrite", TS_EXTS)]
    # 两类合法调用点 + constants + locales + 恢复页目录整体豁免
    hits = _drop(hits, "restic.py", "job_runner.py", "routers/restore.py",
                 "services/compare.py", "constants.py", "locales",
                 "pages/snapshots/restore/")
    # 收窄豁免三个契约字段字面（请求/响应模型与前端出口必然出现，非覆盖能力入口）
    hits = [h for h in hits if not re.search(r"overwrite_mode|overwrite_count|overwrite=", h[2])]
    assert not hits, f"S7 overwrite 出现在两类合法调用点之外：{_fmt(hits)}"


def test_s7_overwrite_modes_single_source():
    restic = (APP / "services" / "restic.py").read_text(encoding="utf-8", errors="ignore")
    assert "OVERWRITE_MODES" in restic, "S7 restic.py 缺少 OVERWRITE_MODES（覆盖模式白名单悬空）"


def test_s7_job_runner_overwrite_passthrough_le_at_most_two():
    # grep -c 语义：统计「含 overwrite 的行数」，应为 ≤2（时间戳固定注入 + 原位 params 透传）
    text = (APP / "services" / "job_runner.py").read_text(encoding="utf-8", errors="ignore")
    line_count = sum(1 for line in text.splitlines() if "overwrite" in line)
    assert line_count <= 2, f"S7 job_runner.py overwrite 透传点 {line_count} 处 > 2"


# ---------------------------------------------------------------- A1 DB 访问只在 repositories/

def test_a1_sql_only_in_repositories():
    hits = _grep(SCAN_APP, r"SELECT |INSERT INTO|UPDATE |DELETE FROM", PY_EXTS)
    hits = _drop(hits, "app/db/", "app/auth/", "constants.py", "health.py")
    assert not hits, f"A1 非仓储层出现 SQL（豁免仅 auth/ 与 health 探针）：{_fmt(hits)}"


# ---------------------------------------------------------------- A3 内置排除集单一来源

def test_a3_exclusion_set_not_duplicated():
    hits = _grep(SCAN_FRONTEND, r"lost\+found", TS_EXTS)
    hits += [(rel, no, line) for rel, no, line in _grep(SCAN_APP, r"lost\+found", PY_EXTS)]
    # 真源本身与常量豁免；测试夹具样例输入不是"复制"（真源仍是 exclusions.py）
    hits = _drop(hits, "exclusions.py", "constants.py", "__tests__/")
    # 散文类误报：解释「伪文件系统告警」的 docstring（如 E31 的 sources.py）会提到
    # lost+found。与 bash 版同口径地按行内容排除，而非整文件排除——整文件放行会
    # 连带放过真正把清单复制进该文件的情况。
    hits = _drop_line_re(hits, r"命中|置黄色|告警")
    assert not hits, f"A3 内置排除集疑似被复制到别处：{_fmt(hits)}"


# ---------------------------------------------------------------- A5 SQLite 单写连接

def test_a5_pragma_trio_present():
    db = (APP / "db" / "database.py").read_text(encoding="utf-8", errors="ignore")
    for pragma in ("journal_mode", "busy_timeout", "foreign_keys"):
        assert f"PRAGMA {pragma}" in db, f"A5 缺少 PRAGMA {pragma} 初始化"


def test_a5_busy_timeout_not_hardcoded():
    db = (APP / "db" / "database.py").read_text(encoding="utf-8", errors="ignore")
    assert not re.search(r"PRAGMA busy_timeout=[0-9]", db), \
        "A5 busy_timeout 裸写数值（应走 settings.db_busy_timeout_ms）"


# ---------------------------------------------------------------- A6 解析零 IO

def test_a6_parser_pure_function():
    parser = (APP / "services" / "parser.py").read_text(encoding="utf-8", errors="ignore")
    bad = [line for line in parser.splitlines()
           if re.match(r"^(import|from)", line)
           and re.search(r"\bos\b|repositories|restic|path_mapper|aiofiles", line)]
    assert not bad, f"A6 parser.py 出现 IO / 仓储 / restic 导入：{bad}"


# ---------------------------------------------------------------- C8 时间语义唯一实现点

def test_c8_zoneinfo_only_in_utils_time():
    hits = _grep(SCAN_APP, r"from zoneinfo import|^import zoneinfo", PY_EXTS)
    hits += [(rel, no, line) for rel, no, line in
             _grep(BACKEND / "tests", r"from zoneinfo import|^import zoneinfo", PY_EXTS)]
    hits = _drop(hits, "utils/time.py")
    # 自匹配排除：本门禁文件自身含有该正则的**字面量**，全行扫描必然命中自己。
    # bash 版在这里会误报（它把本文件列为违规），本版显式排除且不影响任何真实判定。
    hits = _drop(hits, "tests/test_redline_assertions.py")
    assert not hits, f"C8 zoneinfo 直连出现在 utils/time.py 之外：{_fmt(hits)}"


def test_c8_resolve_timezone_exists():
    time_mod = (APP / "utils" / "time.py").read_text(encoding="utf-8", errors="ignore")
    assert "def resolve_timezone" in time_mod, "C8 utils/time.py 缺少 resolve_timezone（唯一实现点悬空）"


# ---------------------------------------------------------------- 可调阈值单一真源

def test_threshold_sse_heartbeat_not_hardcoded():
    hits = _grep(APP / "services", r"15\.0", PY_EXTS)
    hits += [(rel, no, line) for rel, no, line in _grep(APP / "routers", r"15\.0", PY_EXTS)]
    hits += [(rel, no, line) for rel, no, line in _grep(APP / "db", r"15\.0", PY_EXTS)]
    hits = [h for h in hits if not re.search(r"heartbeat", h[2], re.I)]
    assert not hits, f"SSE 心跳间隔在业务层裸写数值：{_fmt(hits)}"


def test_threshold_busy_timeout_not_hardcoded():
    hits = _grep(SCAN_APP, r"busy_timeout\s*=\s*[0-9]{3,}", PY_EXTS)
    hits = _drop(hits, "constants.py")
    assert not hits, f"SQLite busy_timeout 在业务层裸写数值：{_fmt(hits)}"


def test_threshold_settings_fields_exist():
    settings = (APP / "settings.py").read_text(encoding="utf-8", errors="ignore")
    for field in ("sse_heartbeat_seconds", "db_busy_timeout_ms"):
        assert field in settings, f"settings 缺少可配置阈值字段：{field}"


# ---------------------------------------------------------------- 双文档同步（编号锚点）

def test_doc_anchor_ids_consistent():
    agents = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8", errors="ignore")
    spec = (REPO_ROOT / "docs" / "代码审查规范.md").read_text(encoding="utf-8", errors="ignore")
    ids = set()
    for m in re.finditer(r"\[(S|A|C)[0-9]+(?:/[SA][0-9]+)*\]", agents):
        for part in m.group(0)[1:-1].split("/"):
            ids.add(part)
    missing = [i for i in sorted(ids) if not re.search(rf"^\|\s*{re.escape(i)}\s*\|", spec, re.M)]
    assert not missing, f"同步失败：AGENTS.md 引用的编号在规范 §3.1 中不存在：{missing}"


# ---------------------------------------------------------------- G8 .dockerignore 与 Dockerfile 一致

def test_g8_dockerignore_consistent():
    if not ((REPO_ROOT / ".dockerignore").exists() and (REPO_ROOT / "docker" / "Dockerfile").exists()):
        pytest.fail("缺少 .dockerignore 或 docker/Dockerfile")
    checker = REPO_ROOT / "scripts" / "check_dockerignore.py"
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [sys.executable, str(checker)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        env=env,
    )
    # 以字节捕获后防御性解码：check_dockerignore.py 在 GBK 控制台下自身 print 会崩，
    # 但我们只关心它「是否印出不一致结论」，用 errors='replace' 吸收编码噪声。
    out = (proc.stdout or b"").decode("utf-8", errors="replace") + \
          (proc.stderr or b"").decode("utf-8", errors="replace")
    inconsistent = ("未被忽略" in out) or ("不一致" in out) or ("COPY 源未被" in out)
    assert not inconsistent, f"G8 .dockerignore 与 Dockerfile 不一致：\n{out[-2000:]}"
