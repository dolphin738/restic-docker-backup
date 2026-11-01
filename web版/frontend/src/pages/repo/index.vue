<script setup lang="ts">
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import {
  NAlert,
  NButton,
  NCard,
  NCheckbox,
  NDrawer,
  NDrawerContent,
  NForm,
  NFormItem,
  NInput,
  NModal,
  NRadio,
  NRadioGroup,
  NSelect,
  NSpace,
  NText,
  useMessage,
} from 'naive-ui'
import { computed, nextTick, onUnmounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getConfig, putConfig } from '@/api/config'
// E25 缓存端点已在 api/settings.ts（GET /api/cache、POST /api/cache/clear）。
// 设置页缓存维护专区已并入本维护区（2026-10-19），本页是唯一 UI 入口，
// 复用同一对函数，避免第二套路径（/api/ops/cache 并不存在）。
import { getCacheUsage } from '@/api/settings'
import {
  getRepo,
  getRepoStatus,
  listRepos,
  listRcloneConfigs,
  listRcloneRemotes,
  setRepoPassword,
  suggestRepoId,
  testRepo,
  updateRepo,
} from '@/api/repo'
import { isAppError } from '@/api/http'
import type {
  ConfigItem,
  RcloneConfigsOut,
  RcloneRemotesOut,
  RepoOut,
  RepoPasswordRequest,
  RepoTestResponse,
  RepoUpdateRequest,
} from '@/api/types'
import ConfirmGate from '@/components/ConfirmGate.vue'
import RepoListCard from '@/pages/repo/RepoListCard.vue'
import RepoMaintenanceCard from '@/pages/repo/RepoMaintenanceCard.vue'
// 编辑态表单（候选 4 批 4：整块表单移入子组件，本页只留状态与写路径）
import RepoEditForm from '@/pages/repo/RepoEditForm.vue'
// P6 形态层：add-new 行动卡（对齐 Duplicati destinations.html：plus-circle + 标题 + 描述，
// 整卡可点 + Enter/Space）——替代原卡右上角「新建仓库」按钮（用户 2026-10-22 指令改回原样式）。
import { useRepoGates } from '@/composables/useRepoGates'
import PasswordEntryModal from '@/components/PasswordEntryModal.vue'
import StatusTag from '@/components/StatusTag.vue'
// 密钥身份小节（keys/ 是仓库唯一明文文件；只读展示，确认中性化是否生效）
import KeyIdentity from './KeyIdentity.vue'
// add-new 卡图标（Duplicati destinations.html：plus-circle）
import { useUiStore } from '@/stores/ui'
// G4：强随机密码生成（纯前端，本地 crypto 源；轮换面板「生成」按钮用，不经网络）
import { generatePassword as generateRandomPassword } from '@/utils/password'
// rclone 远端配置面板（原设置页分区；B11 迁入本页，A 方案改为抽屉浮层按需唤出，不占主页纵向）
import RcloneConfigPanel from '@/components/settings/RcloneConfigPanel.vue'

const { t } = useI18n()
const ui = useUiStore()
const queryClient = useQueryClient()

const message = useMessage()
ui.setMessageApi(message)
onUnmounted(() => ui.setMessageApi(null))

// ---------------------------------------------------------------- 仓库列表（R-023）
const reposQuery = useQuery({ queryKey: ['repos'], queryFn: listRepos })
const repos = computed<RepoOut[]>(() => reposQuery.data.value ?? [])

// 逐行仓库状态（唯一用途：判定行内「初始化」按钮是否显示——已初始化即隐藏）。
// 后端对同一 repo_id 有 30s 进程内缓存（REPO_STATUS_CACHE_SECONDS），前端同源缓存；
// 结论未到时不渲染按钮，避免已初始化仓库闪现一个按钮再消失。
/** 值得给「初始化」入口的失败分类：只有「未初始化」与兜底 unknown。 */
const INIT_FAILURE_KINDS = new Set(['not_initialized', 'unknown'])


/** 值得给「初始化」入口的失败分类：只有「仓库不存在/未初始化」与兜底 unknown。
 *  其余分类给 init 只会帮倒忙——密码错误 → 重设密码；凭据/网络 → 改配置；锁残留 → 解锁。
 *  判定取自后端结构化 failure_kind（不匹配的中文文案：文案可改、分类稳定）。 */

/** 行内初始化：先把该仓库设为当前（init 作业按当前仓库寻址），再开确认门。 */
function openInit(id: string): void {
  if (id !== ui.currentRepoId) {
    onSetCurrent(id)
  }
  initTargetId.value = id
  initShow.value = true
}

function onSetCurrent(id: string): void {
  ui.setCurrentRepoId(id)
  // 切换仓库：所有仓库域查询（快照/任务/状态/仪表盘）带新 X-Repo-Id 重取
  void queryClient.invalidateQueries()
}

/** 目标位置行卡点击（对齐 Duplicati 选中目标位置语义）：当前行已是选中态，点击空转。 */
function onSelectRepo(id: string): void {
  if (id === ui.currentRepoId) {
    return
  }
  onSetCurrent(id)
}

// ---------------------------------------------------------------- 编辑态（对齐 Duplicati destinations 页两态模型）
// 源码实证（ngclient app/destinations/destinations.html）：selectedDestination 为 null 时是纯列表态
// （零表单零测试按钮）；选中后整页替换为编辑态（← 返回 + 单配置卡 + 内联测试区 + Test & Save 底栏）。
// 配置表单从不常显——列表态下方只放仓库状态与维护区（对应 Duplicati 无此二区，属本项目仓库级运维扩展）。
const editing = ref(false)

/** 进入编辑态：编辑目标即当前仓库（查询数据流硬绑 currentRepoId；切换副作用 = 设为当前，语义一致）。 */
function openManage(id: string): void {
  if (id !== ui.currentRepoId) {
    onSetCurrent(id)
  }
  // 类型下拉**立即**按被管理行回填：列表行数据已在手，不必等 getRepo 返回
  // （此前只靠 watch(repo) 回填 → 异步窗口内显示的是上一个仓库的类型）。
  const row = repos.value.find((x) => x.id === id)
  if (row) {
    repoForm.type = normalizeRepoType(row.type)
  }
  creatingNew.value = false
  editing.value = true
}

/** 返回列表态：新建态清空表单；编辑态回滚到已存值（下次进入同一仓库不残留）。 */
function cancelEdit(): void {
  if (creatingNew.value) {
    clearRepoForm()
    creatingNew.value = false
  } else {
    loadRepoIntoForm()
  }
  clearCredInput()
  editing.value = false
}

/** 表单是否脏（与 onSave 的提交判定同口径：组译 URL/主机标识变更或任一凭据输入非空）。 */
const formDirty = computed(() => {
  if (repoForm.type && composeRepoUrl(repoForm).trim() !== savedUrl.value) {
    return true
  }
  if (repoForm.hostTag !== (repo.value?.host_tag ?? '')) {
    return true
  }
  return credFields.value.some((field) => (credInput[field.suffix] ?? '').trim() !== '')
})

/** 智能主按钮（对齐 Duplicati Test & Save）：新建态 = 创建并测试（2026-10-19 用户裁决：
 *  两枚按钮合一——后端 testRepo 读已落库配置，建完立刻测一次才有即时反馈，无需单独「只建不测」）；
 *  编辑态有变更且未验证 → 先存后测（warn 色，失败转 error 色）；已验证或无变更 → 直接保存（primary）。 */
const saveButton = computed<{ label: string; type: 'primary' | 'warning' | 'error'; run: () => Promise<void> }>(() => {
  if (creatingNew.value) {
    return { label: t('repo.createAndTest'), type: 'primary', run: onCreateAndTest }
  }
  if (formDirty.value && repoTestState.value !== 'success') {
    return {
      label: t('repo.saveAndTest'),
      type: repoTestState.value === 'error' ? 'error' : 'warning',
      run: onSaveAndTest,
    }
  }
  return { label: t('repo.save'), type: 'primary', run: onSave }
})

/** 测试并保存：后端 testRepo 读**已落库**配置（E1 架构约束，先测后存不成立）→ 必须先存再测；
 *  测试通过即返回列表态（对齐 Duplicati save 成功后 selectedDestination=null）。 */
async function onSaveAndTest(): Promise<void> {
  await onSave()
  if (repoTestState.value !== 'idle') {
    // onSave 无变更提前返回时不动测试态；有变更时 onSave 已把结论作废为 idle
    return
  }
  await onTest()
  // 显式断言取值：await onTest() 后 ref.value 被 TS 窄化为 'idle'（它不知道 onTest 会改写），
  // const 按初始化值窄化，直接比较会报 TS2367；as 拓宽后再比较。
  const finalState = repoTestState.value as 'idle' | 'success' | 'error'
  if (finalState === 'success') {
    cancelEdit()
  }
}

