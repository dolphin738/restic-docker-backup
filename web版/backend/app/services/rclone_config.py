"""rclone 配置管理（CLI 编排层）：conf 段的查询 / 新增 / 编辑 / 删除。

运行模型（《rclone配置生成工具设计方案》v1.2 裁决 7）：conf 的增删改**全部走
官方 rclone 子命令**——新增/编辑 create/update、删除 delete、查询 dump（JSON），
**不自研 INI 手术**。理由：rclone 保存 conf = 临时文件 + 双 rename 原子替换
（官方机制，见 rclone 文档），自研重写既重复实现（规范 §3.1 v1.13 条款）又无法
保证格式与权限；目录挂载形态下官方路径可用（单文件 bind mount 上 rename 会 EBUSY）。

能力边界（裁决 8/9）：
- **编辑**只走注册表字段级表单（``GENERATORS``），**不开自由改键口**——未知参数
  一律 422，防绕过校验与注入；
- **删除**对全部段开放（含 OAuth 网盘段），确认门与提示分级在前端；
- 凭据值**永不回显**：查询只给非凭据参数 + ``has_*`` 布尔（S3）；失败原因经
  ``_sanitize`` 把本次提交的凭据原文替换为 ``***``。

子进程纪律镜像 ``rclone.py``：不经 shell、stdin 置 DEVNULL（防交互式提示挂住
子进程）、超时 kill；缺二进制 / 超时 / 事件循环不支持均降级为 **503 + 可操作
reason**，绝不裸 500。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from app.constants import RCLONE_CONFIG_TIMEOUT_SECONDS, SUBPROCESS_LOOP_HINT
from app.services.rclone import exec_rclone, resolve_conf_path
from app.services.rclone_forms import (
    LEGACY_META_FILENAME,
    FormEntry,
    FormSnapshot,
    FormStore,
    LegacySidecarStore,
)
from app.settings import Settings

# 远端名白名单（rclone 允许更宽，但收紧可同时防 INI 段名注入与路径化命名）
REMOTE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# 控制字符（含 CR/LF/NUL）禁止出现在任何值里——INI 格式下换行 = 注入新键
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
# 权限类失败与「参数校验失败」分开：前者 503（部署/权限问题），后者 422（用户输入）
_PERMISSION_HINT = re.compile(r"permission denied|read-only file system|operation not permitted", re.I)
# crypt 的 remote 目标支持三态（rclone 官方文档 §Specifying the remote）：
#   ① 远端指针 `remote:path` / `remote:bucket` —— remote 名须在册
#   ② **本地绝对路径** `/path/to/dir`（Linux）、`C:\path` / `C:/path`（Windows）
#      —— 官方明确支持，用于纯本地文件加密（如可移动介质）
#   ③ 无冒号相对路径 —— rclone 按「进程 cwd 下的子目录」处理；容器内 cwd 不可控，
#      用户以为指向某处实际指到别处（静默错），故一律拒绝
_WINDOWS_DRIVE_PATTERN = re.compile(r"^[A-Za-z]:[\\/]")
_REMOTE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")

# 凭据形态 sidecar（owner 裁决 2026-10-27 方案 A；裁决 16 迁 DB）：记录「上次由本服务
# 保存时，该凭据落盘形态是否为 obscure 产物」。**为什么靠记录而不是事后检测**：rclone 的
# ``reveal`` 无认证校验，对任意足够长的 base64 可解码串都「能解出」一串垃圾且 exit 0
# （v1.60.1 真机实证）——事后从 conf 值反推形态不可靠，这正是旧版「已混淆开关失效」的
# 根因（rclone 自行猜测）。本服务每次写入都显式传 ``--obscure``/``--no-obscure``，写入
# 那一刻形态即真值；读取时用 sha256(conf 现值) 比对——conf 被外部手工改过即判「未知」。
# 记录落 ``rclone_credential_forms`` 表（裁决 16，迁 DB 随备份走）；旧 sidecar 文件仅作
# 一次性导入源（services/rclone_forms.py）。

# 存储形态（裁决 16：**开关 = 目标存储形态**，新建与编辑同一语义）
STORE_FORM_OBSCURED = "obscured"
STORE_FORM_PLAIN = "plain"
STORE_FORMS: tuple[str, ...] = (STORE_FORM_OBSCURED, STORE_FORM_PLAIN)


def classify_crypt_remote(value: str) -> tuple[str, str | None]:
    """判定 crypt 底层目标形态：``("local", None)`` / ``("remote", 远端名)`` / ``("relative", None)``。

    注意 Windows 盘符（``C:\\x`` / ``C:/x``）必须判为 local——否则会被误当成
    名为 ``C`` 的远端而报「远端不在册」。
    """
    text = (value or "").strip()
    if text.startswith("/") or text.startswith("\\\\") or _WINDOWS_DRIVE_PATTERN.match(text):
        return "local", None
    head, sep, _ = text.partition(":")
    if sep and _REMOTE_NAME_PATTERN.match(head):
        return "remote", head
    return "relative", None

# 写后校验失败提示（真机 spike A/B：单文件 bind mount 下 rclone 退出码 0 却没写进去）
_MOUNT_HINT = "请将 compose 改为目录挂载 ./rclone:/etc/rclone:rw（宿主机 conf 移入该目录）"
_SILENT_WRITE_LOSS_HINT = (
    "rclone 报告成功但配置未变更（{action}未生效）——多为 conf 是**单文件** bind mount 所致："
    "rclone 保存走临时文件 + rename 替换，单文件挂载点上替换不落地且**不报错**。" + _MOUNT_HINT
)

RcloneExecutor = Callable[[Sequence[str]], Awaitable[tuple[int, str]]]


# ---------------------------------------------------------------- 错误语义


class RcloneConfigError(Exception):
    """rclone 配置操作失败基类；子类带 HTTP 状态码供路由层直接映射。"""

    status_code = 400


class RcloneConfigInvalid(RcloneConfigError):
    """用户输入/参数不合法（422）。"""

    status_code = 422


class RcloneConfigNotFound(RcloneConfigError):
    """目标段不在 conf 中（404）。"""

    status_code = 404


class RcloneConfigConflict(RcloneConfigError):
    """远端名与既有段重名（409）。"""

    status_code = 409


class RcloneConfigUnavailable(RcloneConfigError):
    """rclone 不可用 / conf 不可写（503，带可操作 reason）。"""

    status_code = 503


# ---------------------------------------------------------------- 生成器


class ConfigGenerator:
    """一个 backend 类型的字段级生成器（注册表条目）。

    属性语义：
        type_name: rconf 里的 ``type`` 值（= 请求体 type）。
        required_keys: 新增时必填（编辑时凭据类可留空 = 保持不变）。
        credential_keys: 凭据键——查询只回 ``has_<key>`` 布尔，不回值（S3）。
        display_keys: 非凭据键——查询回显，编辑可改。
        defaults: 非凭据键缺省值（编辑也按全量更新语义套用）。
        choices: 白名单（值必须命中，否则 422）。
        bool_keys: 布尔键（``true/false/1/0`` 归一为 ``true/false``）。
    """

    type_name: ClassVar[str] = ""
    required_keys: ClassVar[tuple[str, ...]] = ()
    credential_keys: ClassVar[tuple[str, ...]] = ()
    display_keys: ClassVar[tuple[str, ...]] = ()
    defaults: ClassVar[dict[str, str]] = {}
    choices: ClassVar[dict[str, tuple[str, ...]]] = {}
    bool_keys: ClassVar[tuple[str, ...]] = ()

    def build_args(self, params: Mapping[str, Any], *, update: bool = False) -> list[str]:
        """校验参数并产出 ``key=value`` 参数序列（**只含注册表内已知键**）。

        Args:
            params: 请求体 params（未知键一律 422，不开自由改键口）。
            update: 编辑模式——凭据键留空表示「保持不变」（不产出该键）。

        Returns:
            供 ``rclone config create/update`` 使用的 ``key=value`` 列表。

        Raises:
            RcloneConfigInvalid: 未知参数 / 缺必填 / 取值不在白名单 / 含控制字符。
        """
        known = tuple(self.display_keys) + tuple(self.credential_keys)
        unknown = sorted(set(params) - set(known))
        if unknown:
            raise RcloneConfigInvalid(f"不支持的参数：{', '.join(unknown)}")

        args: list[str] = []
        emitted: set[str] = set()
        for key in known:
            raw = params.get(key)
            text = _clean_value(key, raw)
            if not text:
                if key in self.credential_keys:
                    continue  # 编辑留空 = 保持；新增缺必填由下方检查拦截
                text = self.defaults.get(key, "")
                if not text:
                    continue
            text = self._coerce(key, text)
            self._validate_value(key, text)
            args.append(f"{key}={text}")
            emitted.add(key)

        for key in self.required_keys:
            # 编辑模式下凭据键可留空（保持不变）；其余必填一律要求出现
            if key in self.credential_keys and update:
                continue
            if key not in emitted:
                raise RcloneConfigInvalid(f"缺少必填参数：{key}")
        return args

    def _coerce(self, key: str, value: str) -> str:
        """按字段类型归一取值（布尔归一 + 白名单校验）。"""
        if key in self.bool_keys:
            return "true" if value.strip().lower() in {"true", "1", "yes", "on"} else "false"
        allowed = self.choices.get(key)
        if allowed and value not in allowed:
            raise RcloneConfigInvalid(f"参数 {key} 取值非法（可选：{', '.join(allowed)}）")
        return value

    def _validate_value(self, key: str, value: str) -> None:
        """子类钩子：跨字段/格式校验（基类无约束）。"""
        del key, value


class CryptGenerator(ConfigGenerator):
    """crypt 后端：加密叠加层（底层远端 + 密码 + 文件名编码策略）。"""

    type_name = "crypt"
    required_keys = ("remote", "password")
    credential_keys = ("password", "password2")
    display_keys = ("remote", "filename_encryption", "directory_name_encryption", "filename_encoding")
    defaults = {
        "filename_encryption": "standard",
        "directory_name_encryption": "true",
        # owner 2026-10-27 改默认：base64（文件名更短，适配大小写敏感远端）；
        # 仍提供 base32（通用）与 base32768（按 UTF-16/码点计长的远端）
        "filename_encoding": "base64",
    }
    choices = {
        "filename_encryption": ("standard", "obfuscate", "off"),
        "filename_encoding": ("base32", "base64", "base32768"),
    }
    bool_keys = ("directory_name_encryption",)

    def _validate_value(self, key: str, value: str) -> None:
        if key != "remote":
            return
        kind, _ = classify_crypt_remote(value)
        if kind == "relative":
            raise RcloneConfigInvalid(
                "底层目标填「远端名:桶内路径」（如 mywebdav:backup/restic）或**本地绝对路径**"
                "（如 /repo/encrypted、C:/backup/encrypted）；相对路径会被 rclone 当作进程当前目录下的子目录"
                "（容器内不可控），已拒绝"
            )


class WebDAVGenerator(ConfigGenerator):
    """webdav 后端：WebDAV 远端（url + vendor + 用户名/密码 + Bearer 令牌）。"""

    type_name = "webdav"
    # url 必填；认证至少提供 user/pass 或 bearer_token 之一（见 build_args 校验）。
    required_keys = ("url",)
    credential_keys = ("pass", "bearer_token")
    display_keys = ("url", "vendor", "user")
    defaults = {"vendor": "other"}
    choices = {"vendor": ("other", "owncloud", "nextcloud", "sharepoint")}

    def build_args(self, params: Mapping[str, Any], *, update: bool = False) -> list[str]:
        args = super().build_args(params, update=update)
        # bearer_token 可替代 user/pass：至少提供其一（更新态下空值 = 保留既有凭据）。
        user = _clean_value("user", params.get("user"))
        pwd = _clean_value("pass", params.get("pass"))
        bearer = _clean_value("bearer_token", params.get("bearer_token"))
        if bearer:
            return args
        if update and not pwd:
            # 更新态：pass 留空 = 保留既有 user/pass，无需再校验
            return args
        if not (user and pwd):
            raise RcloneConfigInvalid("WebDAV 至少需要 user/pass 或 bearer_token 之一")
        return args

    def _validate_value(self, key: str, value: str) -> None:
        if key == "url" and not value.lower().startswith(("http://", "https://")):
            raise RcloneConfigInvalid("WebDAV 地址须以 http:// 或 https:// 开头")


GENERATORS: dict[str, ConfigGenerator] = {
    "crypt": CryptGenerator(),
    "webdav": WebDAVGenerator(),
}
SUPPORTED_TYPES: tuple[str, ...] = tuple(sorted(GENERATORS))


# ---------------------------------------------------------------- 结果模型


@dataclass(frozen=True)
class RcloneConfigItem:
    """一个 conf 段的**只读视图**（凭据零回显）。

    Attributes:
        remote: 段名（远端名）。
        type: conf 段 type；未注册类型也展示（前端灰显不可编辑）。
        managed: type 在注册表内 → 可字段级编辑。
        params: 非凭据参数（managed 段才有；非 managed 恒空）。
        has_credentials: 凭据键 → 是否已配置（布尔，不含值）。
        credential_obscured: 凭据键 → 落盘形态是否 obscure 产物（True/False；None =
            形态未知——非本服务写入、或 conf 被外部改过哈希对不上，就地转换将跳过）。
    """

    remote: str
    type: str
    managed: bool
    params: dict[str, str] = field(default_factory=dict)
    has_credentials: dict[str, bool] = field(default_factory=dict)
    credential_obscured: dict[str, bool | None] = field(default_factory=dict)


@dataclass(frozen=True)
class RcloneConfigList:
    """查询结果的只读快照。"""

    conf_path: str
    conf_present: bool
    configs: tuple[RcloneConfigItem, ...]
    available: bool
    reason: str | None


@dataclass(frozen=True)
class RcloneWriteResult:
    """一次写操作的结果（凭据零回显）。"""

    remote: str
    type: str
    conf_path: str
    action: str  # create | update | delete
    remotes_after: tuple[str, ...]


# ---------------------------------------------------------------- 服务


class RcloneConfigService:
    """rclone conf 管理（CLI 编排 + 字段校验 + 凭据过滤）。

    并发：实例级 ``asyncio.Lock`` 串行全部写操作（应用为 asyncio 单进程），
    写前「dump 预检 → CLI 写 → listremotes 验证」全在锁内，缩小与宿主机编辑
    及 OAuth token 回写的交叠窗口。
    """

    def __init__(
        self,
        settings: Settings,
        executor: RcloneExecutor | None = None,
        forms: FormStore | None = None,
    ) -> None:
        """构造服务。

        Args:
            settings: 运行期配置（conf 路径来自 ``rclone_config``）。
            executor: CLI 执行器（单测注入；默认走 :func:`exec_rclone`）。
            forms: 凭据形态记录存储（生产 = ``rclone_credential_forms`` 表仓储；
                缺省 = 旧 sidecar 文件实现，形态记录随 data_dir 丢失后退化为「未知」）。
        """
        self._settings = settings
        # 默认执行器用 conf 专用超时（rclone 单次启动实测 ~2.3s，探测的 5s 会误判）
        self._executor: RcloneExecutor = executor or (
            lambda argv: exec_rclone(argv, RCLONE_CONFIG_TIMEOUT_SECONDS)
        )
        self._forms: FormStore = forms or LegacySidecarStore(Path(settings.data_dir) / LEGACY_META_FILENAME)
        self._lock = asyncio.Lock()
        self._redactions: list[str] = []

    # ---- 查询 ----

    @property
    def conf_path(self) -> str:
        """生效的 conf 路径（与探测层同一解析，无形态分支）。"""
        return resolve_conf_path(self._settings)

    async def list_configs(self) -> RcloneConfigList:
        """查询 conf 全段（managed 段回显非凭据参数，凭据只给 ``has_*`` 与形态布尔）。"""
        conf_path = self.conf_path
        conf_present = Path(conf_path).is_file()
        try:
            sections = await self._dump_sections()
        except RcloneConfigUnavailable as exc:
            return RcloneConfigList(conf_path, conf_present, (), False, str(exc))
        snapshot = await self._load_forms()
        # 惰性对齐：宿主机在外部删段留下的孤儿记录清掉（conf 中已不存在的 remote）
        await self._forms.delete_orphans(sections.keys())
        return RcloneConfigList(
            conf_path=conf_path,
            conf_present=conf_present,
            configs=tuple(self._to_items(sections, snapshot)),
            available=True,
            reason=None,
        )

    # ---- 写操作 ----

    async def create(
        self,
        remote_name: str,
        type_name: str,
        params: Mapping[str, Any],
        *,
        store_form: str = STORE_FORM_OBSCURED,
        value_obscured: bool = False,
        available_remotes: Sequence[str] | None = None,
    ) -> RcloneWriteResult:
        """新增配置段（段名不得与既有段重名）。

        Args:
            remote_name: 段名（远端名，白名单校验）。
            type_name: 注册表内类型（crypt / webdav）。
            params: 字段值（未知键 422）。
            store_form: **目标存储形态**（裁决 16）：``obscured`` = 以混淆形态落盘
                （conf 里是 rclone obscure 产物）/ ``plain`` = 以明文落盘。
            value_obscured: 提交的凭据**本身已是**混淆产物（不再混淆一次）。
            available_remotes: 现有远端名清单（校验 crypt 底层远端存在性）；
                None 时以 conf 内既有段名作为清单。
        """
        _check_remote_name(remote_name)
        gen = self._generator(type_name)
        args = gen.build_args(params, update=False)
        self._register_redactions(params, gen)
        async with self._lock:
            sections = await self._dump_sections()
            if remote_name in sections:
                raise RcloneConfigConflict(
                    f"rclone 远端「{remote_name}」已存在（已有：{', '.join(sorted(sections)) or '无'}）"
                )
            self._check_underlying(args, sections, available_remotes)
            argv = [*self._rclone(), "config", "create", remote_name, gen.type_name, *args]
            await self._run([*argv, *self._store_flag(store_form, value_obscured)])
            # 写后校验：单文件 bind mount 下 rclone 退出码为 0 但写入**静默丢失**
            # （真机 spike A/B 实证）——必须以 dump 复核落值，否则 UI 会误报成功。
            final_sections = await self._dump_sections()
            self._verify_write("新增", remote_name, args, final_sections, gen, wait_type=gen.type_name)
            # 形态记录（方案 A）：提交的凭据落盘形态 = 目标存储形态
            target_obscured = store_form == STORE_FORM_OBSCURED
            intended = {
                a.split("=", 1)[0]: target_obscured for a in args if a.split("=", 1)[0] in gen.credential_keys
            }
            await self._record_forms(remote_name, final_sections[remote_name], gen, intended)
        return RcloneWriteResult(
            remote=remote_name,
            type=gen.type_name,
            conf_path=self.conf_path,
            action="create",
            remotes_after=await self._listremotes_safe(),
        )

    async def update(
        self,
        remote_name: str,
        type_name: str,
        params: Mapping[str, Any],
        *,
        store_form: str = STORE_FORM_OBSCURED,
        value_obscured: bool = False,
        declare_form: bool | None = None,
    ) -> RcloneWriteResult:
        """编辑配置段（**全量更新**；凭据留空 = 保持不变）。

        存储形态（裁决 16）：``store_form`` 是**目标存储形态**（``obscured`` / ``plain``），
        新建与编辑同一语义——开关位置不再因模式而反转（此前编辑态把开关当「已存形态」、
        新建态当「提交值形态」，同一件事对应相反的开关位）。

        就地转换（方案 A）：凭据留空但**目标形态与已存形态不一致**且形态已知时，按目标
        形态就地重写该凭据（先 ``reveal`` 解明文，再以显式 flag 重存）——不回填、明文
        不出后端。

        形态声明（``declare_form``）：**形态未知**的凭据（记录缺失——历史配置由宿主机
        手工维护、或本功能上线前写入）无法自动判定是明文还是混淆值（``rclone reveal``
        无认证校验，事后检测不可靠，§12.5 实证）。此时允许用户**一次性声明**该凭据的
        现值形态：声明只写形态记录、**不动 conf 里的密码**，声明后即可用开关做真正的
        就地转换。声明与转换**互斥**（同一次请求不同时做）。
        """
        _check_remote_name(remote_name)
        gen = self._generator(type_name)
        args = gen.build_args(params, update=True)
        self._register_redactions(params, gen)
        async with self._lock:
            sections = await self._dump_sections()
            existing = sections.get(remote_name)
            if existing is None:
                raise RcloneConfigNotFound(f"rclone 远端「{remote_name}」不在配置中（可能已被删除）")
            existing_type = str(existing.get("type", "")).strip()
            if existing_type != gen.type_name:
                raise RcloneConfigInvalid(
                    f"远端「{remote_name}」的类型是 {existing_type or '未知'}，不能按 {gen.type_name} 编辑"
                )
            # 与 create 同口径：编辑把底层远端改成不在册的也能存进去（保存成功、用时报错），
            # 故编辑同样过在册校验（local 形态在 _check_underlying 内跳过）
            self._check_underlying(args, sections, None)
            snapshot = await self._load_forms()
            target_obscured = store_form == STORE_FORM_OBSCURED
            intended: dict[str, bool] = {}
            submitted = {a.split("=", 1)[0] for a in args if "=" in a}
            transforms: list[tuple[str, str, bool]] = []
            for key in gen.credential_keys:
                if key in submitted:
                    # 提交了新值 → 落盘形态 = 目标存储形态
                    intended[key] = target_obscured
                    continue
                stored = str(existing.get(key) or "").strip()
                if not stored:
                    continue
                form = self._credential_form(remote_name, key, stored, snapshot)
                if form is None:
                    # 形态未知：只有用户明确声明才登记（否则保持原样、形态仍未知）
                    if declare_form is not None:
                        intended[key] = declare_form
                    continue
                if declare_form is not None or form == target_obscured:
                    continue  # 声明请求不做转换；目标形态与已存形态一致则无需转换
                plain = await self._reveal(stored) if form else stored
                transforms.append((key, plain, target_obscured))
                intended[key] = target_obscured
            # 就地转换走**独立命令**：--obscure/--no-obscure 是命令级 flag，与主更新
            # 提交的凭据语义可能相反，不能混在一条命令里。
            # 目标=混淆 → --obscure（明文混淆后落盘）；目标=明文 → --no-obscure（原样落盘）。
            for key, plain, target in transforms:
                await self._run(
                    [
                        *self._rclone(),
                        "config",
                        "update",
                        remote_name,
                        f"{key}={plain}",
                        *self._target_flag(target),
                    ]
                )
            if args:
                await self._run(
                    [
                        *self._rclone(),
                        "config",
                        "update",
                        remote_name,
                        *args,
                        *self._store_flag(store_form, value_obscured),
                    ]
                )
            final_sections = await self._dump_sections()
            self._verify_write("编辑", remote_name, args, final_sections, gen)
            await self._record_forms(remote_name, final_sections[remote_name], gen, intended)
        return RcloneWriteResult(
            remote=remote_name,
            type=gen.type_name,
            conf_path=self.conf_path,
            action="update",
            remotes_after=await self._listremotes_safe(),
        )

    async def delete(self, remote_name: str) -> RcloneWriteResult:
        """删除配置段（**全段可删**，含 OAuth 网盘段——删后需宿主机重新授权）。"""
        _check_remote_name(remote_name)
        async with self._lock:
            sections = await self._dump_sections()
            if remote_name not in sections:
                raise RcloneConfigNotFound(f"rclone 远端「{remote_name}」不在配置中")
            type_name = str(sections[remote_name].get("type", "")).strip()
            await self._run([*self._rclone(), "config", "delete", remote_name])
            if remote_name in await self._dump_sections():
                raise RcloneConfigUnavailable(_SILENT_WRITE_LOSS_HINT.format(action="删除"))
            # 段没了，形态记录同事务清账（不留垃圾；否则同名重建会撞上旧记录）
            await self._forms.delete_remote(remote_name)
        return RcloneWriteResult(
            remote=remote_name,
            type=type_name,
            conf_path=self.conf_path,
            action="delete",
            remotes_after=await self._listremotes_safe(),
        )

    # ---- 内部 ----

    # ---- 凭据形态记录（方案 A：写入时记录、读取时哈希校验；裁决 16 落 DB） ----

    async def _load_forms(self) -> FormSnapshot:
        """读形态快照；存储不可用/记录缺失一律按空处理（形态退化为「未知」）。"""
        try:
            return await self._forms.load()
        except Exception:  # noqa: BLE001 - 形态记录是辅助信息，读失败不得影响主流程
            return {}

    @staticmethod
    def _value_hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _credential_form(
        self, remote: str, key: str, value: str, snapshot: FormSnapshot
    ) -> bool | None:
        """查某凭据的落盘形态；哈希对不上（conf 被外部改过）→ ``None``（未知）。"""
        entry = (snapshot.get(remote) or {}).get(key)
        if entry is None:
            return None
        obscured, digest = entry
        if digest != self._value_hash(value):
            return None
        return obscured

    async def _record_forms(
        self,
        remote: str,
        section: Mapping[str, Any],
        gen: ConfigGenerator,
        intended: Mapping[str, bool],
    ) -> None:
        """把本次写操作已知的形态落库（``intended`` = 凭据键 → 落盘形态布尔）。

        conf 里已不存在的键移除记录；intended 未覆盖的键（保持原值的）哈希仍匹配则
        保留旧记录，否则记「未知」（``obscured: false`` + 空哈希 → 读取时判未知）。
        """
        snapshot = await self._load_forms()
        old = snapshot.get(remote) or {}
        entries: dict[str, FormEntry] = {}
        for key in gen.credential_keys:
            value = str(section.get(key) or "").strip()
            if not value:
                continue
            digest = self._value_hash(value)
            if key in intended:
                entries[key] = (intended[key], digest)
                continue
            previous = old.get(key)
            if previous is not None and previous[1] == digest:
                entries[key] = previous
            # 哈希对不上的旧记录**不保留**（外部改过 → 形态未知，由下次声明重建）
        try:
            await self._forms.replace_remote(remote, entries)
        except Exception:  # noqa: BLE001 - 形态记录写失败不得让已完成的写操作报错
            return

    async def _reveal(self, value: str) -> str:
        """``rclone reveal``：把混淆形态的凭据解回明文（就地转换用）。

        **只对 sidecar 记录为混淆形态的值调用**——reveal 无认证校验，喂任意串会
        「解出」垃圾且 exit 0（真机实证），形态必须来自写入时记录而非事后检测。
        解出的明文登记进脱敏表（S3：错误信息不得回显）。
        """
        self._redactions.append(value)
        try:
            out = await self._run([*self._rclone(), "reveal", value])
        except RcloneConfigInvalid as exc:
            raise RcloneConfigUnavailable(f"凭据混淆值解析失败（rclone reveal）：{exc}") from exc
        plain = (out or "").strip()
        if not plain:
            raise RcloneConfigUnavailable("凭据混淆值解析失败（rclone reveal 无输出）")
        self._redactions.append(plain)
        return plain

    def _generator(self, type_name: str) -> ConfigGenerator:
        gen = GENERATORS.get((type_name or "").strip())
        if gen is None:
            raise RcloneConfigInvalid(f"不支持的配置类型：{type_name or '空'}（可选：{', '.join(SUPPORTED_TYPES)}）")
        return gen

    def _rclone(self) -> tuple[str, ...]:
        """rclone 前缀：**显式带 ``--config``**，确保命令作用于我们回报的那个 conf。

        不显式指定时 rclone 走 ``$HOME/.config/rclone/rclone.conf``——主机档与
        容器档 HOME 不同（容器还分 root/nonRoot 档），会导致「读写的 conf 与 UI
        展示的路径不是同一个」（真机 spike 实测踩到）。
        """
        return ("rclone", "--config", self.conf_path)

    async def _dump_sections(self) -> dict[str, dict[str, Any]]:
        out = await self._run([*self._rclone(), "config", "dump"])
        return _parse_dump(out)

    def _to_items(self, sections: Mapping[str, dict[str, Any]], meta: Mapping[str, Any]) -> list[RcloneConfigItem]:
        items: list[RcloneConfigItem] = []
        for name in sorted(sections):
            section = sections[name]
            type_name = str(section.get("type", "")).strip()
            gen = GENERATORS.get(type_name)
            if gen is None:
                # 非注册类型（OAuth 网盘 / 宿主机手工配置）：只露名字与 type
                items.append(RcloneConfigItem(remote=name, type=type_name, managed=False))
                continue
            params = {
                key: str(section[key])
                for key in gen.display_keys
                if section.get(key) is not None and str(section[key]).strip()
            }
            has_credentials = {key: bool(section.get(key)) for key in gen.credential_keys}
            credential_obscured = {
                key: (
                    self._credential_form(name, key, str(section.get(key) or ""), meta)
                    if section.get(key)
                    else None
                )
                for key in gen.credential_keys
            }
            items.append(
                RcloneConfigItem(
                    remote=name,
                    type=type_name,
                    managed=True,
                    params=params,
                    has_credentials=has_credentials,
                    credential_obscured=credential_obscured,
                )
            )
        return items

    def _verify_write(
        self,
        action: str,
        remote_name: str,
        args: Sequence[str],
        sections: Mapping[str, dict[str, Any]],
        gen: ConfigGenerator,
        *,
        wait_type: str = "",
    ) -> None:
        """写后复核落值（单文件 bind mount 下 CLI 会 exit 0 却不落盘）。

        凭据键跳过比对：conf 里存的是混淆值，与提交的明文不可直接比较。

        Raises:
            RcloneConfigUnavailable: 段缺失或落值与提交不符（多为挂载形态问题）。
        """
        section = sections.get(remote_name)
        if section is None:
            raise RcloneConfigUnavailable(_SILENT_WRITE_LOSS_HINT.format(action=action))
        if wait_type and str(section.get("type", "")).strip() != wait_type:
            raise RcloneConfigUnavailable(_SILENT_WRITE_LOSS_HINT.format(action=action))
        for key, value in (a.split("=", 1) for a in args if "=" in a):
            if key in gen.credential_keys:
                continue
            if str(section.get(key, "")) != value:
                raise RcloneConfigUnavailable(_SILENT_WRITE_LOSS_HINT.format(action=action))

    def _check_underlying(
        self, args: Sequence[str], sections: Mapping[str, dict[str, Any]], available_remotes: Sequence[str] | None
    ) -> None:
        """crypt 的底层目标校验（防「保存即失败」）。

        - ``远端名:路径``：远端名必须在册；
        - **本地绝对路径**：跳过在册校验（rclone 会在需要时自动创建目录，官方文档确认）；
        - 相对路径：`CryptGenerator._validate_value` 已拒绝，不会走到这里。
        """
        base = next((a.split("=", 1)[1] for a in args if a.startswith("remote=")), None)
        if base is None:
            return
        kind, target = classify_crypt_remote(base)
        if kind != "remote" or target is None:
            return
        pool = tuple(sections) if available_remotes is None else tuple(available_remotes)
        if target not in pool:
            raise RcloneConfigInvalid(
                f"底层远端「{target}」不在 rclone 配置中（可用远端：{', '.join(sorted(pool)[:10]) or '无'}）"
            )

    def _register_redactions(self, params: Mapping[str, Any], gen: ConfigGenerator) -> None:
        """登记本次提交的凭据原文，供错误信息脱敏（S3）。"""
        self._redactions = [
            str(params[key]) for key in gen.credential_keys if str(params.get(key) or "").strip()
        ]

    @staticmethod
    def _store_flag(store_form: str, value_obscured: bool) -> tuple[str, ...]:
        """按「目标存储形态 + 提交值形态」产出 rclone flag（裁决 16，两轴正交）。

        | 目标存储形态 | 提交的值 | flag | 落盘形态 |
        |---|---|---|---|
        | 混淆（obscured） | 明文 | ``--obscure`` | 混淆（rclone 混淆一次） |
        | 混淆（obscured） | 混淆产物 | ``--no-obscure`` | 混淆（原样落盘） |
        | 明文（plain） | 明文 | ``--no-obscure`` | 明文（原样落盘） |
        | 明文（plain） | 混淆产物 | —— | **矛盾** → 422 |

        ⚠️ 两个 flag 必须显式传，不能靠「不加 flag」表达——rclone 默认行为是**自行判断**
        （``automatically obscure them if they aren't already obscured``），对多数看似
        已混淆的值会照原样写入，于是「不加 flag」与「加 ``--obscure``」在 conf 里产出
        完全一样，界面开关等于失效（2026-10-27 真机实拍：开关开/关生成的 conf 逐字节相同）。
        """
        if store_form not in STORE_FORMS:
            raise RcloneConfigInvalid(f"存储形态非法：{store_form or '空'}（可选：{', '.join(STORE_FORMS)}）")
        if store_form == STORE_FORM_PLAIN and value_obscured:
            raise RcloneConfigInvalid("存储形态为明文，但提交的值是已混淆产物——两者矛盾，请改存储形态或改填明文")
        if store_form == STORE_FORM_OBSCURED:
            return ("--no-obscure",) if value_obscured else ("--obscure",)
        return ("--no-obscure",)

    @staticmethod
    def _target_flag(target_obscured: bool) -> tuple[str, ...]:
        """就地转换的目标形态 → flag（转换时我们提交的**一定是明文**）。

        目标=混淆 → ``--obscure``（rclone 混淆一次后落盘）；目标=明文 → ``--no-obscure``
        （原样落盘明文）。
        """
        return ("--obscure",) if target_obscured else ("--no-obscure",)

    async def _run(self, argv: Sequence[str]) -> str:
        """执行一条 rclone 命令；失败统一成带状态码的异常（凭据脱敏）。

        Raises:
            RcloneConfigUnavailable: 缺二进制 / 超时 / 事件循环不支持 / 权限类失败。
            RcloneConfigInvalid: 命令非零退出（多为参数/校验失败）。
        """
        try:
            code, out = await self._executor(list(argv))
        except FileNotFoundError:
            raise RcloneConfigUnavailable(
                "未找到 rclone 可执行文件（容器镜像应已包含；主机档请先在宿主机安装 rclone）"
            ) from None
        except TimeoutError:
            # 单文件 bind mount 下 rclone 写 conf 实测**挂住不返回**——超时是唯一兜底，
            # 故原因文案直接指向挂载形态，避免用户只看到「超时」而无从下手。
            raise RcloneConfigUnavailable(
                f"rclone 命令超时（>{RCLONE_CONFIG_TIMEOUT_SECONDS:g}s）。"
                f"若 conf 位于单文件 bind mount，rclone 保存会卡住：{_MOUNT_HINT}"
            ) from None
        except NotImplementedError:
            # Windows + uvicorn --reload：SelectorEventLoop 不支持子进程
            raise RcloneConfigUnavailable(SUBPROCESS_LOOP_HINT) from None
        if code != 0:
            reason = self._sanitize((out or "").strip())[:200] or f"rclone 命令失败（退出码 {code}）"
            if _PERMISSION_HINT.search(reason):
                raise RcloneConfigUnavailable(
                    f"rclone 配置写入失败（权限/只读文件系统）：{reason}"
                )
            raise RcloneConfigInvalid(reason)
        return out

    def _sanitize(self, text: str) -> str:
        """把本次提交的凭据原文从输出里抹掉（rclone 异常可能回显参数）。"""
        for secret in self._redactions:
            if secret and secret in text:
                text = text.replace(secret, "***")
        return text

    async def _listremotes_safe(self) -> tuple[str, ...]:
        """写后验证：新远端可见性（失败不推翻已完成的写，仅返回空清单）。"""
        try:
            out = await self._run([*self._rclone(), "listremotes"])
        except RcloneConfigError:
            return ()
        return tuple(line.strip().rstrip(":").strip() for line in out.splitlines() if line.strip())


def _clean_value(key: str, raw: Any) -> str:
    """取值归一时拦掉控制字符（INI 注入面）并返回去空白文本。"""
    if raw is None:
        return ""
    text = str(raw).strip()
    if _CONTROL_CHARS.search(text):
        raise RcloneConfigInvalid(f"参数 {key} 含控制字符（禁止换行/回车等）")
    return text


def _check_remote_name(remote_name: str) -> None:
    if not REMOTE_NAME_PATTERN.match(remote_name or ""):
        raise RcloneConfigInvalid("远端名只能包含字母、数字、下划线与连字符，长度 1–64")


def _parse_dump(raw: str) -> dict[str, dict[str, Any]]:
    """解析 ``rclone config dump`` 的 JSON（段名 → 键值 dict）。

    **输出不纯**：conf 不存在时 rclone 在 stdout 打 ``{}`` 的同时往 **stderr** 打一行
    ``NOTICE: Config file "..." not found - using defaults``（退出码仍为 0），而本模块
    合并 stdout/stderr——故必须截取首尾花括号之间的 JSON 再解析，否则首次部署
    （裁决 3：conf 不存在直接创建）会被误判为「输出无法解析」而 503（真机 spike 实测）。
    """
    text = (raw or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise RcloneConfigUnavailable("rclone config dump 输出无法解析（未取到 JSON；版本不兼容？）") from None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        raise RcloneConfigUnavailable("rclone config dump 输出无法解析（版本不兼容？）") from None
    if not isinstance(data, dict):
        raise RcloneConfigUnavailable("rclone config dump 输出格式异常") from None
    return {str(name): section for name, section in data.items() if isinstance(section, dict)}
