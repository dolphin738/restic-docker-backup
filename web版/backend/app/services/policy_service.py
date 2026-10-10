"""策略服务（§4.4 / §5.1 E08–E11）：字段校验、默认值归一与列↔API 字段换算。

换算责任集中在本模块（避免散到路由）：
- API 字段 ``limit_up`` / ``limit_down`` ↔ DDL 列 ``limit_up_kibps`` / ``limit_down_kibps``；
- 备份源/排除规则的路径白名单校验；
- **内置排除集不入库**（D8）：用户提交的 excludes 中与内置集重合的条目会被剔除，
  执行时由 :class:`ExclusionRules` 统一叠加。

形态口径（主理人裁决定稿）：**policies.sources / policies.excludes 入库与下游
（argv）均为容器前缀形态**；展示形态只存在于 API 边界与 ``display_*`` 字段。
用户提交的 sources（展示路径为主，兼容容器路径）与锚定路径类 exclude（含 glob
通配）分别在 :meth:`PolicyService._normalize_sources` 与
:func:`exclusions.normalize_user_excludes` 归一，白名单均收紧为当前模式根。
"""

from __future__ import annotations

from typing import Any

from app.db.repositories.policy_repo import Policy, PolicyRepo
from app.errors import PathOutOfPrefixError, ValidationError
from app.exclusions import ExclusionRules, normalize_user_excludes
from app.services.path_mapper import PathMapper
from app.settings import Settings, get_settings

COMPRESSION_MODES: tuple[str, ...] = ("auto", "off", "max")
DEFAULT_NAME = "default"
DEFAULT_TAG = "auto"
DEFAULT_GROUP_BY = "host,paths,tags"