// ---------------------------------------------------------------- 仓库类型表单（字段模型对齐 Duplicati destination.config）
// 字段定义以 ngclient destination.config.ts 为权威参照（file/s3/b2/ssh/azure/gcs/rclone 各条目
// 的 customFields + dynamicFields），适配 restic URL 构型（零后端改动：类型与地址仍由服务端从 url 派生）。
// 各类型 URL 方言逐一核验自 restic 0.19.1 对应后端 ParseConfig 源码（2026-10-19）：
//   local  /srv/repo（location.Parse 对无前缀裸路径自动补 local:）   s3  s3:<endpoint>/<bucket>/<path>
//   b2    b2:<bucket>:/<path>（冒号）        gcs  gs:<bucket>:/<path>（冒号）   azure azure:<container>:/<path>（冒号）
//   sftp  sftp:<user>@<host>:/<path>（冒号）  rest rest:<url>（原样）           rclone rclone:<remote>:<path>（冒号）
// URL 组译/解析纯函数在 utils/repoUrl.ts（审查🟡抽出 + 方言表单测），本组件只持字段模型与交互。
import {
  composeRepoUrl,
  isS3Endpoint,
  parseRepoUrl,
  parseS3Url,
  resolveRcloneTarget,
  type RepoTypeKey,
} from '@/utils/repoUrl'
import { CRED_FIELDS, ENDPOINT_OPTIONS, type CredField } from '@/utils/repoCredentials'
import { useRepoProvisioning } from '@/composables/useRepoProvisioning'

/** 类型下拉选项（用户裁决：本项目无后端类型列表端点，用下拉替代 Duplicati 类型选择器列表）。 */
const REPO_TYPES = computed<Array<{ key: RepoTypeKey; label: string }>>(() => [
  { key: 'local', label: t('repo.typeLocal') },
  { key: 'rclone', label: t('repo.typeRclone') },
  { key: 's3', label: t('repo.typeS3') },
  { key: 'b2', label: t('repo.typeB2') },
  { key: 'sftp', label: t('repo.typeSftp') },
  { key: 'azure', label: t('repo.typeAzure') },
  { key: 'gcs', label: t('repo.typeGcs') },
  { key: 'rest', label: t('repo.typeRest') },
])

/** 类型下拉选项（n-select options）。 */
const typeSelectOptions = computed(() => REPO_TYPES.value.map((x) => ({ label: x.label, value: x.key })))

/** 地址由 env 覆盖时整段只读（对齐 Duplicati readOnlyTest）；新建态无已存仓库，恒不锁。 */
const envLocked = computed(() => !creatingNew.value && repo.value?.url_source === 'env')

/** 按类型的地址字段表单（Duplicati customFields ↔ restic URL 段一一映射）。 */
const repoForm = reactive({
  type: '' as RepoTypeKey | '',
  name: '',
  id: '',
  hostTag: '',
  path: '', // local 文件路径 / s3·b2·azure·gcs 文件夹路径 / sftp 远端路径 / rclone 远端路径
  bucket: '', // s3·b2·gcs Bucket / azure 容器
  endpoint: '', // s3 服务器地址（endpoint，进 URL 的 s3:<endpoint>/<bucket>/<path> 首段）
  server: '', // sftp 服务器
  user: '', // sftp 用户名
  url: '', // rest 完整地址
  remote: '', // rclone 远端名
  raw: '', // 已存地址解析失败时的原文回退
})

/** 新建态（区别于编辑态）：类型未选时不渲染地址/凭据区——对齐 Duplicati editBaseUrl=null 分支。 */
const creatingNew = ref(false)
/** 已保存的 URL（脏判定基准；watch(repo) 与 onSave 成功后同步）。 */
const savedUrl = ref('')

/** Bucket/容器名建议值：从系统已配置的其它仓库 URL 提取（同一用户建同类仓库大概率同桶）。
 *  Bucket 名按云账号分配，无跨用户通用值——可用的真实建议源就是本系统已有仓库。 */
const bucketSuggestions = computed(() => {
  const out: string[] = []
  for (const r of reposQuery.data.value ?? []) {
    const url = r.url
    if (!url) {
      continue
    }
    let bucket = ''
    if (r.type === 's3') {
      bucket = parseS3Url(url)?.bucket ?? ''
    } else if (r.type === 'b2' || r.type === 'gcs' || r.type === 'azure') {
      const scheme = r.type === 'b2' ? 'b2:' : r.type === 'gcs' ? 'gs:' : 'azure:'
      const rest = url.startsWith(scheme) ? url.slice(scheme.length) : ''
      const idx = rest.indexOf(':')
      bucket = idx > 0 ? rest.slice(0, idx) : ''
    }
    if (bucket && !out.includes(bucket)) {
      out.push(bucket)
    }
  }
  return out
})

/** Endpoint（服务器地址）建议值：常用云厂商端点在前 + 从系统已配置的其它 s3 仓库 URL 提取
 *  （同账号建同类仓库大概率同端点；MinIO 自建同理）。 */
const endpointSuggestions = computed(() => {
  const out: string[] = [...ENDPOINT_OPTIONS]
  for (const r of reposQuery.data.value ?? []) {
    if (r.type !== 's3' || !r.url) {
      continue
    }
    const endpoint = parseS3Url(r.url)?.endpoint
    if (endpoint && !out.includes(endpoint)) {
      out.push(endpoint)
    }
  }
  return out
})

function clearRepoForm(): void {
  repoForm.type = ''
  repoForm.name = ''
  repoForm.id = ''
  repoForm.hostTag = ''
  repoForm.path = ''
  repoForm.bucket = ''
  repoForm.endpoint = ''
  repoForm.server = ''
  repoForm.user = ''
  repoForm.url = ''
  repoForm.remote = ''
  repoForm.raw = ''
}

/** URL 组译/解析与校验逻辑已抽至 utils/repoUrl.ts（纯函数 + 方言表单测）。 */

/** 按类型的必填校验（对齐 Duplicati isMandatory 红字 * + 保存前门禁）。 */
function repoFormMissing(): string | null {
  switch (repoForm.type) {
    case 'local':
      return repoForm.path.trim() ? null : t('repo.fieldPathRequired')
    case 's3':
      if (!repoForm.endpoint.trim()) {
        return t('repo.fieldEndpointRequired')
      }
      if (!isS3Endpoint(repoForm.endpoint.trim())) {
        return t('repo.fieldEndpointInvalid')
      }
      return repoForm.bucket.trim() ? null : t('repo.fieldBucketRequired')
    case 'sftp':
      if (!repoForm.server.trim()) {
        return t('repo.fieldServerRequired')
      }
      return repoForm.user.trim() ? null : t('repo.fieldUserRequired')
    case 'rest':
      return repoForm.url.trim() ? null : t('repo.fieldRestUrlRequired')
    case 'rclone':
      if (!repoForm.remote.trim()) {
        return t('repo.fieldRemoteRequired')
      }
      // 后端强制 rclone:<远端>:<路径> 非空（path 为空 = 422），前端提前拦
      return repoForm.path.trim() ? null : t('repo.fieldRemotePathRequired')
    default:
      return t('repo.formTypeRequired')
  }
}

/** 类型切换（对齐 Duplicati setDestination：选中新类型 → 地址字段按新类型重建，凭据保留——
 *  凭据键按仓库命名空间（cred.{repo_id}.*）与类型无关，天然保留）。 */
function onTypeChange(value: string | null): void {
  const next = (value ?? '') as RepoTypeKey | ''
  if (next === repoForm.type) {
    return
  }
  repoForm.type = next
  repoForm.path = ''
  repoForm.bucket = ''
  repoForm.endpoint = ''
  repoForm.server = ''
  repoForm.user = ''
  repoForm.url = ''
  repoForm.remote = ''
  repoForm.raw = ''
}

/** A8：向服务端要一个候选仓库标识（r<YYMMDD>-xxxxxx）预填；失败不阻断（用户可手填/留空）。 */
async function regenerateRepoId(): Promise<void> {
  generatingId.value = true
  try {
    const res = await suggestRepoId()
    repoForm.id = res.id
  } catch (error) {
    if (!isAppError(error)) {
      throw error
    }
  } finally {
    generatingId.value = false
  }
}

