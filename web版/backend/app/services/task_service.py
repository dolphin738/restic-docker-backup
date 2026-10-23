"""任务服务（增量设计 §1.1/§2.1 E34–E36）：字段校验、归一与列↔API 字段换算。

字段校验单一真源的历史：共享字段校验原委托 :class:`PolicyService`（任务与策略
字段同构，不复制第二套）。2026-10-15 owner 裁决 Q1「选删」后 PolicyService 随
policies 遗留面（路由 E08–E11 / PolicyRepo / 测试）一并移除，该校验逻辑
**原样并入本模块**——口径逐字保留，行为零变化：

- sources/excludes 三分类归一（白名单收紧为当前模式根）；
- cron 5 字段校验（X5 收紧，与调度器建触发器的解析上限对齐：
  ``app.services.scheduler._cron_trigger`` 自行分字段校验，仅收 5 字段，星期字段按
  标准 crontab 语义换算后交给 APScheduler——不直接用 ``from_crontab``，后者在 3.x
  有星期偏移一天的历史缺陷，issue #286；Q4 静默防线：写入侧试构造，失败即 422
  并透出 APScheduler 原始报错文本）；
- keep_* 非负、compression 白名单、one_file_system 模式缺省、check_sample_ratio
  [0,1]、monthly_full_day 1~31；
- A2 时间线保留 keep_within*（restic duration 形态，复用 restic 层校验器）与
  keep_tags（去空白/去空/保序去重 + flag 注入写侧 fail-fast）。

任务域特有规则（原策略域不适用，故在本模块实现）：
- ``name`` 必填非空（无默认名）；
- ``tag`` 客户端不可见：:meth:`normalize` 显式剔除，服务端生成只读 ``task-<id>``；
- ``dry_run_pending`` 不参与归一：创建时由仓储缺省置 1（R-026-08），
  更新时的「源集合变更置 1」由路由层比对现状后决定（US-026-02 AC2）。
"""

from __future__ import annotations

import json
from datetime import UTC
from typing import Any

from app.constants import MIRROR_REPO_IDS_MAX, RETRY_COUNT_DEFAULT, RETRY_COUNT_MAX
from app.db.repositories.task_repo import Task, TaskRepo
from app.errors import PathOutOfPrefixError, ValidationError
from app.exclusions import ExclusionRules, normalize_user_excludes
from app.services.exclude_spec import flatten_exclude_spec, spec_from_wire
from app.services.path_mapper import PathMapper, source_allowed_prefixes
from app.services.restic import _assert_safe_value, _normalize_duration
from app.services.scheduler import _cron_trigger
from app.settings import Settings, get_settings

COMPRESSION_MODES: tuple[str, ...] = ("auto", "off", "max")
DEFAULT_GROUP_BY = "host,paths,tags"


