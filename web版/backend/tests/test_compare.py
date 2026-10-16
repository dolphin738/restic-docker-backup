"""比对层单测：E20 CompareService（R-015，增量设计 §3.3 / §7 测试策略）。

覆盖：

- **四矩阵**：mtime 同/size 异、mtime 异/size 同、双异、双同（+ 目标不存在 = 新建）；
- **TOCTOU 计数**：预览后改写磁盘现状，断言再次比对回报**当下实际值**（Q4 口径：
  预览值只承诺调用时点，不做缓存）；
- **截断标记**：restic ``ls`` 输出被截断 → ``truncated`` 透出；
- **游标编码边界**（E18/E20 共用的 ``app.utils.cursor``）。

restic 子进程与磁盘 ``os.stat`` 均用替身（零真机依赖）；解析层用**真实**
:class:`OutputParser`，保证解析口径与生产一致（A6：解析改 fixture 先行）。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.constants import SNAPSHOT_FILES_MAX_OFFSET
from app.services import compare as compare_module
from app.services.compare import CompareService
from app.services.parser import OutputParser
from app.services.restic import ProbeResult
from app.utils.cursor import decode_cursor, encode_cursor
from app.utils.time import parse_iso

SNAPSHOT_ID = "a" * 64
ROOT = "/host/etc"
SNAPSHOT_MTIME = "2026-01-03T12:00:00Z"
SNAPSHOT_SIZE = 512

def _ls_line(path: str, *, size: int, mtime: str, node_type: str = "file") -> str:
    """构造一条 ``ls --json`` 记录（NDJSON；目录 type 为 dir）。"""
    return json.dumps(
        {
            "name": path.rsplit("/", 1)[-1],
            "type": node_type,
            "path": path,
            "size": size,
            "mtime": mtime,
            "struct_type": "node",
        }
    )


# 快照根 /host/etc 下：一个同级文件 + 一个子目录（子目录内一个文件）+ 目录条目。
# restic ls 会把目录一并列出：比对必须只记文件（目录 noise 不是覆盖语义）
LS_LINES = [
    _ls_line(ROOT, size=0, mtime=SNAPSHOT_MTIME, node_type="dir"),
    _ls_line(f"{ROOT}/nginx.conf", size=SNAPSHOT_SIZE, mtime=SNAPSHOT_MTIME),
]
# target ⊊ 快照根（/host/etc/nginx）场景：target 之外的同级文件 hosts 必须被排除
LS_LINES_NESTED = [
    _ls_line(ROOT, size=0, mtime=SNAPSHOT_MTIME, node_type="dir"),
    _ls_line(f"{ROOT}/hosts", size=64, mtime=SNAPSHOT_MTIME),
    _ls_line(f"{ROOT}/nginx", size=0, mtime=SNAPSHOT_MTIME, node_type="dir"),
    _ls_line(f"{ROOT}/nginx/nginx.conf", size=SNAPSHOT_SIZE, mtime=SNAPSHOT_MTIME),
]
SNAPSHOTS_LINES = [
    json.dumps(
        [
            {
                "id": SNAPSHOT_ID,
                "short_id": SNAPSHOT_ID[:8],
                "time": "2026-01-03T12:00:00.000000000Z",
                "tree": "0" * 64,
                "paths": [ROOT],
                "hostname": "nas",
                "tags": ["task-1"],
            }
        ]
    ),
]


def _epoch(value: str) -> float:
    """ISO → epoch（走 utils/time，C8 时间语义唯一实现点）。"""
    moment = parse_iso(value)
    assert moment is not None
    return moment.timestamp()


class _FakeOs:
    """``os.stat`` 替身：以 ``{路径: (mtime_epoch, size)}`` 模拟磁盘现状。"""

    def __init__(self, table: dict[str, tuple[float, int]]) -> None:
        self.table = table
        self.calls: list[str] = []

    def stat(self, path: str) -> SimpleNamespace:
        """返回 stat 形状对象；路径未登记即视为不存在（OSError）。"""
        self.calls.append(path)
        try:
            mtime, size = self.table[path]
        except KeyError:
            raise OSError(2, "No such file or directory", path) from None
        return SimpleNamespace(st_mtime=mtime, st_size=size)


class _FakeConfigRepo:
    """配置仓储替身：只提供 ``repo.url`` 与空凭据清单。"""

    def __init__(self) -> None:
        self.values = {"repo.url": "/repo"}

    async def get_value(self, key: str) -> str | None:
        """读取配置值。"""
        return self.values.get(key)

    async def list_all(self) -> list:
        """凭据清单（空 = 无 cred.* 键）。"""
        return []


class _FakeRestic:
    """``ResticCmd`` 替身：按 verb 回放预录输出，并记录 argv 供断言。"""

    def __init__(self, lines: dict[str, list[str]], *, truncated: bool = False) -> None:
        self._lines = lines
        self._truncated = truncated
        self.argv_log: list[list[str]] = []

    def build(self, verb: str, *, args, env_extras=None) -> list[str]:  # noqa: ANN001
        """拼一个可辨识 verb 与位置参数的 argv（不重复实现白名单）。"""
        argv = ["restic", "--json", verb]
        if getattr(args, "snapshot_id", None):
            argv.append(str(args.snapshot_id))
        argv.extend(getattr(args, "extra_positionals", ()) or ())
        return argv

    def build_env(self, *, repo_url: str, repo_id: str = "default", env_extras=None) -> dict[str, str]:  # noqa: ANN001
        """最小化环境（不含任何凭据，S3）。"""
        return {"RESTIC_REPOSITORY": repo_url}

    async def run_json(self, argv, *, env, timeout_seconds, max_lines=200_000) -> ProbeResult:  # noqa: ANN001
        """回放输出（verb 取自 argv[2]）。"""
        self.argv_log.append(list(argv))
        verb = argv[2]
        return ProbeResult(
            exit_code=0, lines=list(self._lines.get(verb, [])), truncated=self._truncated
        )


def _build(monkeypatch, *, disk: dict[str, tuple[float, int]], truncated: bool = False,
           ls_lines: list[str] | None = None):
    """装配 CompareService + os.stat 替身；返回 (service, fake_os, fake_restic)。"""
    fake_os = _FakeOs(disk)
    fake_restic = _FakeRestic(
        {"ls": ls_lines if ls_lines is not None else LS_LINES, "snapshots": SNAPSHOTS_LINES},
        truncated=truncated,
    )
    monkeypatch.setattr(compare_module, "os", fake_os)
    service = CompareService(fake_restic, OutputParser(), _FakeConfigRepo())  # type: ignore[arg-type]
    return service, fake_os, fake_restic


async def _collect(service: CompareService, target: str = ROOT) -> list:
    """跑完一次扫描并返回覆盖项清单。"""
    scan = service.diff(SNAPSHOT_ID, target)
    return [item async for item in scan]


@pytest.mark.parametrize(
    ("mtime", "size", "expected"),
    [
        (SNAPSHOT_MTIME, SNAPSHOT_SIZE + 1, 1),  # mtime 同 / size 异
        ("2026-02-04T08:30:00Z", SNAPSHOT_SIZE, 1),  # mtime 异 / size 同
        ("2026-02-04T08:30:00Z", SNAPSHOT_SIZE + 1, 1),  # 双异
        (SNAPSHOT_MTIME, SNAPSHOT_SIZE, 0),  # 双同 → 不记
    ],
)
async def test_diff_matrix(monkeypatch, mtime, size, expected):
    """四矩阵：mtime/size 任一不同即记为覆盖项，全同则不计。"""
    disk = {f"{ROOT}/nginx.conf": (_epoch(mtime), size)}
    service, _, _ = _build(monkeypatch, disk=disk)

    items = await _collect(service)

    assert len(items) == expected
    if expected:
        item = items[0]
        # 落点 = 快照内绝对路径原样写回（执行层 --target / 的同基底落点）
        assert item.path == f"{ROOT}/nginx.conf"
        assert item.snapshot_mtime == "2026-01-03T12:00:00Z"
        assert item.disk_mtime == parse_iso(mtime).strftime("%Y-%m-%dT%H:%M:%SZ")  # type: ignore[union-attr]
        assert (item.snapshot_size, item.disk_size) == (SNAPSHOT_SIZE, size)


async def test_diff_skips_missing_target(monkeypatch):
    """磁盘上不存在 = 新建而非覆盖，不计入清单。"""
    service, _, _ = _build(monkeypatch, disk={})

    assert await _collect(service) == []


async def test_diff_reports_actual_state_after_disk_change(monkeypatch):
    """TOCTOU：预览后磁盘变化 → 再次比对回报**当下实际值**（预览值不被缓存）。"""
    disk = {f"{ROOT}/nginx.conf": (_epoch("2026-02-04T08:30:00Z"), SNAPSHOT_SIZE)}
    service, fake_os, _ = _build(monkeypatch, disk=disk)

    first = await _collect(service)
    assert len(first) == 1, "预览时点：mtime 不同 → 1 项待覆盖"

    # 磁盘被改写为与快照一致 → 实际已无差异
    fake_os.table[f"{ROOT}/nginx.conf"] = (_epoch(SNAPSHOT_MTIME), SNAPSHOT_SIZE)
    assert await _collect(service) == [], "磁盘追平快照后应回报 0（实际值，非预览值）"

    # 反过来：磁盘再产生新差异 → 回报 1（不是回放上一次的 0）
    fake_os.table[f"{ROOT}/nginx.conf"] = (_epoch(SNAPSHOT_MTIME), SNAPSHOT_SIZE + 8)
    assert len(await _collect(service)) == 1


async def test_diff_propagates_truncated(monkeypatch):
    """restic 输出被截断 → 响应的 truncated 标记可透出。"""
    service, _, _ = _build(monkeypatch, disk={}, truncated=True)

    scan = service.diff(SNAPSHOT_ID, ROOT)
    _ = [item async for item in scan]

    assert scan.truncated is True


async def test_diff_lists_only_matched_subtree(monkeypatch):
    """ls 走目标子树（target == 快照根时 argv 末位即快照根），目录条目不参与比对。"""
    service, fake_os, fake_restic = _build(monkeypatch, disk={})

    await _collect(service)

    ls_argv = [argv for argv in fake_restic.argv_log if argv[2] == "ls"][0]
    assert ls_argv[-1] == ROOT  # 子树 = 目标（此处与快照根相同）
    assert fake_os.calls == [f"{ROOT}/nginx.conf"]  # 目录条目不 stat


async def test_diff_target_equal_root_uses_snapshot_absolute_path(monkeypatch):
    """零回归：target == 快照根时落点即快照内绝对路径（与修复前逐字节一致）。"""
    disk = {
        f"{ROOT}/nginx/nginx.conf": (_epoch("2026-02-04T08:30:00Z"), SNAPSHOT_SIZE),
        f"{ROOT}/hosts": (_epoch("2026-02-04T08:30:00Z"), 64),
    }
    service, _, _ = _build(monkeypatch, disk=disk, ls_lines=LS_LINES_NESTED)

    items = await _collect(service, target=ROOT)

    assert sorted(item.path for item in items) == [f"{ROOT}/hosts", f"{ROOT}/nginx/nginx.conf"]


async def test_diff_target_subdir_no_rebasing_and_narrows_scope(monkeypatch):
    """target ⊊ 快照根：落点仍是快照绝对路径（不嵌套），target 之外的文件不进清单。"""
    disk = {
        f"{ROOT}/nginx/nginx.conf": (_epoch("2026-02-04T08:30:00Z"), SNAPSHOT_SIZE),
        f"{ROOT}/hosts": (_epoch("2026-02-04T08:30:00Z"), 64),
    }
    service, fake_os, fake_restic = _build(monkeypatch, disk=disk, ls_lines=LS_LINES_NESTED)

    items = await _collect(service, target=f"{ROOT}/nginx")

    # ① 落点不再重定基点（旧实现会算成 /host/etc/nginx/nginx/nginx.conf）
    assert [item.path for item in items] == [f"{ROOT}/nginx/nginx.conf"]
    # ② target 之外的同级文件（执行层 --include=<target> 不会写它）不进清单
    assert f"{ROOT}/hosts" not in [item.path for item in items]
    # ③ 只 stat target 子树内、且非目录的条目
    assert fake_os.calls == [f"{ROOT}/nginx/nginx.conf"]
    # 取数范围同步收紧：ls 的子树是 target，不是整棵快照根
    ls_argv = [argv for argv in fake_restic.argv_log if argv[2] == "ls"][0]
    assert ls_argv[-1] == f"{ROOT}/nginx"


async def test_snapshot_roots_returns_container_paths(monkeypatch):
    """快照备份根按容器形态返回（供路由层双重锁收紧）。"""
    service, _, fake_restic = _build(monkeypatch, disk={})

    assert await service.snapshot_roots(SNAPSHOT_ID) == [ROOT]
    assert [argv for argv in fake_restic.argv_log if argv[2] == "snapshots"]


async def test_diff_outside_snapshot_paths_is_empty(monkeypatch):
    """目标不在快照 paths 范围内 → 空清单（越界由路由层 403 拦在前面，此处兜底）。"""
    service, _, _ = _build(monkeypatch, disk={})

    assert await _collect(service, target="/host/other") == []


def test_cursor_roundtrip_and_bounds():
    """游标编码：往返一致、损坏回落 0、越界钳到上限。"""
    assert decode_cursor(None, SNAPSHOT_FILES_MAX_OFFSET) == 0
    assert decode_cursor(encode_cursor(0), SNAPSHOT_FILES_MAX_OFFSET) == 0
    assert decode_cursor(encode_cursor(1234), SNAPSHOT_FILES_MAX_OFFSET) == 1234
    assert decode_cursor("not-a-cursor", SNAPSHOT_FILES_MAX_OFFSET) == 0  # 损坏游标 → 从头返回
    # 越界 → 钳到上限（防游标被篡改为超大值导致空转读取）
    assert decode_cursor(encode_cursor(999_999_999), SNAPSHOT_FILES_MAX_OFFSET) == SNAPSHOT_FILES_MAX_OFFSET
