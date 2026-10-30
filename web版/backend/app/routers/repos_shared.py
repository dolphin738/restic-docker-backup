"""仓库路由的共享件（候选 3 五域拆分的公共底座）。

从 ``routers/repos.py`` 抽出：仓库标识校验/生成、Repo→RepoOut 映射、URL 语法校验、
以及跨域共用的 ``_PROTECTED`` 依赖表。抽出的动机是**拆分而非重构**——
五域各自成为独立 APIRouter 后，这些件若留在聚合器里会造成循环 import。

分层纪律：本模块只放**无状态**纯函数与常量，不含任何端点或 request 依赖。
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Sequence
from urllib.parse import urlsplit

from fastapi import Depends

from app.auth.dependencies import require_bootstrap_done, require_session
from app.constants import REPO_ID_MAX_LENGTH, REPO_ID_PATTERN
from app.db.repositories.repo_repo import Repo, ReposRepo
from app.errors import ValidationError
from app.schemas.api_schemas import RepoOut
from app.services.rclone import RcloneProbe
from app.utils.time import now_utc

# 受保护端点统一依赖（与原 repos.py 的 _PROTECTED 同值）
PROTECTED = [Depends(require_session), Depends(require_bootstrap_done)]

# 仓库标识白名单（与 RepoCreateRequest.id 同源；路径参数侧再校验一次，防 `..` 穿越）
_REPO_ID_RE = re.compile(REPO_ID_PATTERN)


def validate_repo_id(repo_id: str) -> str:
    """校验路径参数中的仓库标识（非法即 422，杜绝拼进密码文件路径时发生穿越）。"""
    if len(repo_id) > REPO_ID_MAX_LENGTH or _REPO_ID_RE.match(repo_id) is None:
        raise ValidationError(
            f"仓库标识非法：仅允许字母、数字、下划线与连字符（1-{REPO_ID_MAX_LENGTH} 字符，且以字母或数字开头）"
        )
    return repo_id


# ---------------------------------------------------------------- A8 仓库标识自动生成
#
# owner 2026-10-17 07:37 立项：POST /repos 的 id 由必填改为可选，缺省服务端生成。
# owner 2026-10-22 裁决改版（单字母前缀方案）：``repo-<26 位小写 ULID>``（31 字符、
# 不可读）→ ``r<YYMMDD>-<6 位随机>``（14 字符，如 ``r261022-k3f9x2``）：
# - 短且可读，日期段让人一眼看出创建时间，字典序即时间序（百年内）；
# - 单字母 ``r`` 前缀保留「服务端生成」的辨识度，同时满足「小写字母开头」的
#   REPO_ID_PATTERN 硬约束（纯日期开头 ``261022-…`` 会被 create 拒掉）；
# - 存量 ``repo-<ULID>`` id 全部照旧工作，本规则只影响新建，零迁移。
# 落库前 exists 查重 + 重试（碰撞概率极低，重试只是形式化兜底）。

# Crockford Base32 小写字母表（剔除易混淆的 i/l/o/u，与密码生成字符集同思路）
_ID_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"
_ID_RANDOM_LENGTH = 6


def new_repo_id() -> str:
    """生成一个候选仓库标识（``r<YYMMDD>-<6 位小写 Base32 随机>``，14 字符）。

    日期段取 **UTC**（C8：时间语义只走 ``utils/time``）：UTC 口径无环境依赖、
    跨部署可比；若用本地时区，「同日」边界会随部署时区漂移。随机段走
    ``secrets.choice``（CSPRNG）。
    """
    stamp = now_utc().strftime("%y%m%d")
    suffix = "".join(secrets.choice(_ID_ALPHABET) for _ in range(_ID_RANDOM_LENGTH))
    return f"r{stamp}-{suffix}"


async def resolve_repo_id(repos_repo: ReposRepo, provided: str | None) -> str:
    """A8：解析新建仓库的标识。

    - 显式提供（非空）→ 原样返回，落库时再由 ``ReposRepo.create`` 做形态 + 存在性校验；
    - 留空 → 服务端生成 ``r<YYMMDD>-<6 位随机>``，生成前 exists 查重，碰撞重试（上限 10 次）。
    """
    candidate = (provided or "").strip()
    if candidate:
        return candidate
    for _ in range(10):
        generated = new_repo_id()
        if not await repos_repo.exists(generated):
            return generated
    # 理论不可达：26 位随机空间下单日碰撞概率极低；兜底抛错避免无限循环
    raise ValidationError("无法生成唯一仓库标识，请稍后重试或显式指定 id", data={})


def to_out(repo: Repo, passfile: str) -> RepoOut:
    """Repo → RepoOut（type 派生 + passfile_status 只读派生）。"""
    return RepoOut(
        id=repo.id,
        name=repo.name,
        type=repo.type,
        url=repo.url,
        host_tag=repo.host_tag,
        url_source=repo.url_source,
        host_tag_source=repo.host_tag_source,
        passfile_status=passfile,
        created_at=repo.created_at,
    )


# ---------------------------------------------------------------- A1 URL 严格校验（Q20）
#
# 依据 restic 0.19.1 各后端 ParseConfig 的官方语法逐一登记（源码核验，非猜测）：
# - s3/b2/local **保持原状不校验**（owner 裁定 Q20：既有形态零回归）；
# - sftp/azure/gs/rest/rclone 新增类型做语法校验；rclone 另加 remote 存在性校验
#   （``rclone listremotes`` 只读探测，remote 不在册 → 422，防"保存即失败"）。
# 校验只认**写入路径**（POST/PUT），env 同步（运维显式设置）不走此门。

# sftp 双形态（restic sftp.ParseConfig）：URL 形态支持端口/IPv6，scp 形态不支持端口。
# restic sftp 后端是 location.NoPassword + 外部 ssh 子进程（免密登录假设）——URL 里的
# 密码会被 scp 解析当作用户名的一部分静默误解，故显式拒绝并指向私钥挂载部署方式。
# user 段均不允许 ":"（密码内嵌检测 + 双形态一致）；scp 形态允许 user@domain@host
# （restic SplitN(host,"@",3) 支持的域限定用户形态）。
_SFTP_URL_FORM = re.compile(
    r"^sftp://(?P<user>[^\s@/:]+)@(?P<host>\[[^\s@/]+\]|[^\s@/:]+)(?::(?P<port>\d+))?/(?P<path>\S+)$"
)
_SFTP_SCP_FORM = re.compile(r"^sftp:(?P<user>[^\s:]+)@(?P<host>[^\s@/:]+):(?P<path>[^\s:]+)$")
# Azure 容器名规则（官方：3-63 位小写字母/数字/连字符，首尾字母数字）
_AZURE_CONTAINER_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?$")
# GCS 存储桶名（官方：3-63 位小写字母/数字/点/下划线/连字符；此处取中等强度子集）
_GCS_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]$")

def validate_repo_url_syntax(url: str) -> None:
    """按协议前缀做 URL 语法校验（纯语法；rclone 的 remote 存在性校验在调用方）。

    Raises:
        ValidationError: 语法非法（422，data 带 url 与可操作提示）。
    """
    stripped = url.strip()
    lowered = stripped.lower()
    if lowered.startswith("sftp:"):
        _validate_sftp_url(stripped)
    elif lowered.startswith("azure:"):
        _validate_azure_url(stripped)
    elif lowered.startswith("gs:"):
        _validate_gs_url(stripped)
    elif lowered.startswith("rest:"):
        _validate_rest_url(stripped)
    # s3:/b2:/本地路径：Q20 裁定不校验（零回归）；rclone: 语法见 _validate_rclone_url_syntax


def _validate_sftp_url(url: str) -> None:
    """sftp 双形态校验；拒密码内嵌（restic 无密码认证，走了也是静默误解）。"""
    if ":" in url[len("sftp:") :].split("@", 1)[0]:
        raise ValidationError(
            "sftp 地址不支持密码认证：restic 的 sftp 后端经系统 ssh 免密登录，"
            "URL 内密码会被当作用户名一部分静默误解。请挂载 SSH 私钥（见部署指南）",
            data={"url": url},
        )
    if _SFTP_URL_FORM.match(url) or _SFTP_SCP_FORM.match(url):
        return
    raise ValidationError(
        "sftp 地址应为 sftp:user@host:目录 或 sftp://user@host[:端口]/目录"
        "（目录必填；scp 形态不支持端口，需要端口请用 sftp:// 形态）",
        data={"url": url},
    )


def _validate_azure_url(url: str) -> None:
    """azure:container:/prefix 校验（restic azure.ParseConfig：冒号必填）。"""
    rest = url[len("azure:") :]
    container, sep, _prefix = rest.partition(":")
    if not sep or not _AZURE_CONTAINER_RE.match(container):
        raise ValidationError(
            "azure 地址应为 azure:容器名:/前缀（前缀可为空；容器名 3-63 位小写字母/数字/连字符）",
            data={"url": url},
        )


def _validate_gs_url(url: str) -> None:
    """gs:bucket:/prefix 校验（restic gs.ParseConfig：冒号必填；前缀是 gs: 不是 gcs:）。"""
    rest = url[len("gs:") :]
    bucket, sep, _prefix = rest.partition(":")
    if not sep or not _GCS_BUCKET_RE.match(bucket):
        raise ValidationError(
            "gcs 地址应为 gs:桶名:/前缀（restic 前缀是 gs:；前缀可为空，冒号必填）",
            data={"url": url},
        )


def _validate_rest_url(url: str) -> None:
    """rest:http(s)://... 校验；拒 user:pass@ 内嵌（env 凭据在 URL 无用户信息时才生效）。"""
    rest = url[len("rest:") :]
    if rest.startswith("http+unix://"):
        return  # 本地 socket 形态（rest:http+unix:///tmp/rest.socket:/repo/），路径由 restic 解析
    try:
        parts = urlsplit(rest)
    except ValueError as exc:
        raise ValidationError(f"rest 地址解析失败：{exc}", data={"url": url}) from exc
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValidationError(
            "rest 地址应为 rest:http://主机[:端口][/路径] 或 rest:https://...",
            data={"url": url},
        )
    if parts.username or parts.password:
        # restic rest 后端 ApplyEnvironment：URL 带用户信息时 RESTIC_REST_* 环境变量**不生效**；
        # 且密码会以明文落库（url 列不加密）→ 拒绝并指向加密凭据字段
        raise ValidationError(
            "rest 地址不支持内嵌用户密码：URL 带用户信息时 RESTIC_REST_USERNAME/PASSWORD "
            "环境变量将失效，且密码会明文存入 url 列。请只填主机地址，"
            "用户名/密码走下方凭据字段（加密存储）",
            data={"url": url},
        )


