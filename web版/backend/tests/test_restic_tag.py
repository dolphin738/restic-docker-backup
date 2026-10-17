"""restic ``tag`` 动词白名单与 argv 拼装（A3）。

纯单元：``ResticCmd.build`` 走真实白名单逻辑，不启动子进程。断言点：

- ``VERBS`` 含 ``tag``；
- ``tag`` 动词把 ``tag_add``/``tag_remove`` 拼成 ``--add``/``--remove``（每元素一条 flag）；
- 快照 id 经 ``extra_positionals`` 落到 argv 末尾（位置参数）；
- ``tag`` 动词且 ``tag_add``/``tag_remove`` 皆空 → 422（restic 端 "nothing to do!" 前移）；
- 标签值以 ``-`` 开头 → 防 flag 注入拒（与 --tag 同款 _assert_safe_value）。
"""

from __future__ import annotations

import pytest

from app.errors import ValidationError
from app.services.restic import VERBS, ResticCmd, WhitelistArgs

SNAPSHOT_A = "a" * 64


def _build(**kwargs) -> list[str]:
    """便捷：构造 tag 动词 argv。"""
    args = WhitelistArgs(**kwargs)
    return ResticCmd().build("tag", args=args)


def test_verbs_contains_tag() -> None:
    assert "tag" in VERBS


def test_tag_add_and_remove_flags_assembled() -> None:
    argv = _build(tag_add=("prod", "env"), tag_remove=("legacy",), extra_positionals=(SNAPSHOT_A,))
    assert "--add" in argv
    # 两个 --add：prod / env
    add_indices = [i for i, v in enumerate(argv) if v == "--add"]
    assert [argv[i + 1] for i in add_indices] == ["prod", "env"]
    assert "--remove" in argv
    assert argv[argv.index("--remove") + 1] == "legacy"
    # 快照 id 落在 verb 之后、argv 末尾（位置参数）
    assert SNAPSHOT_A in argv
    assert argv[-1] == SNAPSHOT_A


def test_tag_filter_and_host_emit_selection_flags() -> None:
    argv = _build(tag_add=("prod",), tags=("task-1",), host="nas")
    assert "--tag" in argv and argv[argv.index("--tag") + 1] == "task-1"
    assert "--host" in argv and argv[argv.index("--host") + 1] == "nas"


def test_tag_without_add_remove_rejected() -> None:
    with pytest.raises(ValidationError):
        _build(extra_positionals=(SNAPSHOT_A,))


def test_tag_value_leading_dash_rejected() -> None:
    with pytest.raises(ValidationError):
        _build(tag_add=("-x",))


def test_tag_add_only_no_positionals_ok() -> None:
    """只给 add、不给快照 id（restic 按过滤条件改全部）——拼装层不拦（高危由路由层拦）。"""
    argv = _build(tag_add=("prod",))
    assert "--add" in argv
    assert "prod" in argv
