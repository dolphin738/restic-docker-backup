"""策略服务（§4.4 / §5.1 E08–E11）：字段校验、默认值归一与列↔API 字段换算。

换算责任集中在本模块（避免散到路由）：
- API 字段 ``limit_up`` / ``limit_down`` ↔ DDL 列 ``limit_up_kibps`` / ``limit_down_kibps``；
- 备份源/排除规则的路径白名单校验；
- **内置排除集不入库**（D8）：用户提交的 excludes 中与内置集重合的条目会被剔除，
  执行时由 :class:`ExclusionRules` 统一叠加。
"""

from __future__ import annotations

from typing import Any

from app.db.repositories.policy_repo import Policy, PolicyRepo
from app.errors import ValidationError
from app.exclusions import ExclusionRules
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
        """把策略行转换为 E08 的 API 形态（列名 → API 字段名）。"""
        return {
            "id": policy.id,
            "name": policy.name,
            "is_default": policy.is_default,
            "enabled": policy.enabled,
            "tag": policy.tag,
            "sources": list(policy.sources),
            "excludes": list(policy.excludes),
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
        """校验备份源路径：逐个过白名单，去重保序。"""
        if not sources:
            raise ValidationError("策略至少需要一个备份源")
        from app.constants import SOURCE_ALLOWED_PREFIXES

        result: list[str] = []
        seen: set[str] = set()
        for item in sources:
            normalized = self._path_mapper.validate(str(item), allowed_prefixes=SOURCE_ALLOWED_PREFIXES)
            if normalized not in seen:
                seen.add(normalized)
                result.append(normalized)
        return result

    def _normalize_excludes(self, excludes: list[str]) -> list[str]:
        """剔除与内置排除集重合的条目（内置集不入库，D8），其余去重保序。"""
        builtin = set(self._exclusions.built_in())
        result: list[str] = []
        seen: set[str] = set()
        for item in excludes:
            candidate = str(item).strip()
            if not candidate or candidate in builtin or candidate in seen:
                continue
            seen.add(candidate)
            result.append(candidate)
        return result

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