class PolicyService:
    """策略校验与归一。"""

    def __init__(
        self,
        repo: PolicyRepo,
        settings: Settings | None = None,
        path_mapper: PathMapper | None = None,
        exclusions: ExclusionRules | None = None,
    ) -> None:
        """构造策略服务。

        Args:
            repo: 策略仓储。
            settings: 运行期配置。
            path_mapper: 路径映射器（校验备份源）。
            exclusions: 内置排除集（用于剔除重合条目）。
        """
        self._repo = repo
        self._settings = settings or get_settings()
        self._path_mapper = path_mapper or PathMapper(self._settings)
        self._exclusions = exclusions or ExclusionRules(self._settings)

    # ---------------------------------------------------------------- 读侧换算

    def to_api(self, policy: Policy) -> dict[str, Any]:
        """把策略行转换为 E08 的 API 形态（列名 → API 字段名）。

        形态原则（主理人裁决定稿）：容器形态只存在于存储与 restic argv，**出 API 给 UI
        的一律带 display 派生**——``display_sources`` / ``display_excludes`` 由
        :meth:`PathMapper.to_display` 从容器字段派生（对齐 E32 的 display_* 做法，
        容器形态字段保留 wire 兼容）。
        """
        return {
            "id": policy.id,
            "name": policy.name,
            "is_default": policy.is_default,
            "enabled": policy.enabled,
            "tag": policy.tag,
            "sources": list(policy.sources),
            "display_sources": [_display_of(item, self._path_mapper) for item in policy.sources],
            "excludes": list(policy.excludes),
            "display_excludes": [_display_of(item, self._path_mapper) for item in policy.excludes],
            "keep_last": policy.keep_last,
            "keep_hourly": policy.keep_hourly,
            "keep_daily": policy.keep_daily,
            "keep_weekly": policy.keep_weekly,
            "keep_monthly": policy.keep_monthly,
            "keep_yearly": policy.keep_yearly,
            "group_by": policy.group_by,
            "cron": policy.cron,
            "one_file_system": policy.one_file_system,
            "compression": policy.compression,
            "check_sample_ratio": policy.check_sample_ratio,
            "monthly_full_day": policy.monthly_full_day,
            "prune_interval_days": policy.prune_interval_days,
            "limit_up": policy.limit_up_kibps,
            "limit_down": policy.limit_down_kibps,
            "last_run_at": policy.last_run_at,
            "created_at": policy.created_at,
            "updated_at": policy.updated_at,
        }

    # ---------------------------------------------------------------- 写侧归一

    def normalize(self, payload: dict[str, Any], *, for_update: bool = False) -> dict[str, Any]:
        """校验并把 API 字段归一为列名口径。

        Args:
            payload: 请求体（API 字段名）。
            for_update: 是否为部分更新（PUT 全量替换语义下仍传全量，此处仅影响缺省补齐）。

        Returns:
            列名字典，可直接交给 :meth:`PolicyRepo.create` / :meth:`PolicyRepo.update`。

        Raises:
            ValidationError: 任一字段非法（422）。
        """
        del for_update  # PUT/POST 均为全量字段语义，缺省值补齐逻辑共用
        values: dict[str, Any] = {}

        values["name"] = _non_empty_str(payload.get("name"), "name", default=DEFAULT_NAME)
        values["enabled"] = 1 if payload.get("enabled", True) else 0
        values["tag"] = payload.get("tag") or DEFAULT_TAG
        values["sources"] = self._normalize_sources(payload.get("sources") or [])
        values["excludes"] = self._normalize_excludes(payload.get("excludes") or [])
        for key in (
            "keep_last",
            "keep_hourly",
            "keep_daily",
            "keep_weekly",
            "keep_monthly",
            "keep_yearly",
        ):
            values[key] = _non_negative_int(payload.get(key, 0), key)
        values["group_by"] = _non_empty_str(payload.get("group_by"), "group_by", default=DEFAULT_GROUP_BY)
        values["cron"] = self._normalize_cron(payload.get("cron"))
        values["one_file_system"] = self._normalize_one_file_system(payload.get("one_file_system"))
        values["compression"] = self._normalize_compression(payload.get("compression"))
        values["check_sample_ratio"] = _ratio(payload.get("check_sample_ratio"))
        values["monthly_full_day"] = _day_of_month(payload.get("monthly_full_day"))
        values["prune_interval_days"] = _non_negative_int(payload.get("prune_interval_days", 7), "prune_interval_days")
        values["limit_up_kibps"] = _non_negative_int(payload.get("limit_up", 0), "limit_up")
        values["limit_down_kibps"] = _non_negative_int(payload.get("limit_down", 0), "limit_down")
        return values

    def default_one_file_system(self) -> bool:
        """sources 模式缺省开启 ``--one-file-system``（方案 §3.2 四层防线第一层）。"""
        return self._settings.restic_web_mode == "sources"

    # ---------------------------------------------------------------- 内部实现

    def _normalize_sources(self, sources: list[str]) -> list[str]:
        """校验备份源路径：接受展示形态（新契约）或容器形态（兼容），归一为容器形态。

        归一口径与 excludes 对齐（主理人裁决，P1 最后一处契约对称化）：前端硬约束
        "请求体只发展示形态"——``/etc/nginx`` 经 ``to_snapshot`` 还原为容器形态；
        已带容器前缀（``/host`` / ``/sources``）的入参视为已是容器形态，直接按
        模式根校验（不二次叠加，避免 ``/host/etc`` → ``/host/host/etc`` 与跨模式
        路径被无害重解释）。

        白名单**收紧为当前模式根**：host 模式下提交 ``/sources/...`` 当备份源实际
        指向容器内数据根，语义错误，应尽早 422 显式失败（数据根不能当备份源）。

        Raises:
            ValidationError: 无备份源、路径非法或越出当前模式根（422）。
        """
        if not sources:
            raise ValidationError("策略至少需要一个备份源")
        result: list[str] = []
        seen: set[str] = set()
        container_prefixes = (ExclusionRules.HOST_PREFIX, ExclusionRules.SOURCES_PREFIX)
        mode_root = self._settings.mode_root
        for raw in sources:
            item = str(raw).strip()
            try:
                if item.startswith(container_prefixes):
                    # 已是容器形态：不二次叠加模式根，直接按模式根校验（跨模式 → 422）
                    container = self._path_mapper.validate(item, allowed_prefixes=(mode_root,))
                else:
                    # 展示形态 → 还原为容器形态后校验（normpath 折叠 ../ 防穿越）
                    container = self._path_mapper.validate(
                        self._path_mapper.to_snapshot(item), allowed_prefixes=(mode_root,)
                    )
            except PathOutOfPrefixError as exc:
                raise ValidationError(
                    f"备份源 {item!r} 不在当前模式根范围内", data={"source": item}
                ) from exc
            if container not in seen:
                seen.add(container)
                result.append(container)
        return result

    def _normalize_excludes(self, excludes: list[str]) -> list[str]:
        """用户追加排除归一为容器形态（三分类规则见 :func:`normalize_user_excludes`），
        再剔除与内置集重合的条目（内置集不入库，D8）。

        锚定路径白名单**收紧为当前模式根**（主理人裁决）：host 模式下排除
        ``/sources/...`` 实际指向容器内数据根，语义错误，应尽早 422 显式失败。
        """
        normalized = normalize_user_excludes(
            list(excludes), self._path_mapper, source_prefixes=(self._settings.mode_root,)
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
        """cron 轻量校验：仅检查字段数（5 或 6），P2 调度器接管后做语义校验。"""
        if value is None or str(value).strip() == "":
            return None
        candidate = str(value).strip()
        fields = candidate.split()
        if len(fields) not in (5, 6):
            raise ValidationError("cron 表达式须为 5 或 6 个字段", data={"cron": candidate})
        return candidate


def _display_of(value: str, mapper: PathMapper) -> str:
    """容器形态 → 展示形态（display 派生专用）。

    纯 glob 模式（不以 ``/`` 开头，如 ``*.tmp``）**原样返回**——它们形态无关，
    过 :meth:`PathMapper.to_display` 会被补上前导 ``/`` 造成回显与用户输入不一致。
    """
    if not value.startswith("/"):
        return value
    return mapper.to_display(value)


def _non_empty_str(value: Any, field_name: str, *, default: str) -> str:
    """取非空字符串，缺省回落默认值。"""
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip()


def _non_negative_int(value: Any, field_name: str) -> int:
    """校验并转换为非负整数。"""
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{field_name} 须为整数", data={"field": field_name}) from exc
    if number < 0:
        raise ValidationError(f"{field_name} 不能为负数", data={"field": field_name})
    return number


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