class TaskService:
    """任务校验与归一。"""

    def __init__(self, repo: TaskRepo, settings: Settings | None = None) -> None:
        """构造任务服务。

        Args:
            repo: 任务仓储（tag 生成等单一实现点的宿主）。
            settings: 运行期配置（缺省取全局单例）。
        """
        self._repo = repo
        self._settings = settings or get_settings()
        self._path_mapper = PathMapper(self._settings)
        self._exclusions = ExclusionRules(self._settings)

    # ---------------------------------------------------------------- 读侧换算

    def to_api(self, task: Task) -> dict[str, Any]:
        """把任务行转换为 E34' 的 API 形态（列名 → API 字段名 + display 派生）。

        形态原则：容器形态只存在于存储与 restic argv，出 API 一律带 display
        派生——``display_sources`` / ``display_excludes`` 由 :meth:`_display_of`
        派生（单一换算点，不复制第二套）。
        """
        return {
            "id": task.id,
            "repo_id": task.repo_id,
            "name": task.name,
            "enabled": task.enabled,
            "tag": task.tag,
            "sources": list(task.sources),
            "display_sources": [self._display_of(item) for item in task.sources],
            "excludes": list(task.excludes),
            "display_excludes": [self._display_of(item) for item in task.excludes],
            # 批次② 结构化过滤条件（原样回传；前端据此预填快捷卡/规则列表）
            "exclude_spec": task.exclude_spec,
            "keep_last": task.keep_last,
            "keep_hourly": task.keep_hourly,
            "keep_daily": task.keep_daily,
            "keep_weekly": task.keep_weekly,
            "keep_monthly": task.keep_monthly,
            "keep_yearly": task.keep_yearly,
            "keep_within": task.keep_within,
            "keep_within_hourly": task.keep_within_hourly,
            "keep_within_daily": task.keep_within_daily,
            "keep_within_weekly": task.keep_within_weekly,
            "keep_within_monthly": task.keep_within_monthly,
            "keep_within_yearly": task.keep_within_yearly,
            "keep_tags": list(task.keep_tags),
            "group_by": task.group_by,
            "cron": task.cron,
            "one_file_system": task.one_file_system,
            "compression": task.compression,
            "check_sample_ratio": task.check_sample_ratio,
            "monthly_full_day": task.monthly_full_day,
            "prune_interval_days": task.prune_interval_days,
            "limit_up": task.limit_up_kibps,
            "limit_down": task.limit_down_kibps,
            "dry_run_pending": task.dry_run_pending,
            "last_run_at": task.last_run_at,
            # C3 任务级失败重试（0 = 不重试）
            "retry_count": task.retry_count,
            # C4 多目的地：备份成功后镜像到的仓库 id 列表（空 = 单目的地）
            "mirror_repo_ids": list(task.mirror_repo_ids),
            # greenfield：无 legacy 策略可继承，DDL 无此列，恒 None（wire 兼容占位）
            "legacy_policy_id": None,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
        }

    # ---------------------------------------------------------------- 写侧归一

    def normalize(self, payload: dict[str, Any]) -> dict[str, Any]:
        """校验并把 API 字段归一为列名口径。

        Args:
            payload: 请求体（API 字段名）。

        Returns:
            列名字典，可直接交给 :meth:`TaskRepo.create` / :meth:`TaskRepo.update`；
            **不含** tag / dry_run_pending / last_run_at（服务端控制列）。

        Raises:
            ValidationError: name 空或任一字段非法（422）。
        """
        raw_name = payload.get("name")
        if raw_name is None or not str(raw_name).strip():
            raise ValidationError("任务名称不能为空", data={"field": "name"})

        values: dict[str, Any] = {}
        values["name"] = str(raw_name).strip()
        values["enabled"] = 1 if payload.get("enabled", True) else 0
        values["sources"] = self._normalize_sources(payload.get("sources") or [])
        raw_spec = payload.get("exclude_spec")
        if raw_spec:
            # 批次②：spec 为唯一事实源——此处展平一次以**前置校验**（glob 语法 / 大小阈值 /
            # negate∧icase → 422），并保持 excludes 列置空，避免与 spec 各存一份漂移；
            # 真实拼装在 JobRunner 与 dry-run 端点经 flatten_exclude_spec 展开。
            flatten_exclude_spec(spec_from_wire(raw_spec), self._path_mapper)
            values["exclude_spec"] = raw_spec
            values["excludes"] = []
        else:
            values["excludes"] = self._normalize_excludes(payload.get("excludes") or [])
            values["exclude_spec"] = None
        for key in (
            "keep_last",
            "keep_hourly",
            "keep_daily",
            "keep_weekly",
            "keep_monthly",
            "keep_yearly",
        ):
            values[key] = _non_negative_int(payload.get(key, 0), key)
        # A2 时间线保留：restic duration 形态校验复用 restic 层单一实现
        # （与 _cron_trigger 同款跨模块私有导入先例——校验逻辑不复制第二套）；
        # 归一后空串 = 未启用（与 keep_* 的 0 口径一致）。
        for key in (
            "keep_within",
            "keep_within_hourly",
            "keep_within_daily",
            "keep_within_weekly",
            "keep_within_monthly",
            "keep_within_yearly",
        ):
            values[key] = _normalize_duration(payload.get(key), key) or ""
        values["keep_tags"] = _normalize_keep_tags(payload.get("keep_tags"))
        values["group_by"] = _non_empty_str(payload.get("group_by"), "group_by", default=DEFAULT_GROUP_BY)
        values["cron"] = self._normalize_cron(payload.get("cron"))
        values["one_file_system"] = self._normalize_one_file_system(payload.get("one_file_system"))
        values["compression"] = self._normalize_compression(payload.get("compression"))
        values["check_sample_ratio"] = _ratio(payload.get("check_sample_ratio"))
        values["monthly_full_day"] = _day_of_month(payload.get("monthly_full_day"))
        values["prune_interval_days"] = _non_negative_int(payload.get("prune_interval_days", 7), "prune_interval_days")
        values["limit_up_kibps"] = _non_negative_int(payload.get("limit_up", 0), "limit_up")
        values["limit_down_kibps"] = _non_negative_int(payload.get("limit_down", 0), "limit_down")
        # C3 重试次数：钳 [0, RETRY_COUNT_MAX]（写侧 fail-fast，不留给运行期静默截断）
        values["retry_count"] = _retry_count(payload.get("retry_count", RETRY_COUNT_DEFAULT))
        # C4 多目的地：镜像仓 id 只做形态归一（去重/上限），存在性与自指由路由层
        # 校验（需要 repos 表，TaskService 不持有跨仓储依赖）。
        values["mirror_repo_ids"] = _normalize_mirror_ids(payload.get("mirror_repo_ids"))
        # tag 由服务端生成（task-<id>），客户端提交一律忽略（共享知识 §9.1）；
        # 注意 tag 不出现在 values 中——TaskRepo.create 负责插入后回写。
        return values

    # ---------------------------------------------------------------- 服务端控制列

    @staticmethod
    def generate_tag(task_id: int) -> str:
        """任务 tag 生成规则（单一实现点委托仓储）：``task-<id>``，只读。"""
        return TaskRepo.generate_tag(task_id)

    # ---------------------------------------------------------------- 内部实现

    def default_one_file_system(self) -> bool:
        """缺省开启 ``--one-file-system``（方案 §3.2 四层防线第一层；A0 收敛后固定 True）。"""
        return True

    def _display_of(self, value: str) -> str:
        """容器形态 → 展示形态（display 派生专用）。

        纯 glob 模式（不以 ``/`` 开头，如 ``*.tmp``）**原样返回**——它们形态无关，
        过 :meth:`PathMapper.to_display` 会被补上前导 ``/`` 造成回显与用户输入不一致。
        """
        if not value.startswith("/"):
            return value
        return self._path_mapper.to_display(value)

    def _normalize_sources(self, sources: list[str]) -> list[str]:
        """校验备份源路径：接受展示形态（新契约）或容器形态（兼容），归一为容器形态。

        归一口径（主理人裁决，P1 最后一处契约对称化）：前端硬约束"请求体只发展示形态"
        ——``/etc/nginx`` 经 ``to_snapshot`` 还原为容器形态；已带容器前缀
        （A0 收敛后即 ``/sources``）的入参视为已是容器形态，直接按模式根校验
        （不二次叠加，避免 ``/sources/etc`` → ``/sources/sources/etc`` 的重解释）。

        白名单**收敛为模式根**（A0 后恒为 ``/sources``）：越出该根的入参实际指向
        容器内不可备份面，语义错误，应尽早 422 显式失败。

        Raises:
            ValidationError: 无备份源、路径非法或越出备份源根（422）。
        """
        if not sources:
            raise ValidationError("任务至少需要一个备份源")
        result: list[str] = []
        seen: set[str] = set()
        browse_root = self._settings.browse_root
        # "已是容器形态"判定前缀 = 当前源根（docker 取 /sources、host 取 /，均由配置派生）——
        # host 模式下浏览根本身是根路径，绝对路径即容器形态，to_snapshot 亦幂等。
        container_prefixes = (browse_root,)
        for raw in sources:
            item = str(raw).strip()
            try:
                if item.startswith(container_prefixes):
                    # 已是容器形态：不二次叠加源根，直接按源根校验（跨形态 → 422）
                    container = self._path_mapper.validate(item, allowed_prefixes=(browse_root,))
                else:
                    # 展示形态 → 还原为容器形态后校验（normpath 折叠 ../ 防穿越）
                    container = self._path_mapper.validate(
                        self._path_mapper.to_snapshot(item), allowed_prefixes=(browse_root,)
                    )
            except PathOutOfPrefixError as exc:
                raise ValidationError(
                    f"备份源 {item!r} 不在当前备份源根范围内", data={"source": item}
                ) from exc
            if container not in seen:
                seen.add(container)
                result.append(container)
        return result

    def _normalize_excludes(self, excludes: list[str]) -> list[str]:
        """用户追加排除归一为容器形态（三分类规则见 :func:`normalize_user_excludes`），
        再剔除与内置集重合的条目（内置集不入库，D8）。

        锚定路径白名单**收紧为当前备份源根**（主理人裁决）：排除
        ``/sources/...`` 之外的已挂载远端路径（如残留 ``/host/...``）无对应挂载点，
        语义错误，应尽早 422 显式失败。
        """
        normalized = normalize_user_excludes(
            list(excludes), self._path_mapper, source_prefixes=source_allowed_prefixes(self._settings)
        )
        builtin = set(self._exclusions.built_in())
        return [item for item in normalized if item not in builtin]

    def _normalize_one_file_system(self, value: Any) -> int:
        """``None`` 表示"按模式取缺省"（sources 模式缺省 true）。"""
        if value is None:
            return 1 if self.default_one_file_system() else 0
        return 1 if bool(value) else 0

    def _normalize_compression(self, value: Any) -> str:
        """压缩模式白名单校验。"""
        candidate = str(value or "auto")
        if candidate not in COMPRESSION_MODES:
            raise ValidationError(
                f"压缩模式非法：{candidate}", data={"allowed": list(COMPRESSION_MODES)}
            )
        return candidate

    @staticmethod
    def _normalize_cron(value: Any) -> str | None:
        """cron 校验：**只收 5 字段（分 时 日 月 周）**（X5 owner 裁决收紧）。

        调度器建触发器的解析上限即 5 字段（``app.services.scheduler._cron_trigger``
        自行分字段校验，星期字段按标准 crontab 语义换算后交给 APScheduler——
        不直接用 ``from_crontab``，后者在 3.x 有星期偏移一天的历史缺陷，issue #286）。
        放行 6 字段（秒级）会导致保存成功但任务永不自动触发，故在写入侧直接 422 拒绝。

        **语义可解析性（Q4 静默防线）**：只校验字段数会放过 ``sun-sat``（跨周围绕）、
        ``*/8``（越界步长）、``0 3 * * 8``（越界星期）这类表达式——它们能顺利保存，
        随后调度器解析失败返回 ``None`` + 一条 WARNING，任务退化「仅手动」，前端无
        任何提示，管理员不知道自己配的调度根本没生效（与缺陷 A 同类的静默问题）。
        故写入侧用 ``_cron_trigger`` 试构造一次，失败即 422，并把 APScheduler 的
        **原始报错文本**透出（如 "the minimum value in a range must not be higher
        than the maximum"），让管理员知道具体哪里错了。

        校验只在写入路径生效，已有数据不受影响。
        """
        if value is None or str(value).strip() == "":
            return None
        candidate = str(value).strip()
        fields = candidate.split()
        if len(fields) != 5:
            raise ValidationError("cron 须为 5 字段（分 时 日 月 周）", data={"cron": candidate})
        try:
            _cron_trigger(candidate, timezone=UTC)  # 语义试构造（时区与字段校验无关，取 UTC）
        except ValueError as exc:
            raise ValidationError(
                f"cron 无法解析：{exc}", data={"cron": candidate, "reason": str(exc)}
            ) from exc
        return candidate


