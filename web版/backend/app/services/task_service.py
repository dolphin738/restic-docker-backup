"""任务服务（增量设计 §1.1/§2.1 E34–E36）：字段校验、归一与列↔API 字段换算。

共享字段校验**委托既有 :class:`PolicyService`**（§1.1 选型表）：任务与策略字段同构，
sources/excludes 三分类归一、cron 5 字段校验（X5 收紧，与调度器建触发器的解析上限对齐：
``app.services.scheduler._cron_trigger`` 自行分字段校验，仅收 5 字段，星期字段按标准
crontab 语义换算后交给 APScheduler——不直接用 ``from_crontab``，后者在 3.x 有星期偏移
一天的历史缺陷，issue #286）、
keep_* 非负、compression 白名单、one_file_system 模式缺省——全部复用策略域单一真源，
**不复制第二套校验**。

任务域特有规则（策略域不适用，故在本模块实现）：
- ``name`` 必填非空（策略域有 ``DEFAULT_NAME`` 缺省，任务无默认名）；
- ``tag`` 客户端不可见：:meth:`normalize` 显式剔除，服务端生成只读 ``task-<id>``；
- ``dry_run_pending`` 不参与归一：创建时由仓储缺省置 1（R-026-08），
  更新时的「源集合变更置 1」由路由层比对现状后决定（US-026-02 AC2）。
"""

from __future__ import annotations

from typing import Any

from app.db.repositories.task_repo import Task, TaskRepo
from app.errors import ValidationError
from app.services.policy_service import PolicyService


class TaskService:
    """任务校验与归一。"""

    def __init__(self, repo: TaskRepo, policy_service: PolicyService) -> None:
        """构造任务服务。

        Args:
            repo: 任务仓储（tag 生成等单一实现点的宿主）。
            policy_service: 策略服务（共享字段校验委托方）。
        """
        self._repo = repo
        self._policy_service = policy_service

    # ---------------------------------------------------------------- 读侧换算

    def to_api(self, task: Task) -> dict[str, Any]:
        """把任务行转换为 E34' 的 API 形态（列名 → API 字段名 + display 派生）。

        形态原则同策略域：容器形态只存在于存储与 restic argv，出 API 一律带
        display 派生——``display_sources`` / ``display_excludes`` 由
        :meth:`PolicyService.display_of` 派生（共享换算点，不复制第二套）。
        """
        return {
            "id": task.id,
            "repo_id": task.repo_id,
            "name": task.name,
            "enabled": task.enabled,
            "tag": task.tag,
            "sources": list(task.sources),
            "display_sources": [self._policy_service.display_of(item) for item in task.sources],
            "excludes": list(task.excludes),
            "display_excludes": [self._policy_service.display_of(item) for item in task.excludes],
            "keep_last": task.keep_last,
            "keep_hourly": task.keep_hourly,
            "keep_daily": task.keep_daily,
            "keep_weekly": task.keep_weekly,
            "keep_monthly": task.keep_monthly,
            "keep_yearly": task.keep_yearly,
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
            # greenfield：无 legacy 策略可继承，DDL 无此列，恒 None（wire 兼容占位）
            "legacy_policy_id": None,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
        }

    # ---------------------------------------------------------------- 写侧归一

    def normalize(self, payload: dict[str, Any]) -> dict[str, Any]:
        """校验并把 API 字段归一为列名口径（共享字段委托 :meth:`PolicyService.normalize`）。

        Args:
            payload: 请求体（API 字段名）。

        Returns:
            列名字典，可直接交给 :meth:`TaskRepo.create` / :meth:`TaskRepo.update`；
            **不含** tag / dry_run_pending / last_run_at（服务端控制列）。

        Raises:
            ValidationError: name 空或任一共享字段非法（422，与策略页同规则）。
        """
        raw_name = payload.get("name")
        if raw_name is None or not str(raw_name).strip():
            raise ValidationError("任务名称不能为空", data={"field": "name"})

        values = self._policy_service.normalize(payload)
        # name 用任务域规则覆盖（策略域的 DEFAULT_NAME 缺省不适用）
        values["name"] = str(raw_name).strip()
        # tag 由服务端生成（task-<id>），客户端提交一律忽略（共享知识 §9.1）
        values.pop("tag", None)
        return values

    # ---------------------------------------------------------------- 服务端控制列

    @staticmethod
    def generate_tag(task_id: int) -> str:
        """任务 tag 生成规则（单一实现点委托仓储）：``task-<id>``，只读。"""
        return TaskRepo.generate_tag(task_id)