const generatingId = ref(false)

/** 进入新建态（对齐 Duplicati selectDestination('new')）：编辑态 + 空表单，类型未选前只出类型下拉。 */
function openCreate(): void {
  clearRepoForm()
  creatingNew.value = true
  editing.value = true
}

/** 建仓编排（§4.3 F14b）：本页与任务向导建仓步共用 useRepoProvisioning，
 *  此处只注入本页的切仓钩子与列表失效。 */
const provisioning = useRepoProvisioning({
  setCurrentRepoId: (id: string) => onSetCurrent(id),
  onRepoCreated: async () => {
    await reposQuery.refetch()
  },
})

/** 落库一次创建（不含「之后干什么」的决策）：成功返回新仓库 id 并已设为当前仓库；
 *  失败返回 null（AppError 如 id 重复 409/422 已由全局提示展示）。 */
async function runCreate(): Promise<string | null> {
  if (!repoForm.name.trim()) {
    message.warning(t('repo.nameRequired'))
    return null
  }
  const missing = repoFormMissing()
  if (missing) {
    message.warning(missing)
    return null
  }
  creating.value = true
  try {
    // 建仓与凭据写入走 useRepoProvisioning（与任务向导建仓步同一实现，§4.3 F14b）。
    // 凭据键 = cred.{created.id}.*（id 留空时用服务端生成的，输入按 suffix 暂存
    // 不绑 id，因此「先填凭据再换 id」也不丢）。repoFormMissing 已保证 type 已选。
    const createdId = await provisioning.create({
      id: repoForm.id,
      name: repoForm.name,
      url: composeRepoUrl(repoForm).trim() || null,
      hostTag: repoForm.hostTag,
      type: repoForm.type as RepoTypeKey,
      credInput,
    })
    await provisioning.writeCredentials(createdId, repoForm.type as RepoTypeKey, credInput)
    message.success(t('repo.created'))
    await reposQuery.refetch()
    onSetCurrent(createdId)
    return createdId
  } catch (error) {
    // AppError（如 id 重复 → 409 / 422）已由全局提示展示
    if (!isAppError(error)) {
      throw error
    }
    return null
  } finally {
    creating.value = false
  }
}

/** 创建并测试（2026-10-19 用户裁决：新建态两枚按钮合一，主按钮即此动作）。
 *  后端 testRepo 读的是**已落库**配置
 *  （E1 架构约束，先测后存不成立），所以顺序必然是「先建 → 再测」。
 *  建完**留在编辑态**（切换成刚建出的仓库），立刻跑一次连接测试；测试成功后由
 *  结论区与行内初始化按钮引导下一步（不再自动退回列表——退了就看不到测试结论）。
 *  地址/凭据有误 → 报错可改；地址可达但未初始化 → 结论区直接给初始化入口，
 *  用户不必回列表去找行内按钮。 */
async function onCreateAndTest(): Promise<void> {
  const id = await runCreate()
  if (!id) {
    return
  }
  // 退出新建态 → 测试区与初始化入口可见（两者都只在编辑态渲染）
  creatingNew.value = false
  // 等表单按服务端返回值重新装载（watch(repo) → loadRepoIntoForm）
  await repoQuery.refetch()
  // 放行 currentRepoId watcher（它会清空测试态）：必须早于 onTest 写入结论，否则结论被晚到的清空吃掉
  await nextTick()
  await onTest()
}

const creating = ref(false)

// ---------------------------------------------------------------- 删除仓库（R-023）
/** 删除确认门的「同时删除磁盘数据」勾选（默认不勾：保留磁盘数据，重新添加同 id 仓库可复用）。
 *  勾选后 purge_disk=true：rmtree 仓库目录 + 删密码文件——治「删库后同址新建撞上
 *  打不开的旧仓库」的孤儿死循环；仅本地 FS 可自动清空（远程后端后端 422）。 */
/** 删除目标仓库行（勾选框仅本地 FS 显示：远程后端无本地数据可清）。 */

/** 删除门描述：按是否勾选「同时删除磁盘数据」分两档（口径见方案 §3.1 / §4 L3）。
 *  - 不勾：快照数据与密码文件都留在磁盘，但本应用**再无入口**——需以相同 id 与地址
 *    重建仓库才能恢复；删除误导句「仓库内的 restic 快照不受影响」。
 *  - 勾选：目录内容 + 密码文件一并销毁，**不可逆、无归档可救**。
 */

/** 快照数展示文案：null / 加载中 → 不可知（不写 0）。 */

/** 镜像引用（非空 → 后端必然 409，门里提前说清去哪解）。 */


// ---------------------------------------------------------------- 仓库密码（R-023）
// 密码文件按仓库维度摆放（<PASSFILE_ROOT>/<repo_id>/password），新仓库缺省不存在。
// 它既是 restic 仓库密钥，也是凭据加密的密钥源——缺失时任何仓库操作都注定失败，
// 故此处提供「生成/自填」入口，而不是让用户自己去猜宿主机路径。
// E4 改版：与 C4 轮换共用 PasswordEntryModal（NModal 弹窗 + 两段式输入），API 不变。
const passwordShow = ref(false)
const passwordSaving = ref(false)
/** 生成场景的一次性明文：仅在弹窗存活期展示，关闭即丢弃（不落库、不落日志）。 */
const generatedPassword = ref<string | null>(null)

const passfileMissing = computed(() => (repo.value?.passfile_status ?? 'missing') !== 'configured')

/** 弹窗目标仓库（行内按钮传入；缺省当前仓库）与是否走 force 覆盖（重设场景）。
 *  与行内「初始化」同口径：非当前仓库先切成当前（后端按 X-Repo-Id 寻址）。 */
const passwordTargetId = ref<string | null>(null)
const passwordForce = ref(false)

/** 打开弹窗即清一次性明文（自填草稿由组件在 show 翻转时自清）。
 *  force = 密码文件已存在但仓库打不开（密码错误）→ 后端仅在探测未连接时接受覆盖。 */
function openPassword(id?: string, force = false): void {
  const target = id ?? currentRepoId.value
  if (target !== ui.currentRepoId) {
    onSetCurrent(target)
  }
  passwordTargetId.value = target
  passwordForce.value = force
  generatedPassword.value = null
  passwordShow.value = true
}

function closePassword(): void {
  passwordShow.value = false
  generatedPassword.value = null
  passwordTargetId.value = null
  passwordForce.value = false
}

// ---------------------------------------------------------------- 重建仓库（清空快照 + 新密码重新初始化）
// 与「重设密码」分工：记得原密码→重设（重写文件）；彻底忘记 / 文件夹被旧仓库卡住→重建。
// 2026-10-21 前仅 default 开放；删除仓库不触碰磁盘数据 ⇒ 同址新建会撞上打不开的旧仓库
// （cat config 报「密码错误」），非默认仓库此前没有任何恢复路径（死循环），故放开到全部仓库。
// 仅本地 FS 可自动清空（远程 422，需手动清空后端存储后删除重建）。

async function generatePassword(): Promise<void> {
  await submitPassword({})
}

/** 自填提交（PasswordEntryModal submit-custom 事件）。 */
function onSubmitPassword(password: string): void {
  void submitPassword({ password })
}

async function submitPassword(body: RepoPasswordRequest): Promise<void> {
  const id = passwordTargetId.value ?? currentRepoId.value
  if (!id) {
    return
  }
  passwordSaving.value = true
  try {
    const result = await setRepoPassword(id, { ...body, force: passwordForce.value })
    // 只在生成场景回显；自填场景 password 恒为 null
    generatedPassword.value = result.generated ? result.password : null
    message.success(t('repo.passwordSetOk'))
    // 一屏闭环：此处**故意不作废**测试结论——init 入口挂在「未初始化」结论上，
    // 作废反而让入口消失、多一次点击。repoQuery 回刷新即让 passfileMissing 转 false
    // → 初始化按钮就地解锁，用户「设密码 → 初始化」两步连点即可。
    await Promise.all([reposQuery.refetch(), repoQuery.refetch(), statusQuery.refetch(), configQuery.refetch()])
    // 各行状态一并失效：密码写入后仓库可连性变了，行内按钮判定要立刻跟上
    await queryClient.invalidateQueries({ queryKey: ['repo', 'status'] })
    if (!result.generated) {
      passwordShow.value = false
    }
  } catch (error) {
    if (!isAppError(error)) {
      throw error
    }
  } finally {
    passwordSaving.value = false
  }
}

// ---------------------------------------------------------------- 当前仓库配置（R-023）
const currentRepoId = computed(() => ui.currentRepoId)