def validate_rclone_url_syntax(url: str) -> str:
    """rclone:remote:path 语法校验；返回 remote 名（存在性校验由调用方异步执行）。"""
    spec = url[len("rclone:") :]
    remote, sep, path = spec.partition(":")
    if not sep or not remote or not path or any(ch.isspace() for ch in spec):
        raise ValidationError(
            "rclone 地址应为 rclone:远端名:路径（远端名须与宿主机 rclone config 中配置的一致；"
            "路径为桶内路径，如 rclone:myremote:mybucket/restic）",
            data={"url": url},
        )
    return remote


def rclone_remote_of(url: str) -> str | None:
    """从 ``rclone:<remote>:<path>`` 宽松取出 remote 名；非 rclone 后端/语法不符返回 ``None``。

    **与校验版的区别**：本函数用于扫描**既有**数据（可能含历史脏值），故不抛异常；
    remote 段按**完整相等**比对（配合此处带尾冒号切分），杜绝 ``foo`` 误命中 ``foobar``。
    """
    text = (url or "").strip()
    if not text.lower().startswith("rclone:"):
        return None
    head, sep, _ = text[len("rclone:") :].partition(":")
    return head if sep and head else None


def count_remote_usage(backed: Sequence[tuple[str, str, str]]) -> dict[str, int]:
    """按 remote 名聚合「直接引用它的仓库数」（含归档仓库，避免漏判）。"""
    counts: dict[str, int] = {}
    for _rid, _name, url in backed:
        remote = rclone_remote_of(url)
        if remote:
            counts[remote] = counts.get(remote, 0) + 1
    return counts


async def validate_repo_url(url: str, probe: RcloneProbe) -> None:
    """仓库 URL 完整校验（语法 + rclone remote 存在性）。空地址跳过（允许日后再补）。

    Raises:
        ValidationError: 语法非法或 rclone remote 不在册（422）。
    """
    stripped = (url or "").strip()
    if not stripped:
        return
    if stripped.lower().startswith("rclone:"):
        remote = validate_rclone_url_syntax(stripped)
        remotes = await probe.list_remotes()
        if not remotes.available:
            # 探测不可用（缺二进制/超时/conf 读取失败）→ 放行语法正确的 URL，
            # 由 E29 连通性测试给出最终判定（原因明示，不静默）
            return
        if remote not in remotes.remotes:
            raise ValidationError(
                f"rclone 远端「{remote}」不在 rclone 配置中（可用远端："
                f"{', '.join(remotes.remotes) or '无'}）——请先在宿主机 rclone config 添加",
                data={"remote": remote, "available_remotes": list(remotes.remotes)},
            )
        return
    validate_repo_url_syntax(stripped)
