"""应用配置：由 pydantic-settings 从环境变量读取（方案 §4.1 部署项 vs 界面项边界）。

字段名即环境变量名的大写形式，例如 ``sources_root`` ← ``SOURCES_ROOT``。
compose 中只保留"容器怎么跑"的部署事实；业务配置一律走 DB（config 表）与 Web 界面。
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.constants import (
    DB_BUSY_TIMEOUT_MS,
    DEFAULT_RESTORE_ROOT,
    DEFAULT_SOURCES_ROOT,
    DEPLOY_MODE_AUTO,
    SSE_HEARTBEAT_SECONDS,
)

try:  # pragma: no cover - Windows 等非 POSIX 平台无 pwd 模块
    import pwd
except ImportError:  # pragma: no cover
    pwd = None  # type: ignore[assignment]


class Settings(BaseSettings):
    """Web 版运行期配置。"""

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore", env_file=None)

    # ---- 部署项（compose 决定，运行期只读）----
    tz: str = "UTC"

    # ---- 路径（§1.2 部署拓扑）----
    data_dir: str = "/data"
    cache_dir: str = "/cache/restic"
    # 备份源根（A0 模式收敛后唯一形态：只认容器内可见目录树，对齐 Duplicati）。
    # 远端数据（NFS/SMB/S3/B2…）经宿主机挂载或 rclone mount 映射进本前缀。
    sources_root: str = DEFAULT_SOURCES_ROOT
    restore_root: str = DEFAULT_RESTORE_ROOT
    repo_root: str = "/repo"
    # 部署形态（设计方案 §3.1）：docker | host | auto（默认 auto = 自动检测）。
    # 自动检测只决定**默认值**（浏览根），不充当安全边界；安全边界是 deny 前缀 + 此处显式覆盖。
    deploy_mode: str = DEPLOY_MODE_AUTO
    # 仓库密码文件**根目录**（E5 per-repo 布局）：实际路径 = <passfile_root>/<repo_id>/password。
    # 当前仅 `default` 一行 → /data/repos/default/password；R-023 多仓库时按 repo_id 派生。
    passfile_root: str = "/data/repos"
    # 前端 SPA 静态产物目录（单容器方案 §2：同一端口提供 API 与页面）。
    # 目录不存在时后端不挂载静态路由，`/` 仍返回统一 JSON 404（本地开发由 Vite dev server 提供页面）。
    static_dir: str = "/app/webroot"
    # rclone 配置文件路径（A1 万能后端；compose 把宿主机 `rclone config` 生成的 conf 挂进来）。
    # 未设置时 rclone 用自身默认（$HOME/.config/rclone/rclone.conf）——容器内 HOME 随运行
    # 用户变化（root 档 /root、nonRoot 档 /home/backup），显式指定更稳。该值经 os.environ
    # 原样传给 restic 子进程、再被 rclone 子进程读取（RCLONE_CONFIG）；本字段同时供
    # 只读探测回报 conf 位置（Q17：UI 不生成、不编辑、不展示 conf 内容）。
    rclone_config: str | None = None

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

    # ---- 可调阈值（默认值与 constants 保持一致；反代超时/高并发部署可覆盖）----
    # SSE 空闲心跳间隔（秒）。反代 idle timeout 明显小于 15s 的部署需下调，
    # 否则 nginx/caddy 会先于心跳断开长连接，前端误判为断流并重连。
    sse_heartbeat_seconds: float = SSE_HEARTBEAT_SECONDS
    # SQLite 锁等待超时（毫秒）。高并发落库或外部工具长时间读库时若频繁
    # SQLITE_BUSY 可上调；过大则失败延迟变长，需与单写串行策略一起权衡。
    db_busy_timeout_ms: int = DB_BUSY_TIMEOUT_MS

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
    def copy_log_dir(self) -> Path:
        """手动 copy 的 restic 原始输出留存目录（/data/logs/copy/<name>.log）。

        copy 没有 JSON 输出、也没有 job 日志（手动操作不经 JobRunner），原文只在
        这里留档：失败后要判断「到底复制了几条 / 卡在哪」只能靠它。S1：路径字面量
        唯一出处；API 只回文件名，不回本路径（容器形态路径不出 API）。
        """
        return Path(self.data_dir) / "logs" / "copy"

    @property
    def dump_dir(self) -> Path:
        """数据库 dump 产物目录（/data/dump，R-019；S1：路径字面量唯一出处）。"""
        return Path(self.data_dir) / "dump"

    @property
    def audit_archive_dir(self) -> Path:
        """审计归档目录（/data/audit/archive，R-020 裁决3：压缩归档落盘处；
        S1：路径字面量唯一出处）。DB 配置键 ``retention.audit_archive_dir`` 可覆盖。"""
        return Path(self.data_dir) / "audit" / "archive"

    @property
    def browse_root(self) -> str:
        """备份源树的浏览根（设计方案 §3.2 的**单一事实源**）。

        ``docker`` → ``sources_root``（默认 ``/sources``）；``host`` → ``/``。
        启动时算一次（进程内缓存），``PathMapper``、源路径校验、排除规则前缀映射
        **全部改读本值**——UI 允许选的路径与后端接受写入的路径因此永不不一致
        （S2 argv 白名单的前置条件）。
        """
        # 延迟导入：deploy_mode 依赖本模块，顶层导入会成环
        from app.services.deploy_mode import effective_browse_root

        return effective_browse_root(self)

    @property
    def deploy_mode_effective(self) -> str:
        """生效的部署形态（``docker`` / ``host``；``auto`` 已解析为具体值）。"""
        from app.services.deploy_mode import detect_deploy_mode

        return detect_deploy_mode(self)

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

    def passfile_path(self, repo_id: str = "default") -> Path:
        """仓库密码文件路径（E5 per-repo 布局的唯一实现点）。

        布局：``<passfile_root>/<repo_id>/password``（当前仅 ``default`` 一行）。
        ``RESTIC_PASSWORD_FILE`` 显式设置时**优先**（运维逃生舱，覆盖 per-repo 布局）。

        Args:
            repo_id: 仓库标识；R-023 多仓库落地后由调用方按仓库传入。
        """
        override = os.environ.get("RESTIC_PASSWORD_FILE", "").strip()
        if override:
            return Path(override)
        return Path(self.passfile_root) / repo_id / "password"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """进程内单例配置（测试可用 get_settings.cache_clear() 重置）。"""
    return Settings()