const repoQuery = useQuery({
  queryKey: ['repo', currentRepoId],
  queryFn: () => getRepo(currentRepoId.value),
})

const statusQuery = useQuery({
  queryKey: ['repo', 'status', currentRepoId],
  queryFn: () => getRepoStatus(currentRepoId.value),
  refetchInterval: 60_000,
})

const repo = computed(() => repoQuery.data.value ?? null)

/** 后端 type 字段 → 表单类型键（未知/空一律回落 local，与后端派生口径一致）。 */
function normalizeRepoType(ty: unknown): RepoTypeKey {
  return ty === 's3' || ty === 'b2' || ty === 'sftp' || ty === 'azure' || ty === 'gcs' || ty === 'rest' || ty === 'rclone'
    ? ty
    : 'local'
}

const repoType = computed<RepoTypeKey>(() => normalizeRepoType(repo.value?.type))

/** 表单所选类型（新建流 = 下拉选择；编辑流 = 已存类型，切换下拉即时生效）。
 *  凭据字段表 / rclone 探测 / 地址字段一律以它为准——曾误绑 repoType（已存仓库类型），
 *  导致新建流「选择类型后配置项不对」（新建无已存仓库，repoType 恒为当前默认仓库）。 */
const formType = computed<RepoTypeKey>(() => (repoForm.type || repoType.value) as RepoTypeKey)

// A1 rclone 只读探测（Q17）：仅**表单所选类型**为 rclone 时按需拉取（spawn 子进程，60s 内不重取）。
// 定义须晚于 formType（enabled 依赖它）——vue-query 求值 enabled 是即时的。
const rcloneRemotesQuery = useQuery({
  queryKey: ['rclone', 'remotes'],
  queryFn: listRcloneRemotes,
  enabled: computed(() => formType.value === 'rclone'),
  staleTime: 60_000,
})
const rcloneRemotes = computed<RcloneRemotesOut | null>(() => rcloneRemotesQuery.data.value ?? null)

/** rclone conf 段清单（含 type 与 crypt 段的底层 `remote`）——只用于「实际落盘路径」推断。
 *  与 remotes 探测分两个查询：configs 要 dump 整份 conf，比 listremotes 重。 */
const rcloneConfigsQuery = useQuery({
  queryKey: ['rclone', 'configs'],
  queryFn: listRcloneConfigs,
  enabled: computed(() => formType.value === 'rclone'),
  staleTime: 60_000,
})
const rcloneConfigs = computed<RcloneConfigsOut['configs']>(
  () => rcloneConfigsQuery.data.value?.configs ?? [],
)

/** 远端名候选（探测到的段名）——点输入框旁的下拉按钮展开，选中即回填。 */
const rcloneRemoteMenu = computed(() =>
  (rcloneRemotes.value?.remotes ?? []).map((name) => ({ label: name, key: name })),
)

/** 当前输入的远端在 conf 里的段（推断落点用；未探测到 → null）。 */
const selectedRemoteItem = computed(
  () => rcloneConfigs.value.find((c) => c.remote === repoForm.remote.trim()) ?? null,
)

/** 「实际落盘路径」：crypt 段按其**底层 remote** 拼（`/home/222` + `restic`），
 *  其它段只能按 `remote:path` 示意（OAuth 网盘的内部路径不对外暴露）。 */
const rcloneResolved = computed(() =>
  resolveRcloneTarget(repoForm.remote, repoForm.path, selectedRemoteItem.value),
)

/** 同一远端下已有的仓库（路径相同时会互相覆盖，必须提前说清）。 */
const rcloneRemoteUsers = computed(() => {
  const remote = repoForm.remote.trim()
  if (!remote) {
    return []
  }
  const path = repoForm.path.trim().replace(/^\/+/, '')
  const selfId = repo.value?.id
  const out: Array<{ id: string; name: string; path: string; samePath: boolean }> = []
  for (const r of repos.value) {
    if (r.type !== 'rclone' || !r.url || r.id === selfId) {
      continue
    }
    const parsed = parseRepoUrl('rclone', r.url)
    if (!parsed || parsed.remote !== remote) {
      continue
    }
    out.push({
      id: r.id,
      name: r.name,
      path: parsed.path ?? '',
      samePath: (parsed.path ?? '') === path,
    })
  }
  return out
})

// A 方案：rclone 远端配置改为抽屉浮层（不占主页纵向、按需浮出；列表头与建仓表单两处入口）。
// 抽屉关闭时让背后表单的 rcloneRemotes 探测重新拉取，使在抽屉里新建的远端即时在表单状态提示中可见。
const rcloneDrawerOpen = ref(false)
function openRcloneDrawer() {
  rcloneDrawerOpen.value = true
}
watch(rcloneDrawerOpen, (open) => {
  if (!open) {
    rcloneRemotesQuery.refetch()
    rcloneConfigsQuery.refetch()
  }
})

/** 已存仓库 → 表单（对齐 Duplicati mapper.from 的装载路径：URL 解析回字段）。 */
function loadRepoIntoForm(): void {
  const r = repo.value
  if (!r) {
    return
  }
  clearRepoForm()
  repoForm.name = r.name
  repoForm.hostTag = r.host_tag ?? ''
  repoForm.type = repoType.value
  const url = r.url ?? ''
  if (url) {
    const parsed = parseRepoUrl(repoType.value, url)
    if (parsed) {
      Object.assign(repoForm, parsed)
    } else {
      repoForm.raw = url
    }
  }
  savedUrl.value = url
}

watch(
  () => repo.value,
  (r) => {
    if (!r) {
      return
    }
    loadRepoIntoForm()
  },
  { immediate: true },
)

// 切换仓库时清空凭据输入，避免残留；连接结论同样作废（仓库已换）
watch(currentRepoId, () => {
  clearCredInput()
  repoTestState.value = 'idle'
  testResult.value = null
  verifyRequired.value = false
})

// ---------------------------------------------------------------- 凭据（按仓库命名空间）
const configQuery = useQuery({ queryKey: ['config'], queryFn: getConfig })

const credPrefix = computed(() => `cred.${currentRepoId.value}.`)
const credInput = reactive<Record<string, string>>({})

/** 只取当前仓库命名空间内的凭据项（隔离其它仓库的 cred.*）。 */
const credItems = computed(() =>
  (configQuery.data.value?.items ?? []).filter((item) => item.key.startsWith(credPrefix.value)),
)

function itemOf(suffix: string): ConfigItem | undefined {
  return credItems.value.find((item) => item.key === credPrefix.value + suffix)
}

/** 凭据字段表按**表单所选类型**取（formType），而非已存仓库类型（repoType）——
 *  编辑流切换类型后凭据组随之切换；新建流按下拉选择渲染（但保存需仓库 id，见模板注释）。 */
const credFields = computed<CredField[]>(() => CRED_FIELDS[formType.value])

const saving = ref(false)
const testing = ref(false)
const testResult = ref<RepoTestResponse | null>(null)
// B3 自愈：找回丢失快照（restic recover），危险操作 ConfirmGate 关键词 RECOVER。
// 后端早已支持（constants.py JOB_TYPES + job_runner 埋 AUDIT_RECOVER_RUN），此前前端零入口——G1 补齐。

// ---------------------------------------------------------------- C4 密钥轮换 + 手动 copy（搬家）
// 输入方式与 E44 同款（生成→复制 / 自填），与「设置仓库密码」共用 PasswordEntryModal。
// 区别只在生成实现：轮换新密码后端不回显（S3）→ 前端本地生成、确认后才随请求提交。

/** 前端本地生成 32 位随机密码（唯一生成器 utils/password.ts，纯 crypto 源，不经网络、不落任何存储）。 */
function generateRotatePw(): void {
  rotateGenerated.value = generateRandomPassword()
}

// E25 本地缓存：占用查询（服务端 30s 缓存，前端 5 分钟不重取）+ 清空（S6 危险操作）
const cacheQuery = useQuery({
  queryKey: ['ops', 'cache'],
  queryFn: getCacheUsage,
  staleTime: 300_000,
  refetchOnWindowFocus: false,
})

// 候选 4 批 1：9 个危险动作确认门的开关与确认动作全部收进 useRepoGates()
// （此前它们散在本文件各处，状态定义与门渲染相隔约 1700 行）。
const gates = useRepoGates({
  currentRepoId,
  t,
  message,
  queryClient,
  reposQuery,
  repoQuery,
  statusQuery,
  configQuery,
  cacheQuery,
  repos,
  onDeleted: onSetCurrent,
  resetTestResult,
})