def _non_empty_str(value: Any, field_name: str, *, default: str) -> str:
    """取非空字符串，缺省回落默认值。"""
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip()


def _non_negative_int(value: Any, field_name: str) -> int:
    """校验并转换为非负整数（非整数形态 → 422）。

    陷阱：``int(1.5)`` 不报错、静默截断为 1。对 keep_* 保留份数 / retry_count
    重试次数这类「份数」语义是错的——用户填 1.5 应显式 422，不能悄悄变成 1
    （Q4 静默防线：宁可早失败，不要晚发现）。与 pydantic 取整契约同口径：
    整值浮点（``1.0``）与整数字符串（``"3"``）照常接受。
    """
    if isinstance(value, float) and not value.is_integer():
        raise ValidationError(f"{field_name} 须为整数", data={"field": field_name})
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{field_name} 须为整数", data={"field": field_name}) from exc
    if number < 0:
        raise ValidationError(f"{field_name} 不能为负数", data={"field": field_name})
    return number


def _normalize_keep_tags(value: Any) -> list[str]:
    """A2 ``keep_tags`` 写侧归一：去首尾空白、丢空串、保序去重。

    每元素过 :func:`_assert_safe_value`（禁 ``-`` 开头/NUL/换行——防 flag 注入，
    与拼装层同款校验）——**写侧 fail-fast**：表单保存即 422，不留到任务提交时
    才报（Q4 静默防线：坏配置尽早显式失败）。

    Args:
        value: 请求体原值（None/列表/元组；其余形态 422）。

    Returns:
        归一后的标签列表（可能为空 = 未启用）。

    Raises:
        ValidationError: 形态非列表或任一元素非法。
    """
    if value is None:
        return []
    if not isinstance(value, list | tuple):
        raise ValidationError("keep_tags 须为字符串数组", data={"field": "keep_tags"})
    result: list[str] = []
    seen: set[str] = set()
    for raw in value:
        item = str(raw).strip()
        if not item or item in seen:
            continue
        _assert_safe_value(item, "keep_tags")
        seen.add(item)
        result.append(item)
    return result


