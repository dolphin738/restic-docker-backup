"""应用配置：由 pydantic-settings 从环境变量读取（方案 §4.1 部署项 vs 界面项边界）。

字段名即环境变量名的大写形式，例如 ``restic_web_mode`` ← ``RESTIC_WEB_MODE``。
compose 中只保留"容器怎么跑"的部署事实；业务配置一律走 DB（config 表）与 Web 界面。
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

try:  # pragma: no cover - Windows 等非 POSIX 平台无 pwd 模块
    import pwd
except ImportError:  # pragma: no cover
    pwd = None  # type: ignore[assignment]


class Settings(BaseSettings):
    """Web 版运行期配置。"""

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore", env_file=None)

    # ---- 部署项（compose 决定，运行期只读）----
    restic_web_mode: Literal["host", "sources"] = "host"
    host_mount_mode: Literal["ro", "rw"] = "rw"
    tz: str = "UTC"

    # ---- 路径（§1.2 部署拓扑）----
    data_dir: str = "/data"
    cache_dir: str = "/cache/restic"
    host_root: str = "/host"
    sources_root: str = "/sources"
    restore_root: str = "/restore"
    repo_root: str = "/repo"
    passfile: str = "/run/secrets/restic-pass"

    # ---- 内置排除集派生（host 模式防套娃条目的部署目录解析，方案 §3.1）----
    # 可选：显式指定 compose 部署目录（宿主机绝对路径）；留空则尝试从 /proc/self/mountinfo 反推。
    restic_web_deploy_dir: str | None = None

    # ---- HTTP / TLS ----
    host: str = "0.0.0.0"
    port: int = 8000
    force_https: bool = False
    # 反代后置时置 true：登录限流与会话 cookie 的 Secure 判定将信任 X-Forwarded-* 头
    trust_proxy: bool = False

    # ---- 会话（§8）----
    session_cookie_name: str = "rw_session"
    session_ttl_hours: int = 24

    # ---- 登录锁定与限流（§8）----
    max_failed_attempts: int = 5
    lock_base_seconds: int = 300
    lock_max_seconds: int = 3600
    login_rate_limit: int = 5
    login_rate_window_seconds: int = 60

    # ---- 杂项 ----
    restic_binary: str = "restic"
    audit_detail_limit: int = 2000

    # ---- 派生属性 ----
    @property
    def db_path(self) -> Path:
        """SQLite 数据库文件路径（/data/restic.db，§1.1 基础设施层）。"""
        return Path(self.data_dir) / "restic.db"

    @property
    def jobs_dir(self) -> Path:
        """任务日志目录（/data/jobs/<id>.log）。"""
        return Path(self.data_dir) / "jobs"

    @property
    def mode_root(self) -> str:
        """当前模式下备份源的根前缀：host → /host，sources → /sources。"""
        return self.host_root if self.restic_web_mode == "host" else self.sources_root

    @property
    def run_user(self) -> str:
        """容器内当前运行用户名（E03 四象限卡 user 字段，root 档为 "root"）。"""
        uid = os.getuid() if hasattr(os, "getuid") else 0
        if pwd is not None:
            try:
                return pwd.getpwuid(uid).pw_name
            except KeyError:
                return str(uid)
        return str(uid)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """进程内单例配置（测试可用 get_settings.cache_clear() 重置）。"""
    return Settings()