// 解构到原名（template 与既有代码零改动）
const {
  unlockShow, unlocking, unlockJobId, onUnlock, onUnlockDone,
  initShow, initializing, initTargetId, initJobMap, onInitGateToggle, onInit, onInitDone,
  repairShow, repairing, repairJobId, repairTarget, onRepair, onRepairDone,
  recoverShow, recovering, recoverJobId, onRecover, onRecoverDone,
  cacheShow, cacheClearing, onClearCache,
  deleteShow, deleting, deleteTarget, deletePurge, deleteTargetRepo,
  deleteDescription, deleteSnapshotText, deleteMirrorRefs, onDeleteClick, confirmDelete,
  rebuildShow, rebuildResultShow, rebuildTargetId, rebuildPassword, rebuildSaving,
  openRebuild, onRebuild, closeRebuild,
  rotateShow, rotating, rotateGenerated, openRotate, onRotate,
  copyShow, copying, copyTarget, copyResult, copySourceLabel, copyOptions, openCopy, onCopy,
} = gates

/** 维护卡的开门请求 → 打开对应确认门（门仍在下方 template 里，生命周期不变）。 */
function onMaintenanceOpen(action: 'unlock' | 'repair' | 'recover' | 'rotate' | 'copy' | 'cache'): void {
  if (action === 'unlock') unlockShow.value = true
  else if (action === 'repair') repairShow.value = true
  else if (action === 'recover') recoverShow.value = true
  else if (action === 'rotate') openRotate()
  else if (action === 'copy') openCopy()
  else cacheShow.value = true
}

/** 维护区作业终态 → 清 jobId 让进度卡消失并刷新状态。 */
function onMaintenanceJobDone(job: 'unlock' | 'repair' | 'recover'): void {
  if (job === 'unlock') onUnlockDone()
  else if (job === 'repair') onRepairDone()
  else onRecoverDone()
}

/** copy 源仓固定为「当前查看的仓库」，弹窗显式标出——否则极易把复制方向看反。 */

/** copy 目标候选：其它仓库中已配地址与密码的（否则后端 422）。 */

// ---------------------------------------------------------------- 保存 / 测试 / 解锁 / 初始化
async function onSave(): Promise<void> {
  const missing = repoFormMissing()
  if (missing) {
    message.warning(missing)
    return
  }
  const nextUrl = repoForm.type ? composeRepoUrl(repoForm).trim() : ''
  const repoPatch: RepoUpdateRequest = {}
  if (nextUrl !== savedUrl.value) {
    repoPatch.url = nextUrl || null
  }
  if (repoForm.hostTag !== (repo.value?.host_tag ?? '')) {
    repoPatch.host_tag = repoForm.hostTag || null
  }
  const credPatch: Record<string, string | null> = {}
  for (const field of credFields.value) {
    const value = credInput[field.suffix]
    if (value) {
      credPatch[credPrefix.value + field.suffix] = value
    }
  }
  if (
    repoPatch.url === undefined &&
    repoPatch.host_tag === undefined &&
    Object.keys(credPatch).length === 0
  ) {
    message.info(t('repo.noChanges'))
    return
  }
  saving.value = true
  try {
    if (repoPatch.url !== undefined || repoPatch.host_tag !== undefined) {
      await updateRepo(currentRepoId.value, repoPatch)
    }
    if (Object.keys(credPatch).length > 0) {
      await putConfig(credPatch)
    }
    message.success(t('repo.saved'))
    clearCredInput()
    savedUrl.value = nextUrl
    // E1：配置已变 → 旧的连接结论作废，强制复验后才放行不可逆动作
    repoTestState.value = 'idle'
    testResult.value = null
    verifyRequired.value = true
    await Promise.all([repoQuery.refetch(), configQuery.refetch()])
    void statusQuery.refetch()
  } finally {
    saving.value = false
  }
}

function clearCredInput(): void {
  for (const key of Object.keys(credInput)) {
    credInput[key] = ''
  }
}

/** E1 连接验证状态机：idle（未验证/结论已作废）→ success / error。
 *
 *  与向导同构，但**门禁形态不同**：后端 `testRepo` 读的是**已落库**的仓库配置，
 *  因此「先测后存」在架构上不成立——凭据必须先保存才能被测到，若把「保存」也挡在
 *  测试之后，用户将永远无法录入第一组凭据（死锁）。等价强门禁取而代之：
 *  任何 URL/凭据变更后，旧结论立即作废（idle），并强制复验后才放行不可逆动作（init）。
 */
const repoTestState = ref<'idle' | 'success' | 'error'>('idle')
/** 配置已保存但尚未复验：此时禁用不可逆的 init（在错误地址上 init 会凭空建出新仓库）。
 *  测试成功即解除；切仓即清零（新仓库是新的验证对象）。 */
const verifyRequired = ref(false)

/** 连接结论作废（切仓 / 配置变更 / 密码写入 / init 完成后）：旧结论描述的是旧状态，
 *  留着只会误导（如「仓库不存在」在 init 成功后仍挂在那里）。 */
function resetTestResult(): void {
  repoTestState.value = 'idle'
  testResult.value = null
}

async function onTest(): Promise<void> {
  testing.value = true
  testResult.value = null
  try {
    testResult.value = await testRepo(currentRepoId.value)
    repoTestState.value = testResult.value.ok ? 'success' : 'error'
    if (testResult.value.ok) {
      verifyRequired.value = false
    }
  } catch {
    // AppError 已由 http 层全局副作用提示（网络错误也包装为 AppError）；本页状态机须落 error，
    // 否则验证态卡在旧值、E1 复验门禁拿不到「已复验」信号（审查💭②：原先无 catch，异常直接外抛）
    repoTestState.value = 'error'
  } finally {
    testing.value = false
  }
}

/** 「仓库不存在 / 尚未初始化」结论的识别标记——对应后端 `services/restic.py`
 *  `_FAILURE_HINTS` 的第三档（failure_reason 返回的中文文案含这些子串）。
 *  含义：地址与凭据已被接受，但仓库还没建（或 bucket/路径不存在）→ 该给初始化入口，
 *  而不是当凭据错误让用户反复改。后端若改文案，此处退化为普通失败提示（不会误判成功）。 */
const NOT_INITIALIZED_MARKERS = ['未初始化', '不存在', 'not initialized', 'unable to open config file', 'no such bucket', 'does not exist']

/** 测试失败且结论落在「未初始化」档位（新建后首次测试最常见）。 */
const testNotInitialized = computed(() => {
  if (repoTestState.value !== 'error') {
    return false
  }
  // 优先用后端结构化分类（稳定）；旧后端无该字段时回落到原因文案匹配
  const kind = testResult.value?.failure_kind
  if (kind) {
    return INIT_FAILURE_KINDS.has(kind)
  }
  const reason = (testResult.value?.reason ?? '').toLowerCase()
  return NOT_INITIALIZED_MARKERS.some((m) => reason.includes(m.toLowerCase()))
})

/** 测试失败且后端判定为「密码错误」（密码文件在但仓库打不开）→ 就地给重设出口（force 覆盖），
 *  与列表行内 passwordAction='reset' 同口径，避免用户退回列表态才能自救。 */
const testPasswordWrong = computed(
  () => repoTestState.value === 'error' && testResult.value?.failure_kind === 'password',
)

// ---------------------------------------------------------------- 展示辅助
const repoStatus = computed(() => statusQuery.data.value ?? null)


/** 字节数人读化（容量卡用；维护卡的缓存展示在 RepoMaintenanceCard 内另有一份）。 */
function formatBytes(value: number): string {
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  let size = value
  let index = 0
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024
    index += 1
  }
  return `${size.toFixed(index === 0 ? 0 : 1)} ${units[index]}`
}

const capacityText = computed(() => {
  // capacity 可能为 null（后端缺省/代理改写/未初始化），?. 兜底——否则整页渲染抛错白屏
  const size = repoStatus.value?.capacity?.size
  return size ? formatBytes(size) : t('common.none')
})

</script>