def _retry_count(value: Any) -> int:
    """C3 重试次数归一：非负整数 + 钳上限（>上限按上限，非整数/负数 → 422）。

    Duplicati 的 number-of-retries 无上限，本项目必须设上限：无上限的重试会让
    任务长期占着仓库互斥锁（E1），后续调度周期全部撞冲突跳过。
    """
    number = _non_negative_int(value, "retry_count")
    return min(number, RETRY_COUNT_MAX)


def _normalize_mirror_ids(value: Any) -> list[str]:
    """C4 镜像仓 id 归一：去空白、丢空、保序去重、钳 :data:`~app.constants.MIRROR_REPO_IDS_MAX`。

    钳上限的理由（与 C3 retry_count 同口径）：每个镜像都要独立走一遍 copy 全链路，
    无上限会让单次备份被镜像阶段拖到超过调度周期。写侧钳好，运行期
    (:func:`job_runner._as_mirror_ids`) 只再兜一次防脏数据。

    Raises:
        ValidationError: 形态非列表/JSON 数组（422）。
    """
    if value is None:
        return []
    if isinstance(value, str):
        try:
            loaded = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise ValidationError("mirror_repo_ids 须为字符串数组", data={"field": "mirror_repo_ids"}) from exc
        if not isinstance(loaded, list):
            raise ValidationError("mirror_repo_ids 须为字符串数组", data={"field": "mirror_repo_ids"})
        value = loaded
    if not isinstance(value, list | tuple):
        raise ValidationError("mirror_repo_ids 须为字符串数组", data={"field": "mirror_repo_ids"})
    result: list[str] = []
    for raw in value:
        item = str(raw).strip()
        if item and item not in result:
            result.append(item)
    if len(result) > MIRROR_REPO_IDS_MAX:
        # 与 C3 retry_count 不同：数量超限**必须报错**而非钳断——静默截掉第 4 个
        # 镜像仓，用户会以为异地副本在跑，实际那个仓从没收到过数据。
        raise ValidationError(
            f"镜像仓数量超过上限（最多 {MIRROR_REPO_IDS_MAX} 个）",
            data={"field": "mirror_repo_ids", "count": len(result)},
        )
    return result


def _ratio(value: Any) -> float:
    """校验抽样比例为 [0, 1] 区间浮点。"""
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError("check_sample_ratio 须为数值", data={"field": "check_sample_ratio"}) from exc
    if not 0.0 <= number <= 1.0:
        raise ValidationError("check_sample_ratio 须在 0~1 之间", data={"field": "check_sample_ratio"})
    return number


def _day_of_month(value: Any) -> int:
    """校验月度全量校验日为 1~31。"""
    number = _non_negative_int(value, "monthly_full_day")
    if not 1 <= number <= 31:
        raise ValidationError("monthly_full_day 须在 1~31 之间", data={"field": "monthly_full_day"})
    return number