<template>
  <n-space vertical :size="16">
    <!-- 列表态（对齐 Duplicati destinations 列表态语义）：仓库状态（上）→ 目标位置列表（中）→ 仓库维护（下），零配置表单 -->
    <template v-if="!editing">
    <!-- 仓库状态（独立卡，置于列表上方；用户 2026-10-19 布局指令）：
         状态行 = 连接态/锁定态标签 + 容量（挨着锁标签）+ 右侧「设置仓库密码」入口（仅密码缺失时出现）。 -->
    <n-card :title="t('dashboard.repoCard')" size="small">
      <template #header-extra>
        <n-button size="tiny" secondary :loading="statusQuery.isFetching.value" @click="statusQuery.refetch()">
          {{ t('common.refresh') }}
        </n-button>
      </template>
      <!-- 状态卡左右分栏（用户 2026-10-23 布局指令）：左 = 连接态/容量，右 = 密钥身份，
           两者用【纵向】分隔线并列，不再上下堆叠；≤640px 回落为上下。
           分隔线必须紧贴左内容（左栏按内容收缩，不撑满整行）：撑满会把分隔线推到卡片
           2/3 处，中间留一大片空白、分隔线悬空（用户 2026-10-23 截图反馈）。 -->
      <div class="repo__status-split">
        <div class="repo__status-main">
          <n-space align="center" :size="8">
            <!-- P3c 收口：连接态 / 锁定态（映射不变） -->
            <StatusTag
              :kind="repoStatus?.connected ? 'success' : 'error'"
              :label="repoStatus?.connected ? t('dashboard.repoConnected') : t('dashboard.repoDisconnected')"
            />
            <StatusTag
              :kind="repoStatus?.locked ? 'warning' : 'default'"
              :label="repoStatus?.locked ? t('dashboard.repoLocked') : t('dashboard.repoUnlocked')"
            />
            <!-- 容量紧跟锁标签（原 NStatistic 独立行 → 行内文案，省一行） -->
            <n-text depth="3" class="repo__capacity-inline tabular-nums">
              {{ t('dashboard.repoCapacity') }} {{ capacityText }}
            </n-text>
            <!-- 「设置仓库密码」入口已与「初始化」一样下放到仓库列表行内（每仓一行，按行判定），
                 此处不再放按钮——状态卡只负责展示，动作统一在列表行内，避免两处入口各判一套。 -->
          </n-space>
        </div>
        <!-- 密钥身份（只读）：keys/ 是仓库唯一明文文件，这里确认 init 后的中性化
             是否真的生效、有没有历史遗留的真实身份 key（《云端仓库安全基线》§5）。
             不设 v-if：currentRepoId 恒有值（statusQuery 同样直接依赖它），右栏恒定存在，
             分栏宽度也就不会随条件跳动。 -->
        <div class="repo__status-keys">
          <KeyIdentity :repo-id="currentRepoId" />
        </div>
      </div>
      <!-- 告警通栏置于分栏下方：告警是全卡级信号（错误/锁残留/口令文件缺失），
           不属于左栏事实行；留在左栏会被压到内容宽度、字挤成多行。 -->
      <n-alert v-if="repoStatus?.last_error" class="repo__status-alert" type="error" :show-icon="false">
        {{ t('dashboard.repoLastError') }}：{{ repoStatus.last_error }}
      </n-alert>
      <n-alert v-if="passfileMissing" class="repo__status-alert" type="warning" :show-icon="false">
        {{ t('repo.passfileMissingAlert') }}
      </n-alert>
      <!-- 锁残留属状态信号（留在状态卡）；对应动作按钮已收进下方「仓库维护」分区 -->
      <n-alert v-if="repoStatus?.locked" class="repo__status-alert" type="warning" :show-icon="false">
        {{ t('repo.lockedNote') }}
      </n-alert>
    </n-card>
    <!-- 仓库列表（R-023）：对齐 Duplicati「目标位置」模块（ngclient backup/destination）——
         新增入口 = add-new 行动卡（用户 2026-10-22 指令：由卡右上角按钮改回 Duplicati 原样式；
         早前的大操作卡形态 2026-10-19 曾回退为按钮，现按指令恢复，P6 以共用 ActionCard 落地），
         add-new 卡下直接接仓库行卡（用户 2026-10-22 指令去掉「或管理已保存的仓库」分隔文案，不留空 divider）。
         行卡 = [类型图标][名称 + 状态标签][地址·主机标识][右侧操作]，整行点击 = 设为当前
         （Duplicati 选中目标位置语义；当前行高亮且空转）；删除走 ConfirmGate 三门，default 禁删。 -->
    <!-- 仓库列表卡（候选 4 批 3：RepoListCard 自包含行卡与行内判定） -->
    <RepoListCard
      :repos="repos"
      :current-repo-id="ui.currentRepoId"
      :init-job-map="initJobMap"
      :init-target-id="initTargetId"
      @select="onSelectRepo"
      @manage="openManage"
      @create="openCreate"
      @rclone-drawer="openRcloneDrawer"
      @password="openPassword"
      @init="openInit"
      @rebuild="openRebuild"
      @delete="onDeleteClick"
      @init-done="onInitDone"
    />

    <!-- 维护卡（候选 4 批 2：RepoMaintenanceCard 自包含展示，确认门仍在下方由本页持有） -->
    <RepoMaintenanceCard
      :repo-status="repoStatus"
      :passfile-missing="passfileMissing"
      :unlock-job-id="unlockJobId"
      :repair-job-id="repairJobId"
      :recover-job-id="recoverJobId"
      :unlocking="unlocking"
      :repairing="repairing"
      :recovering="recovering"
      :cache-clearing="cacheClearing"
      @open="onMaintenanceOpen"
      @job-done="onMaintenanceJobDone"
    />

    <!--
      仓库维护（G6：对齐 Duplicati `backup/database` 的 Maintenance 分区范式——
      「先解释风险，再给动作」：分区标题 → 风险说明段 → 动作行）。
      原散落在状态卡内的 unlock / repair 与 C4 轮换、copy 统一收进本分区（init 已上移到仓库行内），
      状态卡回归纯展示；并补上 B3 recover（G1：后端早已支持、前端此前零入口）与
      E25 清空缓存（原设置页专区已并入本分区，本页是唯一 UI 入口，复用 api/settings 的同一对函数）。
      危险动作一律走 ConfirmGate 关键词确认。
    -->
    </template>

    <!-- 编辑态（对齐 Duplicati destinations 编辑态）：← 返回 + 单配置卡；
         选中前配置表单不渲染（源码：selectedDestination === null 分支只有列表）。 -->
    <!-- 编辑态（候选 4 批 4：RepoEditForm 承接整块表单；
         确认门、保存/创建/测试等写路径仍留在本页，组件只 emit 请求。
         repoForm / credInput 以 prop 传入（reactive 对象），v-model 双向仍写回父页同一份状态。 -->
    <template v-else>
      <RepoEditForm
        :repo-form="repoForm"
        :cred-input="credInput"
        :creating-new="creatingNew"
        :current-repo-id="currentRepoId"
        :repo="repo"
        :repo-type="repoType"
        :type-select-options="typeSelectOptions"
        :bucket-suggestions="bucketSuggestions"
        :endpoint-suggestions="endpointSuggestions"
        :cred-fields="credFields"
        :item-of="creatingNew ? null : itemOf"
        :env-locked="envLocked"
        :rclone-remotes="rcloneRemotes"
        :rclone-probe-error="rcloneRemotesQuery.error.value"
        :rclone-remote-menu="rcloneRemoteMenu"
        :rclone-resolved="rcloneResolved"
        :rclone-remote-users="rcloneRemoteUsers"
        :repo-test-state="repoTestState"
        :test-result="testResult"
        :testing="testing"
        :saving="saving"
        :creating="creating"
        :generating-id="generatingId"
        :test-not-initialized="testNotInitialized"
        :test-password-wrong="testPasswordWrong"
        :passfile-missing="passfileMissing"
        :verify-required="verifyRequired"
        :initializing="initializing"
        :init-target-id="initTargetId"
        :init-job-map="initJobMap"
        :save-button="saveButton"
        @cancel="cancelEdit"
        @type-change="onTypeChange"
        @regenerate-id="regenerateRepoId"
        @rclone-drawer="openRcloneDrawer"
        @test="onTest"
        @password="(force: boolean) => openPassword(currentRepoId, force)"
        @init="openInit(currentRepoId)"
        @rebuild="openRebuild()"
        @init-done="(payload) => onInitDone(currentRepoId, payload)"
      />
    </template>

    <!-- A 方案：rclone 远端配置抽屉浮层（列表态 / 编辑态均可唤出；宽度 min(1040px,92vw) 容纳 780px 远端表 + 表单） -->
    <n-drawer
      v-model:show="rcloneDrawerOpen"
      width="min(1040px, 92vw)"
      placement="right"
      :title="t('repo.rcloneManageTitle')"
    >
      <n-drawer-content :native-scrollbar="false">
        <RcloneConfigPanel />
      </n-drawer-content>
    </n-drawer>

    <!-- 手动解锁：三步确认门 -->
    <ConfirmGate
      v-model:show="unlockShow"
      :title="t('repo.unlockBtn')"
      :keyword="'UNLOCK'"
      :description="t('repo.unlockDesc')"
      :loading="unlocking"
      @confirm="onUnlock"
    >
      <template #preview>
        <n-text depth="3">{{ t('repo.unlockPreview') }}</n-text>
      </template>
    </ConfirmGate>

    <!-- 初始化仓库：三步确认门（重复 init 会在错误地址上另建新仓库，须慎重） -->
    <ConfirmGate
      :show="initShow"
      :title="t('repo.initBtn')"
      :keyword="'INIT'"
      :description="t('repo.initDesc')"
      :loading="initializing"
      @update:show="onInitGateToggle"
      @confirm="onInit"
    >
      <template #preview>
        <n-text depth="3">{{ t('repo.initPreview') }}</n-text>
      </template>
    </ConfirmGate>

    <!-- B1 自愈：修复仓库——三步确认门（关键词 REPAIR，重建索引/卷一致性，不可逆提示） -->
    <ConfirmGate
      v-model:show="repairShow"
      :title="t('repo.repairBtn')"
      :keyword="'REPAIR'"
      :description="t('repo.repairDesc')"
      :loading="repairing"
      @confirm="onRepair"
    >
      <template #preview>
        <n-text depth="3">{{ t('repo.repairPreview') }}</n-text>
        <n-radio-group v-model:value="repairTarget" size="small">
          <n-space>
            <n-radio value="index">{{ t('repo.repairTargetIndex') }}</n-radio>
            <n-radio value="packs">{{ t('repo.repairTargetPacks') }}</n-radio>
          </n-space>
        </n-radio-group>
      </template>
    </ConfirmGate>

    <!-- B3 自愈：找回丢失快照——三步确认门（关键词 RECOVER，restic recover 无子命令、无额外参数，
         但会改写仓库元数据，故与 repair 同规格留痕：审计 recover.run） -->
    <ConfirmGate
      v-model:show="recoverShow"
      :title="t('repo.recoverBtn')"
      :keyword="'RECOVER'"
      :description="t('repo.recoverDesc')"
      :loading="recovering"
      @confirm="onRecover"
    >
      <template #preview>
        <n-text depth="3">{{ t('repo.recoverPreview') }}</n-text>
      </template>
    </ConfirmGate>

    <!-- E25 清空本地缓存：三步确认门（关键词 CLEAR）。仅删缓存目录内容，不触碰仓库数据 -->
    <ConfirmGate
      v-model:show="cacheShow"
      :title="t('repo.cacheBtn')"
      :keyword="'CLEAR'"
      :description="t('repo.cacheDesc')"
      :loading="cacheClearing"
      @confirm="onClearCache"
    >
      <template #preview>
        <n-text depth="3">{{ t('repo.cachePreview') }}</n-text>
      </template>
    </ConfirmGate>

    <!-- 删除仓库：两档确认门（删除保护方案 §4 L3）
         默认 = **归档**：数据/密码文件/凭据全留，可一键恢复 → 轻门（无关键词）；
         勾「同时删除磁盘数据」= **销毁**：目录内容 + 口令都没了 → 重门（关键词 = 仓库 id）。 -->
    <ConfirmGate
      v-model:show="deleteShow"
      :title="deletePurge ? t('repo.destroyTitle') : t('repo.archiveTitle')"
      :keyword="deletePurge ? (deleteTarget ?? '') : ''"
      :description="deleteDescription"
      :loading="deleting"
      :confirm-label="deletePurge ? t('repo.destroyBtn') : t('repo.archiveBtn')"
      @confirm="confirmDelete"
    >
      <template #preview>
        <!-- 后果可视化（删除保护方案 P1）：快照总数 + 镜像引用清单。
             统计不阻塞删除门：取不到就显示「不可知」，绝不写 0。 -->
        <div class="repo__del-facts">
          <div class="repo__del-fact">{{ deleteSnapshotText }}</div>
          <div v-if="deleteMirrorRefs.length" class="repo__del-fact repo__del-fact--warn">
            {{ t('repo.usageMirrorRefs', { n: deleteMirrorRefs.length }) }}
            <span class="repo__del-names">{{ deleteMirrorRefs.map((r) => r.name).join(' / ') }}</span>
            <div class="repo__del-hint">{{ t('repo.usageMirrorHint') }}</div>
          </div>
        </div>
        <!-- 磁盘清理勾选（仅本地 FS，默认不勾）：不勾 = 保留磁盘数据与密码文件
             （重新添加同 id 仓库仍可复用，但同址新建会撞上打不开的旧仓库）；
             勾选 = rmtree 仓库目录 + 删密码文件，该地址可干净重用。
             文案已由 ConfirmGate 的 description 告警区统一展示，此处不再重复。 -->
        <n-checkbox v-if="deleteTargetRepo?.type === 'local'" v-model:checked="deletePurge">
          {{ t('repo.deletePurgeDiskLabel') }}
        </n-checkbox>
      </template>
    </ConfirmGate>

    <!-- 重建仓库（本地 FS 全仓库开放）：三步确认门（关键词 REBUILD；清空全部快照，不可恢复） -->
    <ConfirmGate
      v-model:show="rebuildShow"
      :title="t('repo.rebuildTitle')"
      :keyword="'REBUILD'"
      :description="t('repo.rebuildConfirm')"
      :loading="rebuildSaving"
      @confirm="onRebuild(rebuildTargetId ?? currentRepoId)"
    >
      <template #preview>
        <n-text depth="3">{{ t('repo.rebuildPreview') }}</n-text>
      </template>
    </ConfirmGate>

    <!-- C4 手动 copy：把当前仓快照增量复制到目标仓（增量只传新快照，可中断续传）。
         宽度收口：n-modal preset=card 默认无宽度约束，会被内容撑成全屏宽瘦长条。 -->
    <n-modal
      v-model:show="copyShow"
      preset="card"
      :title="t('repo.copyBtn')"
      style="width: min(560px, calc(100vw - 32px))"
    >
      <n-space vertical :size="12">
        <n-text depth="3">{{ t('repo.copyDesc') }}</n-text>
        <n-form label-placement="left" label-width="120" size="small">
          <n-form-item :label="t('repo.copySource')">
            <n-input :value="copySourceLabel" readonly />
          </n-form-item>
          <n-form-item :label="t('repo.copyTarget')">
            <n-select
              v-model:value="copyTarget"
              :options="copyOptions"
              :placeholder="t('repo.copyTargetPlaceholder')"
            />
          </n-form-item>
        </n-form>
        <n-space justify="end">
          <n-button quaternary @click="copyShow = false">{{ t('common.cancel') }}</n-button>
          <n-button
            type="primary"
            :loading="copying"
            :disabled="!copyTarget"
            @click="onCopy"
          >
            {{ t('repo.copyRun') }}
          </n-button>
        </n-space>
        <template v-if="copyResult">
          <n-alert v-if="!copyResult.ok" type="error" :show-icon="false">
            {{ t('repo.copyFailed', { code: copyResult.exit_code, message: copyResult.message }) }}
          </n-alert>
          <n-alert v-else-if="copyResult.copied === 0" type="warning" :show-icon="false">
            {{ t('repo.copyNoNew') }}
          </n-alert>
          <n-alert v-else-if="copyResult.copied" type="success" :show-icon="false">
            {{
              t('repo.copyDoneN', {
                n: copyResult.copied,
                total: copyResult.target_total ?? copyResult.copied,
              })
            }}
          </n-alert>
          <n-alert v-else type="success" :show-icon="false">{{ t('repo.copyDone') }}</n-alert>
          <n-text v-if="copyResult.log_name" depth="3" class="repo__note">
            {{ t('repo.copyLogRetained', { name: copyResult.log_name }) }}
          </n-text>
        </template>
      </n-space>
    </n-modal>

    <!-- E44 设置仓库密码：NModal（与 C4 轮换共用 PasswordEntryModal 两段式输入）。
         生成 = 后端 secrets 生成并已写入口令文件后一次性回显（确认 = 我已备份，关闭）；
         自填 = 直接提交写入。API 与一次性明文语义不变。 -->
    <PasswordEntryModal
      v-model:show="passwordShow"
      :title="passwordForce ? t('repo.passwordResetTitle') : t('repo.passwordTitle')"
      :intro="passwordForce ? t('repo.passwordResetIntro') : t('repo.passwordIntro')"
      :generated-hint="t('repo.passwordGeneratedHint')"
      :custom-label="t('repo.passwordCustomLabel')"
      :custom-placeholder="t('repo.passwordCustomPlaceholder')"
      :generate-label="t('common.generatePassword')"
      :submit-label="t('repo.passwordUseCustom')"
      :confirm-label="t('repo.passwordSavedClose')"
      :generated="generatedPassword"
      :loading="passwordSaving"
      :min-length="1"
      :show-generated-cancel="false"
      :show-generate="!passwordForce"
      @generate="generatePassword"
      @submit-custom="onSubmitPassword"
      @confirm-generated="closePassword"
    />

    <!-- C4 密钥轮换：输入方式与 E44 同款（生成→复制 / 自填）。
         生成 = 前端本地强随机（轮换新密码后端不回显，S3）；确认 = restic key passwd，
         成功后旧密码作废、口令文件同步重写。未提交前可返回自填（showGeneratedCancel）。 -->
    <PasswordEntryModal
      v-model:show="rotateShow"
      :title="t('repo.rotateBtn')"
      :intro="t('repo.rotateDesc')"
      :generated-hint="t('repo.rotateGeneratedHint')"
      :custom-label="t('repo.rotateNewPw')"
      :custom-placeholder="t('repo.rotateNewPwPlaceholder')"
      :generate-label="t('common.generatePassword')"
      :submit-label="t('repo.rotateUseCustom')"
      :confirm-label="t('repo.rotateConfirmLabel')"
      :generated="rotateGenerated"
      :loading="rotating"
      :min-length="10"
      @generate="generateRotatePw"
      @submit-custom="onRotate"
      @confirm-generated="onRotate(rotateGenerated ?? '')"
    />

    <!-- 重建默认仓库结果：展示一次性新密码（与 E44 生成态同款展示 + 复制） -->
    <PasswordEntryModal
      v-model:show="rebuildResultShow"
      :title="t('repo.rebuildTitle')"
      :generated-hint="t('repo.rebuildGeneratedHint')"
      :generated="rebuildPassword"
      :loading="rebuildSaving"
      :show-generated-cancel="false"
      confirm-label="我已备份，关闭"
      @confirm-generated="closeRebuild"
    />
  </n-space>
</template>

<style scoped>
.repo__note {
  display: block;
  font-size: 12px;
}

.repo__test-init {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 8px;
}

.repo__cred {
  width: 100%;
}

.repo__picked {
  flex: 1 1 200px;
}

.repo__id-input {
  flex: 1 1 200px;
}

/* G6 维护分区动作行：左「标题 + 风险说明」，右动作按钮（对齐 Duplicati Maintenance 的
   「先解释再给动作」范式；窄屏纵向堆叠，保证按钮不被说明文字挤压变形）。 */
.repo__maint-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 0;
  border-top: 1px solid var(--n-divider-color, rgba(128, 128, 140, 0.16));
}

.repo__maint-row:first-of-type {
  border-top: none;
}

.repo__maint-text {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

@media (max-width: 640px) {
  .repo__maint-row {
    align-items: flex-start;
    flex-direction: column;
  }
}

/* A 方案：行内 init 进度条——在仓库行的动作区下方占满整行（抽屉日志是覆盖层不占布局）。 */
.repo__init-progress {
  width: 100%;
  margin-top: 8px;
}

/* 不可达仓库横幅（P1-3）：v-show 隐藏时不占位，故外边距也只在此处生效。 */
.repo__unreachable {
  margin-bottom: 12px;
}

/* 删除门「后果可视化」（删除保护方案 P1）：快照数 + 镜像引用清单。
   用 flex 列而非 n-space（同 UI 全量改造方案 §192 的结构行约定）。 */
.repo__del-facts {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 13px;
}

.repo__del-fact--warn {
  color: var(--n-text-color-warning, #d07a00);
}

.repo__del-names {
  margin-left: 4px;
  font-weight: 600;
}

.repo__del-hint {
  margin-top: 2px;
  font-weight: 400;
  opacity: 0.85;
}

/* ---------------------------------------------------------------- 目标位置列表（对齐 Duplicati） */

/* 仓库状态行内元素：容量文案（挨着锁标签）+ 最右「设置仓库密码」入口 */
.repo__capacity-inline {
  font-size: 13px;
}

/* 状态卡左右分栏：左（连接态/容量）←→ 右（密钥身份），横向并排、不用分隔线
   （用户 2026-10-23 指令：分隔线去掉）。
   两栏都按内容收缩（左栏不撑满），否则右栏会被推到卡片最右侧、中间一大片空白；
   垂直居中对齐，左栏单行时不会吊在右栏块顶部。
   右栏上限 50%：超长 host 名时不至于把左栏挤没；空间不足时 key 行自带 flex-wrap 换行。 */
.repo__status-split {
  display: flex;
  align-items: center;
  gap: 40px;
}

.repo__status-main {
  flex: 0 1 auto;
  min-width: 0;
}

.repo__status-keys {
  flex: 0 1 auto;
  max-width: 50%;
  min-width: 0;
}

/* 告警通栏：位于分栏下方，全卡宽度（不参与分栏，否则会被压成窄栏多行） */
.repo__status-alert {
  margin-top: 12px;
}

/* ≤640px：并排会挤，回落为上下堆叠（无分隔线，仅靠行间距分组） */
@media (max-width: 640px) {
  .repo__status-split {
    flex-direction: column;
    align-items: stretch; /* 纵向堆叠时 center 会变成「水平居中」，须复位 */
    gap: 12px;
  }

  .repo__status-keys {
    flex: none;
    width: 100%;
    max-width: none; /* 纵向堆叠时 50% 上限失效，须显式解除 */
  }
}

.repo__status-action {
  margin-left: auto;
}

/* 「新建仓库」操作卡 ↔ 仓库列表的分界线（用户 2026-10-24 指令）。
   取 --n-divider-color，与本页其它分隔线同源，暗色下自动跟随主题。 */
.repo__list-divider {
  height: 0;
  border-top: 1px solid var(--n-divider-color, rgba(128, 128, 140, 0.16));
}

/* 仓库行卡：[类型图标][名称+状态标签 / 地址·主机标识][类型][删除]（Duplicati destination-list-item） */
.repo__dest {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  padding: 12px 12px;
  border: 1px solid var(--n-border-color, #e0e0e6);
  border-radius: 5px;
  background: transparent;
  color: inherit;
  text-align: left;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}

.repo__dest:hover {
  border-color: var(--n-color-target, #2080f0);
}

/* role=button 行卡的键盘焦点环（div 无默认焦点样式） */
.repo__dest:focus-visible {
  outline: 2px solid var(--n-color-target, #2080f0);
  outline-offset: 1px;
}

/* 当前仓库：主色边框 + 同色淡底（选中态；点击空转，不再触发切换） */
.repo__dest--current,
.repo__dest--current:hover {
  border-color: var(--n-color-target, #2080f0);
  background: var(--n-color-target-transparent, rgba(32, 128, 240, 0.12));
  cursor: default;
}

.repo__dest-icon {
  flex: none;
}

.repo__dest-body {
  display: flex;
  flex-direction: column;
  gap: 4px;
  flex: 1 1 auto;
  min-width: 0;
}

.repo__dest-name {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  min-width: 0;
}

.repo__dest-id {
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.repo__dest-desc {
  font-size: 12px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 右侧类型文案：窄屏让位给名称/操作，隐藏（图标已表意） */
.repo__dest-type {
  flex: none;
  font-size: 12px;
}

.repo__id-chip {
  font-size: 12px;
}

/* 必填字段标签（对齐 Duplicati 字段标签的红色 * + tooltip「This field is mandatory」） */
.repo__req {
  margin-left: 2px;
  color: var(--n-error-color, #d03050);
  font-weight: 600;
  cursor: help;
}

/* 编辑卡体：flex 纵轴 12px 间距（替代 n-space，原因见上方注释） */
.repo__edit-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
}


/* 编辑态（对齐 Duplicati destinations 编辑态）：顶部返回行 + 底部动作栏 */
.repo__pick-row {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
}
.repo__pick-input {
  flex: 1;
  min-width: 0;
}

.repo__edit-title {
  display: flex;
  align-items: center;
  gap: 10px;
}

.repo__edit-header {
  display: flex;
}

.repo__form-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding-top: 12px;
  border-top: 1px solid var(--n-divider-color, rgba(128, 128, 140, 0.16));
}

@media (max-width: 640px) {
  .repo__dest-type {
    display: none;
  }
}
</style>
