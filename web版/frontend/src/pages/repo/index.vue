<script setup lang="ts">
import { useQueries, useQuery, useQueryClient } from '@tanstack/vue-query'
import {
  NAutoComplete,
  NAlert,
  NButton,
  NCard,
  NForm,
  NFormItem,
  NGrid,
  NGi,
  NIcon,
  NInput,
  NModal,
  NRadio,
  NRadioGroup,
  NSelect,
  NSpace,
  NTag,
  NText,
  NTooltip,
  useMessage,
} from 'naive-ui'
import { computed, h, nextTick, onUnmounted, reactive, ref, watch, type FunctionalComponent } from 'vue'
import { useI18n } from 'vue-i18n'
import { browseFs } from '@/api/fs'
import { getConfig, putConfig } from '@/api/config'
import { createJob } from '@/api/jobs'
// E25 缓存端点已在 api/settings.ts（GET /api/cache、POST /api/cache/clear）。
// 设置页缓存维护专区已并入本维护区（2026-10-19），本页是唯一 UI 入口，
// 复用同一对函数，避免第二套路径（/api/ops/cache 并不存在）。
import { clearCache, getCacheUsage } from '@/api/settings'
import {
  createRepo,
  deleteRepo,
  getRepo,
  getRepoStatus,
  listRepos,
  listRcloneRemotes,
  manualCopy,
  rotateRepoKey,
  setRepoPassword,
  suggestRepoId,
  testRepo,
  updateRepo,
} from '@/api/repo'
import { hasCode, isAppError } from '@/api/http'
import type {
  ConfigItem,
  RcloneRemotesOut,
  RepoOut,
  RepoPasswordRequest,
  RepoStatusResponse,
  RepoTestResponse,
  RepoUpdateRequest,
} from '@/api/types'
import ConfirmGate from '@/components/ConfirmGate.vue'
import JobProgress from '@/components/JobProgress.vue'
import PasswordEntryModal from '@/components/PasswordEntryModal.vue'
import PathBreadcrumb from '@/components/PathBreadcrumb.vue'
import StatusTag from '@/components/StatusTag.vue'
// 存储后端类型图标（零依赖内联 SVG）：仓库列表行卡 + 编辑态类型卡共用
import { backendIcon } from '@/components/icons/BackendIcons'
import VirtualTree, { type TreeItem, type TreePage } from '@/components/VirtualTree.vue'
import { useUiStore } from '@/stores/ui'
import { formatDateTime } from '@/utils/datetime'
// G4：强随机密码生成（纯前端，本地 crypto 源；轮换面板「生成」按钮用，不经网络）
import { generateStrongPassword } from '@/utils/passwordStrength'

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
const rowStatusQueries = useQueries({
  queries: () =>
    repos.value.map((r) => ({
      queryKey: ['repo', 'status', r.id],
      queryFn: () => getRepoStatus(r.id),
      staleTime: 30_000,
    })),
})

const rowStatusById = computed<Record<string, RepoStatusResponse | undefined>>(() => {
  const out: Record<string, RepoStatusResponse | undefined> = {}
  repos.value.forEach((r, i) => {
    out[r.id] = rowStatusQueries.value[i]?.data
  })
  return out
})

/** 需要初始化 = 已填地址 + 探测结论「未连接」；无地址（未配置）与已连接均不显示。 */
function needsInit(r: RepoOut): boolean {
  if (!r.url) {
    return false
  }
  const st = rowStatusById.value[r.id]
  return !!st && !st.connected
}

/** 行内初始化：先把该仓库设为当前（init 作业按当前仓库寻址），再开确认门。 */
function openInit(id: string): void {
  if (id !== ui.currentRepoId) {
    onSetCurrent(id)
  }
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
import { composeRepoUrl, isS3Endpoint, parseRepoUrl, parseS3Url, type RepoTypeKey } from '@/utils/repoUrl'

/** 类型下拉选项（用户裁决：本项目无后端类型列表端点，用下拉替代 Duplicati 类型选择器列表）。 */
const REPO_TYPES = computed<Array<{ key: RepoTypeKey; label: string }>>(() => [
  { key: 'local', label: t('repo.typeLocal') },
  { key: 's3', label: t('repo.typeS3') },
  { key: 'b2', label: t('repo.typeB2') },
  { key: 'sftp', label: t('repo.typeSftp') },
  { key: 'azure', label: t('repo.typeAzure') },
  { key: 'gcs', label: t('repo.typeGcs') },
  { key: 'rest', label: t('repo.typeRest') },
  { key: 'rclone', label: t('repo.typeRclone') },
])

/** 类型下拉选项（n-select options）。 */
const typeSelectOptions = computed(() => REPO_TYPES.value.map((x) => ({ label: x.label, value: x.key })))

/** 下拉项渲染：类型图标 + 名称（零依赖内联 SVG，与列表行卡同源）。 */
function renderTypeLabel(option: { label: string; value: string }) {
  return h(NSpace, { align: 'center', size: 8 }, () => [
    h(NIcon, { size: 18 }, () => h(backendIcon(option.value))),
    option.label,
  ])
}

/** 地址由 env 覆盖时整段只读（对齐 Duplicati readOnlyTest）；新建态无已存仓库，恒不锁。 */
const envLocked = computed(() => !creatingNew.value && repo.value?.url_source === 'env')

/** 必填字段标签（对齐 Duplicati 字段标签的红色 * + tooltip「This field is mandatory」）。 */
const ReqLabel: FunctionalComponent<{ label: string }> = (props) =>
  h('span', { class: 'repo__req-label' }, [
    props.label,
    h('span', { class: 'repo__req', title: t('repo.fieldMandatory') }, '*'),
  ])

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
      return repoForm.remote.trim() ? null : t('repo.fieldRemoteRequired')
    default:
      return t('repo.formTypeRequired')
  }
}

/** rclone 预设快捷卡（改版前弹窗的填入项，用户裁决恢复；URL 仍走字段组译——
 *  点卡只填「远端名」字段，路径留空由用户补）。 */
const RCLONE_PRESETS = computed(() => [
  { key: 'onedrive', label: t('repo.dest.rcloneOnedrive'), remote: 'onedrive', note: t('repo.dest.rcloneOnedriveNote') },
  { key: 'googledrive', label: t('repo.dest.rcloneGoogledrive'), remote: 'googledrive', note: t('repo.dest.rcloneGoogledriveNote') },
  { key: 's3compat', label: t('repo.dest.rcloneS3'), remote: 's3compat', note: t('repo.dest.rcloneS3Note') },
  { key: 'other', label: t('repo.dest.rcloneOther'), remote: 'remote', note: t('repo.dest.rcloneOtherNote') },
])

const rclonePresetKey = ref('')

function selectRclonePreset(key: string): void {
  const preset = RCLONE_PRESETS.value.find((p) => p.key === key)
  if (!preset) {
    return
  }
  rclonePresetKey.value = key
  repoForm.remote = preset.remote
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
  rclonePresetKey.value = ''
}

/** A8：向服务端要一个候选仓库标识（repo-<ULID>）预填；失败不阻断（用户可手填/留空）。 */
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
    const created = await createRepo({
      id: repoForm.id.trim() || undefined,
      name: repoForm.name.trim(),
      url: composeRepoUrl(repoForm).trim() || null,
      host_tag: repoForm.hostTag.trim() || null,
    })
    // 凭据随创建落盘：键 = cred.{created.id}.*（id 留空时用服务端生成的，输入按 suffix 暂存
    // 不绑 id，因此「先填凭据再换 id」也不丢）。repoFormMissing 已保证 type 已选。
    const credPatch: Record<string, string | null> = {}
    for (const field of CRED_FIELDS[repoForm.type as RepoTypeKey]) {
      const value = credInput[field.suffix]
      if (value) {
        credPatch[`cred.${created.id}.${field.suffix}`] = value
      }
    }
    if (Object.keys(credPatch).length > 0) {
      await putConfig(credPatch)
    }
    message.success(t('repo.created'))
    await reposQuery.refetch()
    onSetCurrent(created.id)
    return created.id
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
const deleting = ref(false)
const deleteShow = ref(false)
const deleteTarget = ref<string | null>(null)

function onDeleteClick(id: string): void {
  if (id === 'default') {
    message.warning(t('repo.deleteDefaultForbidden'))
    return
  }
  deleteTarget.value = id
  deleteShow.value = true
}

async function confirmDelete(): Promise<void> {
  const id = deleteTarget.value
  if (!id) {
    return
  }
  deleting.value = true
  try {
    await deleteRepo(id)
    message.success(t('repo.deleted'))
    if (ui.currentRepoId === id) {
      onSetCurrent('default')
    }
    await reposQuery.refetch()
  } catch (error) {
    if (!isAppError(error)) {
      throw error
    }
  } finally {
    deleting.value = false
    deleteTarget.value = null
    deleteShow.value = false
  }
}

function typeLabel(ty: string): string {
  const key = `repo.type${ty.charAt(0).toUpperCase()}${ty.slice(1)}`
  return t(key)
}

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

/** 打开弹窗即清一次性明文（自填草稿由组件在 show 翻转时自清）。 */
function openPassword(): void {
  generatedPassword.value = null
  passwordShow.value = true
}

function closePassword(): void {
  passwordShow.value = false
  generatedPassword.value = null
}

async function generatePassword(): Promise<void> {
  await submitPassword({})
}

/** 自填提交（PasswordEntryModal submit-custom 事件）。 */
function onSubmitPassword(password: string): void {
  void submitPassword({ password })
}

async function submitPassword(body: RepoPasswordRequest): Promise<void> {
  const id = currentRepoId.value
  if (!id) {
    return
  }
  passwordSaving.value = true
  try {
    const result = await setRepoPassword(id, body)
    // 只在生成场景回显；自填场景 password 恒为 null
    generatedPassword.value = result.generated ? result.password : null
    message.success(t('repo.passwordSetOk'))
    await Promise.all([reposQuery.refetch(), repoQuery.refetch(), statusQuery.refetch(), configQuery.refetch()])
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

interface CredField {
  suffix: string
  labelKey: string
  /** secret = 密码框；region = 常用值下拉建议（可手填）；text = 明文（ID/名称/路径等非敏感项）。
   *  曾有的 endpoint 类已退役：restic 0.19.1 的 s3 后端 ApplyEnvironment 只读
   *  AWS_DEFAULT_REGION，AWS_ENDPOINT_URL 全库零引用（minio-go v7.1.0 实证）——
   *  自定义 S3 服务器的正确配置位是地址区的「服务器地址」（进 URL 首段）。 */
  kind: 'text' | 'secret' | 'region'
}

/** 常用区域建议值（S3 URL 区域段与 AWS_DEFAULT_REGION 共用）。 */
const REGION_OPTIONS = [
  'cn-north-1',
  'cn-northwest-1',
  'ap-east-1',
  'ap-southeast-1',
  'us-east-1',
  'us-west-1',
  'us-west-2',
  'eu-west-1',
]

/** 常用 S3 Endpoint 建议值（MinIO 本机 / 阿里云 OSS / 腾讯云 COS / AWS 官方）。
 *  用于地址区「服务器地址」输入（endpointSuggestions 在其后拼接既有仓库提取值）。 */
const ENDPOINT_OPTIONS = [
  'http://127.0.0.1:9000',
  'https://oss-cn-hangzhou.aliyuncs.com',
  'https://oss-cn-beijing.aliyuncs.com',
  'https://oss-cn-shanghai.aliyuncs.com',
  'https://cos.ap-beijing.myqcloud.com',
  'https://cos.ap-shanghai.myqcloud.com',
  'https://s3.amazonaws.com',
]

/** 字段类型 → 建议值列表（null = 无建议，纯输入）。 */
function credOptions(kind: CredField['kind']): string[] | null {
  if (kind === 'region') {
    return REGION_OPTIONS
  }
  return null
}

/** 凭据键表（§4.3）：基础后缀；完整键 = cred.{repo_id}.{suffix}（R-023 命名空间化）。
 * A1 新增后端（restic 0.19.1 官方 env 逐一登记；sftp 无凭据字段——免密登录；
 * rclone 无凭据字段——conf 宿主机托管，Q17 只读档位）。 */
const CRED_FIELDS: Record<RepoTypeKey, CredField[]> = {
  local: [],
  s3: [
    { suffix: 'aws_access_key_id', labelKey: 'repo.credAwsAccessKeyId', kind: 'text' },
    { suffix: 'aws_secret_access_key', labelKey: 'repo.credAwsSecretAccessKey', kind: 'secret' },
    { suffix: 'aws_default_region', labelKey: 'repo.credAwsDefaultRegion', kind: 'region' },
  ],
  b2: [
    { suffix: 'b2_account_id', labelKey: 'repo.credB2AccountId', kind: 'text' },
    { suffix: 'b2_account_key', labelKey: 'repo.credB2AccountKey', kind: 'secret' },
  ],
  sftp: [],
  azure: [
    { suffix: 'azure_account_name', labelKey: 'repo.credAzureAccountName', kind: 'text' },
    { suffix: 'azure_account_key', labelKey: 'repo.credAzureAccountKey', kind: 'secret' },
  ],
  gcs: [
    { suffix: 'google_project_id', labelKey: 'repo.credGoogleProjectId', kind: 'text' },
    { suffix: 'google_application_credentials', labelKey: 'repo.credGoogleApplicationCredentials', kind: 'text' },
  ],
  rest: [
    { suffix: 'restic_rest_username', labelKey: 'repo.credRestUsername', kind: 'text' },
    { suffix: 'restic_rest_password', labelKey: 'repo.credRestPassword', kind: 'secret' },
  ],
  rclone: [],
}

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
const unlockShow = ref(false)
const unlocking = ref(false)
const unlockJobId = ref('')
const initShow = ref(false)
const initializing = ref(false)
const initJobId = ref('')
// B1 自愈：仓库修复（repair index / repair packs），危险操作 ConfirmGate 关键词 REPAIR
const repairShow = ref(false)
const repairing = ref(false)
const repairJobId = ref('')
const repairTarget = ref<'index' | 'packs'>('index')
// B3 自愈：找回丢失快照（restic recover），危险操作 ConfirmGate 关键词 RECOVER。
// 后端早已支持（constants.py JOB_TYPES + job_runner 埋 AUDIT_RECOVER_RUN），此前前端零入口——G1 补齐。
const recoverShow = ref(false)
const recovering = ref(false)
const recoverJobId = ref('')

// ---------------------------------------------------------------- C4 密钥轮换 + 手动 copy（搬家）
// 输入方式与 E44 同款（生成→复制 / 自填），与「设置仓库密码」共用 PasswordEntryModal。
// 区别只在生成实现：轮换新密码后端不回显（S3）→ 前端本地生成、确认后才随请求提交。
const rotateShow = ref(false)
const rotating = ref(false)
/** 生成态的一次性明文（前端本地生成）；null = 自填态。确认前仅存于内存。 */
const rotateGenerated = ref<string | null>(null)
const copyShow = ref(false)
const copying = ref(false)
const copyTarget = ref<string | null>(null)
const copyResult = ref<{ ok: boolean; exit_code: number; message: string } | null>(null)

function openRotate(): void {
  rotateGenerated.value = null
  rotateShow.value = true
}

/** 前端本地生成强随机密码（G4 passwordStrength，纯 crypto 源，不经网络、不落任何存储）。 */
function generateRotatePw(): void {
  rotateGenerated.value = generateStrongPassword()
}

async function onRotate(newPassword: string): Promise<void> {
  if (!currentRepoId.value || newPassword.length < 10) {
    return
  }
  rotating.value = true
  try {
    await rotateRepoKey({ repo_id: currentRepoId.value, new_password: newPassword })
    message.success(t('repo.rotateOk'))
    rotateShow.value = false
    rotateGenerated.value = null
  } catch {
    // AppError 已有全局提示
  } finally {
    rotating.value = false
  }
}

// E25 本地缓存：占用查询（服务端 30s 缓存，前端 5 分钟不重取）+ 清空（S6 危险操作）
const cacheQuery = useQuery({
  queryKey: ['ops', 'cache'],
  queryFn: getCacheUsage,
  staleTime: 300_000,
  refetchOnWindowFocus: false,
})
const cacheShow = ref(false)
const cacheClearing = ref(false)

/** copy 目标候选：其它仓库中已配地址与密码的（否则后端 422）。 */
const copyOptions = computed(() =>
  (reposQuery.data.value ?? [])
    .filter((repo) => repo.id !== currentRepoId.value)
    .map((repo) => ({
      label: `${repo.name} (${repo.id})`,
      value: repo.id,
      disabled: !(repo.url ?? '').trim() || repo.passfile_status !== 'configured',
    })),
)

async function onCopy(): Promise<void> {
  if (!currentRepoId.value || !copyTarget.value) {
    return
  }
  copying.value = true
  copyResult.value = null
  try {
    const resp = await manualCopy({ source_repo_id: currentRepoId.value, target_repo_id: copyTarget.value })
    copyResult.value = { ok: resp.ok, exit_code: resp.exit_code, message: resp.message }
    if (resp.ok) {
      message.success(t('repo.copyOk'))
    }
  } catch {
    // AppError 已有全局提示
  } finally {
    copying.value = false
  }
}

function openCopy(): void {
  copyTarget.value = null
  copyResult.value = null
  copyShow.value = true
}

// ---------------------------------------------------------------- 本地路径目录选择弹窗（E33；对齐 Duplicati FolderTree + Browse...）
const CONTAINER_ROOT = '/'
const browsePath = ref(CONTAINER_ROOT)
const mountChips = ref<string[]>([])
const browseShow = ref(false)
const browsePicked = ref('')

function fsLoader(path: string, cursor?: string): Promise<TreePage> {
  return browseFs(path, cursor).then((page) => {
    if (page.path === CONTAINER_ROOT) {
      mountChips.value = page.mounts
    }
    return {
      items: page.items.map((item) => ({
        key: item.path,
        name: item.name,
        type: item.type,
        path: item.path,
      })),
      nextCursor: page.next_cursor,
    }
  })
}

function onTreeSelect(item: TreeItem): void {
  browsePicked.value = item.path
  if (item.type === 'dir') {
    browsePath.value = item.path
  }
}

/** 打开目录选择弹窗：从当前已选路径（或容器根）起浏览。 */
function openBrowse(): void {
  browsePath.value = repoForm.path || CONTAINER_ROOT
  browsePicked.value = repoForm.path
  browseShow.value = true
}

function confirmBrowse(): void {
  if (browsePicked.value) {
    repoForm.path = browsePicked.value
  }
  browseShow.value = false
}

function jumpTo(path: string): void {
  browsePath.value = path
}

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
  const reason = (testResult.value?.reason ?? '').toLowerCase()
  return NOT_INITIALIZED_MARKERS.some((m) => reason.includes(m.toLowerCase()))
})

async function onUnlock(): Promise<void> {
  unlocking.value = true
  try {
    const created = await createJob('unlock')
    unlockJobId.value = created.id
    message.success(t('repo.unlockSubmitted'))
    void statusQuery.refetch()
  } catch (error) {
    if (!isAppError(error)) {
      throw error
    }
  } finally {
    unlocking.value = false
  }
}

async function onInit(): Promise<void> {
  initializing.value = true
  try {
    const created = await createJob('init')
    initJobId.value = created.id
    message.success(t('repo.initSubmitted'))
    void statusQuery.refetch()
  } catch (error) {
    if (!isAppError(error)) {
      throw error
    }
  } finally {
    initializing.value = false
  }
}

async function onRepair(): Promise<void> {
  repairing.value = true
  try {
    const created = await createJob('repair', { params: { repair_target: repairTarget.value } })
    repairJobId.value = created.id
    message.success(t('repo.repairSubmitted'))
    void statusQuery.refetch()
  } catch (error) {
    if (!isAppError(error)) {
      throw error
    }
  } finally {
    repairing.value = false
  }
}

/** B3 自愈：找回丢失快照（restic recover 无子命令、无额外参数），动作入审计 recover.run。 */
async function onRecover(): Promise<void> {
  recovering.value = true
  try {
    const created = await createJob('recover')
    recoverJobId.value = created.id
    message.success(t('repo.recoverSubmitted'))
    void statusQuery.refetch()
  } catch (error) {
    if (!isAppError(error)) {
      throw error
    }
  } finally {
    recovering.value = false
  }
}

/** E25 清空本地缓存：与设置页同口径——409 JOB_CONFLICT 单独提示（有任务在跑不能清），
 *  其余错误交给全局提示（clearCache 是 silent 请求，不会双重 toast）。 */
async function onClearCache(): Promise<void> {
  cacheClearing.value = true
  try {
    await clearCache()
    message.success(t('settings.cache.cleared'))
    cacheShow.value = false
    await cacheQuery.refetch()
  } catch (error) {
    if (hasCode(error, 'JOB_CONFLICT')) {
      message.warning(t('settings.cache.conflict'))
    } else if (!isAppError(error)) {
      throw error
    }
  } finally {
    cacheClearing.value = false
  }
}

// ---------------------------------------------------------------- 展示辅助
const repoStatus = computed(() => statusQuery.data.value ?? null)

/** 缓存占用一行文案（truncated 时值仍可用，标注为「部分统计」即可，后端不 5xx）。 */
const cacheUsageText = computed(() => {
  const usage = cacheQuery.data.value
  if (!usage) {
    return t('repo.cacheUnknown')
  }
  return t('repo.cacheUsage', { size: formatBytes(usage.size_bytes), n: usage.items_count })
})

/** 缓存目录一行文案（原设置页专区字段并入，沿用纯文本注释口径）；无数据时不渲染。 */
const cachePathLine = computed(() => {
  const usage = cacheQuery.data.value
  return usage ? t('repo.cachePath', { path: usage.path }) : ''
})

/** 测量时间一行文案；无数据时不渲染。 */
const cacheMeasuredLine = computed(() => {
  const usage = cacheQuery.data.value
  return usage ? t('repo.cacheMeasured', { time: formatDateTime(usage.measured_at) }) : ''
})

/** du 超时截断标注（后端回部分值，仍可用，仅提示不精确）。 */
const cacheTruncated = computed(() => cacheQuery.data.value?.truncated ?? false)

const capacityText = computed(() => {
  // capacity 可能为 null（后端缺省/代理改写/未初始化），?. 兜底——否则整页渲染抛错白屏
  const size = repoStatus.value?.capacity?.size
  return size ? formatBytes(size) : t('common.none')
})

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
      <n-space vertical :size="10">
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
          <!-- 密码文件缺失 = 任何仓库操作都注定失败（restic 拿不到密码）：行内最右给可操作入口 -->
          <n-button
            v-if="passfileMissing"
            size="tiny"
            type="primary"
            class="repo__status-action"
            @click="openPassword"
          >
            {{ t('repo.passwordSetBtn') }}
          </n-button>
        </n-space>
        <n-alert v-if="repoStatus?.last_error" type="error" :show-icon="false">
          {{ t('dashboard.repoLastError') }}：{{ repoStatus.last_error }}
        </n-alert>
        <n-alert v-if="passfileMissing" type="warning" :show-icon="false">
          {{ t('repo.passfileMissingAlert') }}
        </n-alert>
        <!-- 锁残留属状态信号（留在状态卡）；对应动作按钮已收进下方「仓库维护」分区 -->
        <n-alert v-if="repoStatus?.locked" type="warning" :show-icon="false">
          {{ t('repo.lockedNote') }}
        </n-alert>
      </n-space>
    </n-card>
    <!-- 仓库列表（R-023）：对齐 Duplicati「目标位置」模块（ngclient backup/destination）——
         行卡 = [类型图标][名称 + 状态标签][地址·主机标识][右侧操作]，整行点击 = 设为当前
         （Duplicati 选中目标位置语义；当前行高亮且空转）；删除走 ConfirmGate 三门，default 禁删。
         新增入口 = 卡右上角按钮（用户 2026-10-19 布局指令；早前的大操作卡形态已回退）。 -->
    <n-card :title="t('repo.listTitle')" size="small">
      <template #header-extra>
        <n-button size="small" type="primary" secondary @click="openCreate">{{ t('repo.newRepo') }}</n-button>
      </template>
      <n-space vertical :size="10">
        <!-- 仓库行卡：整行点击 = 设为当前；当前行高亮且点击空转（已是选中态）。
             交互形态照搬 Duplicati destination.component.html：div + role=button + tabindex=0
             + keydown.enter/space（避免 button 嵌 button 的非法 HTML——行内还有删除按钮）。 -->
        <div
          v-for="r in repos"
          :key="r.id"
          role="button"
          tabindex="0"
          class="repo__dest"
          :class="{ 'repo__dest--current': r.id === ui.currentRepoId }"
          :aria-current="r.id === ui.currentRepoId ? 'true' : undefined"
          @click="onSelectRepo(r.id)"
          @keydown.enter="onSelectRepo(r.id)"
          @keydown.space.prevent="onSelectRepo(r.id)"
        >
          <n-icon :size="20" class="repo__dest-icon"><component :is="backendIcon(r.type)" /></n-icon>
          <span class="repo__dest-body">
            <span class="repo__dest-name">
              <n-text strong>{{ r.name }}</n-text>
              <!-- 仓库 ID（改版前的表格有 ID 列，行卡化后曾在列表态丢失；ID 是凭据键
                   cred.{id}.* 与 RESTIC_REPOSITORY 寻址的锚点，必须可见可核对） -->
              <n-text depth="3" class="repo__dest-id">#{{ r.id }}</n-text>
              <StatusTag v-if="r.id === 'default'" size="tiny" kind="warning" :label="t('repo.defaultTag')" />
              <StatusTag v-if="r.id === ui.currentRepoId" size="tiny" kind="success" :label="t('repo.currentTag')" />
              <StatusTag
                size="tiny"
                :kind="r.passfile_status === 'configured' ? 'success' : 'warning'"
                :label="r.passfile_status === 'configured' ? t('repo.passfileConfigured') : t('repo.passfileMissingShort')"
              />
            </span>
            <n-text depth="3" class="repo__dest-desc">
              {{ r.url || '—' }}<template v-if="r.host_tag"> · {{ r.host_tag }}</template>
            </n-text>
          </span>
          <n-text depth="3" class="repo__dest-type">{{ typeLabel(r.type) }}</n-text>
          <!-- 行内动作（对齐 Duplicati manage-actions）：管理（整页切编辑态）+ 初始化 + 删除；均须 stop 冒泡。
               初始化只在「已填地址且探测未连接」时出现（已初始化的仓库不再显示），
               密码文件缺失时禁用并给原因——与维护区原门禁同口径。 -->
          <n-button size="tiny" secondary @click.stop="openManage(r.id)">
            {{ t('repo.manage') }}
          </n-button>
          <n-tooltip v-if="needsInit(r) && r.passfile_status !== 'configured'" trigger="hover">
            <template #trigger>
              <n-button size="tiny" secondary type="warning" disabled>{{ t('repo.initBtn') }}</n-button>
            </template>
            {{ t('repo.initBlockedByPassfile') }}
          </n-tooltip>
          <n-button
            v-else-if="needsInit(r)"
            size="tiny"
            secondary
            type="warning"
            :loading="initializing && r.id === ui.currentRepoId"
            @click.stop="openInit(r.id)"
          >
            {{ t('repo.initBtn') }}
          </n-button>
          <n-button
            size="tiny"
            secondary
            type="error"
            :disabled="r.id === 'default'"
            @click.stop="onDeleteClick(r.id)"
          >
            {{ t('repo.delete') }}
          </n-button>
        </div>
      </n-space>
    </n-card>
    <!--
      仓库维护（G6：对齐 Duplicati `backup/database` 的 Maintenance 分区范式——
      「先解释风险，再给动作」：分区标题 → 风险说明段 → 动作行）。
      原散落在状态卡内的 unlock / repair 与 C4 轮换、copy 统一收进本分区（init 已上移到仓库行内），
      状态卡回归纯展示；并补上 B3 recover（G1：后端早已支持、前端此前零入口）与
      E25 清空缓存（原设置页专区已并入本分区，本页是唯一 UI 入口，复用 api/settings 的同一对函数）。
      危险动作一律走 ConfirmGate 关键词确认。
    -->
    <n-card :title="t('repo.maintenanceTitle')" size="small">
      <n-space vertical :size="12">
        <n-text depth="3" class="repo__note">{{ t('repo.maintenanceIntro') }}</n-text>

        <!-- 动作行 1：解锁（仅锁残留时有意义） -->
        <div class="repo__maint-row">
          <div class="repo__maint-text">
            <n-text strong>{{ t('repo.unlockBtn') }}</n-text>
            <n-text depth="3" class="repo__note">{{ t('repo.unlockHint') }}</n-text>
          </div>
          <n-button
            size="small"
            type="warning"
            :disabled="!repoStatus?.locked"
            :loading="unlocking"
            @click="unlockShow = true"
          >
            {{ t('repo.unlockBtn') }}
          </n-button>
        </div>

        <!-- 动作行 2：修复索引/卷（B1，仅已连接可操作） -->
        <div class="repo__maint-row">
          <div class="repo__maint-text">
            <n-text strong>{{ t('repo.repairBtn') }}</n-text>
            <n-text depth="3" class="repo__note">{{ t('repo.repairHint') }}</n-text>
          </div>
          <n-button
            size="small"
            type="error"
            :disabled="!repoStatus?.connected"
            :loading="repairing"
            @click="repairShow = true"
          >
            {{ t('repo.repairBtn') }}
          </n-button>
        </div>

        <!-- 动作行 3：找回丢失快照（B3，G1 新增入口） -->
        <div class="repo__maint-row">
          <div class="repo__maint-text">
            <n-text strong>{{ t('repo.recoverBtn') }}</n-text>
            <n-text depth="3" class="repo__note">{{ t('repo.recoverHint') }}</n-text>
          </div>
          <n-button
            size="small"
            type="error"
            :disabled="!repoStatus?.connected"
            :loading="recovering"
            @click="recoverShow = true"
          >
            {{ t('repo.recoverBtn') }}
          </n-button>
        </div>

        <!-- 初始化不在此处：已按用户要求移到每个仓库行内（未初始化才显示），见仓库列表行卡。 -->

        <!-- 动作行 5/6：C4 密钥轮换 + 手动 copy（搬迁自状态卡，逻辑不变） -->
        <div class="repo__maint-row">
          <div class="repo__maint-text">
            <n-text strong>{{ t('repo.rotateBtn') }}</n-text>
            <n-text depth="3" class="repo__note">{{ t('repo.rotateHint') }}</n-text>
          </div>
          <n-button
            size="small"
            secondary
            :disabled="passfileMissing"
            @click="openRotate"
          >
            {{ t('repo.rotateBtn') }}
          </n-button>
        </div>

        <div class="repo__maint-row">
          <div class="repo__maint-text">
            <n-text strong>{{ t('repo.copyBtn') }}</n-text>
            <n-text depth="3" class="repo__note">{{ t('repo.copyHint') }}</n-text>
          </div>
          <n-button size="small" secondary @click="openCopy">
            {{ t('repo.copyBtn') }}
          </n-button>
        </div>

        <!-- 动作行 7：E25 清空本地缓存（后端 S6 危险操作，有活跃任务时 409）。
             原设置页「缓存维护」专区已并入本行：路径/大小/条目数/测量时间/截断
             全部沿用本行纯文本注释口径展示（不再用设置页的 descriptions 表格）。 -->
        <div class="repo__maint-row">
          <div class="repo__maint-text">
            <n-text strong>{{ t('repo.cacheBtn') }}</n-text>
            <n-text depth="3" class="repo__note">{{ t('repo.cacheHint') }}</n-text>
            <n-text depth="3" class="repo__note">{{ cacheUsageText }}</n-text>
            <n-text v-if="cachePathLine" depth="3" class="repo__note">{{ cachePathLine }}</n-text>
            <n-text v-if="cacheMeasuredLine" depth="3" class="repo__note">{{ cacheMeasuredLine }}</n-text>
            <n-text v-if="cacheTruncated" depth="3" class="repo__note">{{ t('settings.cache.truncated') }}</n-text>
          </div>
          <n-button size="small" secondary :loading="cacheClearing" @click="cacheShow = true">
            {{ t('repo.cacheBtn') }}
          </n-button>
        </div>

        <template v-if="initJobId">
          <JobProgress :job-id="initJobId" :title="t('repo.initJobTitle')" />
        </template>
        <template v-if="unlockJobId">
          <JobProgress :job-id="unlockJobId" :title="t('repo.unlockJobTitle')" />
        </template>
        <template v-if="repairJobId">
          <JobProgress :job-id="repairJobId" :title="t('repo.repairJobTitle')" />
        </template>
        <template v-if="recoverJobId">
          <JobProgress :job-id="recoverJobId" :title="t('repo.recoverJobTitle')" />
        </template>
      </n-space>
    </n-card>
    </template>

    <!-- 编辑态（对齐 Duplicati destinations 编辑态）：← 返回 + 单配置卡；
         选中前配置表单不渲染（源码：selectedDestination === null 分支只有列表）。 -->
    <template v-else>
      <div class="repo__edit-header">
        <n-button quaternary size="small" @click="cancelEdit">{{ t('repo.backToList') }}</n-button>
      </div>
      <n-card :title="creatingNew ? t('repo.newRepoTitle') : t('repo.editRepoTitle')" size="small">
        <!-- 对齐 Duplicati 单项配置卡头：标明当前配置的目标位置（id 即列表行卡的选中项） -->
        <template #header-extra>
          <n-text v-if="!creatingNew" depth="3" class="repo__id-chip">
            {{ t('repo.colId') }}：#{{ currentRepoId }}
          </n-text>
        </template>
        <!-- 编辑卡体用普通 flex 容器而非 n-space：naive Space 对「v-if 注释 + HTML 注释」相邻子节点
             存在 vnode 克隆缺陷（页脚会被挂载两次，且每轮列表/编辑切换泄漏一份，2026-10-19 实证），
             回归护栏见 __tests__/page-render-smoke.spec.ts 的循环用例。 -->
        <div class="repo__edit-body">
          <n-form label-placement="top" size="small">
            <!-- 存储类型下拉（用户裁决：本项目无后端类型列表端点，用下拉替代 Duplicati 类型选择器；
                 选中后才渲染地址/凭据区——对齐 Duplicati editBaseUrl=null 时卡内只出类型选择器。
                 :value 传 null 而非 ''——naive 把空串当「已选中空值」不显示占位符（实测）。 -->
            <n-form-item :label="t('repo.formType')">
              <n-select
                :value="repoForm.type || null"
                :options="typeSelectOptions"
                :placeholder="t('repo.formTypePlaceholder')"
                :render-label="renderTypeLabel"
                @update:value="onTypeChange"
              />
              <n-text depth="3" class="repo__note">
                {{ creatingNew ? t('repo.dest.hint') : t('repo.formTypeChangeHint') }}
              </n-text>
            </n-form-item>

            <!-- 地址区：选中类型后按类型渲染（Duplicati customFields 逐类型字段集 ↔ restic URL 段） -->
            <template v-if="repoForm.type">
              <n-form-item :label="t('repo.formName')">
                <n-input v-model:value="repoForm.name" :placeholder="t('repo.nameRequired')" />
              </n-form-item>

              <!-- env 覆盖：地址整段只读（对齐 Duplicati readOnlyTest） -->
              <n-alert v-if="envLocked" type="warning" :show-icon="false">{{ t('repo.urlFromEnv') }}</n-alert>

              <!-- local：文件路径 + 浏览…（FolderTree 弹窗，对齐 Duplicati Browse... 交互） -->
              <n-form-item v-if="repoForm.type === 'local'">
                <template #label><ReqLabel :label="t('repo.fieldPath')" /></template>
                <n-space :size="8" align="center" class="repo__cred">
                  <n-input
                    v-model:value="repoForm.path"
                    :placeholder="t('repo.dest.localPlaceholder')"
                    :disabled="envLocked"
                  />
                  <n-button size="small" secondary :disabled="envLocked" @click="openBrowse">
                    {{ t('repo.browseBtn') }}
                  </n-button>
                </n-space>
              </n-form-item>

              <!-- s3：服务器地址（Endpoint）+ Bucket + 文件夹路径。
                   URL 组译 = restic 0.19.1 原生斜杠方言 s3:<endpoint>/<bucket>[/<path>]（ParseConfig
                   源码核验：endpoint/bucket 以 / 分隔；旧 s3:region:bucket:/path 冒号方言会被
                   minio-go 域名校验拒绝，已退役——区域只留凭据里的 AWS 默认区域，管签名）。 -->
              <n-grid v-else-if="repoForm.type === 's3'" cols="1 m:2" :x-gap="12" responsive="screen">
                <n-gi>
                  <n-form-item>
                    <template #label><ReqLabel :label="t('repo.fieldBucket')" /></template>
                    <!-- get-show 恒真：naive-ui AutoComplete 默认仅「有输入值」才展开
                         （mergedShowOptions = !!value），聚焦空框不弹；常用值要一点就出 -->
                    <n-auto-complete
                      v-model:value="repoForm.bucket"
                      :options="bucketSuggestions"
                      :get-show="() => true"
                      placeholder="mybucket"
                      :disabled="envLocked"
                    />
                  </n-form-item>
                </n-gi>
                <n-gi>
                  <n-form-item>
                    <template #label><ReqLabel :label="t('repo.fieldEndpoint')" /></template>
                    <!-- 建议值 = 常用云厂商端点（MinIO/OSS/COS/AWS）+ 既有 s3 仓库提取值 -->
                    <n-auto-complete
                      v-model:value="repoForm.endpoint"
                      :options="endpointSuggestions"
                      :get-show="() => true"
                      :placeholder="t('repo.fieldEndpointPlaceholder')"
                      :disabled="envLocked"
                    />
                  </n-form-item>
                </n-gi>
                <n-gi>
                  <n-form-item :label="t('repo.fieldFolderPath')">
                    <n-input
                      v-model:value="repoForm.path"
                      :placeholder="t('repo.fieldFolderPathPlaceholder')"
                      :disabled="envLocked"
                    />
                  </n-form-item>
                </n-gi>
              </n-grid>

              <!-- b2 / gcs / azure：Bucket（或容器）+ 文件夹路径（Duplicati Bucketname + Path 双字段） -->
              <n-grid
                v-else-if="repoForm.type === 'b2' || repoForm.type === 'gcs' || repoForm.type === 'azure'"
                cols="1 m:2"
                :x-gap="12"
                responsive="screen"
              >
                <n-gi>
                  <n-form-item>
                    <template #label>
                      <ReqLabel :label="repoForm.type === 'azure' ? t('repo.fieldContainer') : t('repo.fieldBucket')" />
                    </template>
                    <!-- get-show 恒真：naive-ui AutoComplete 默认仅「有输入值」才展开
                         （mergedShowOptions = !!value），聚焦空框不弹；常用值要一点就出 -->
                    <n-auto-complete
                      v-model:value="repoForm.bucket"
                      :options="bucketSuggestions"
                      :get-show="() => true"
                      :placeholder="repoForm.type === 'azure' ? 'mycontainer' : 'mybucket'"
                      :disabled="envLocked"
                    />
                  </n-form-item>
                </n-gi>
                <n-gi>
                  <n-form-item :label="t('repo.fieldFolderPath')">
                    <n-input
                      v-model:value="repoForm.path"
                      :placeholder="t('repo.fieldFolderPathPlaceholder')"
                      :disabled="envLocked"
                    />
                  </n-form-item>
                </n-gi>
              </n-grid>

              <!-- sftp：服务器 + 用户名 + 文件夹路径（Duplicati ssh 条目 server/user/path；restic URL 无端口段） -->
              <n-grid v-else-if="repoForm.type === 'sftp'" cols="1 m:2" :x-gap="12" responsive="screen">
                <n-gi>
                  <n-form-item>
                    <template #label><ReqLabel :label="t('repo.fieldServer')" /></template>
                    <n-input
                      v-model:value="repoForm.server"
                      :placeholder="t('repo.fieldServerPlaceholder')"
                      :disabled="envLocked"
                    />
                  </n-form-item>
                </n-gi>
                <n-gi>
                  <n-form-item>
                    <template #label><ReqLabel :label="t('repo.fieldUser')" /></template>
                    <n-input
                      v-model:value="repoForm.user"
                      :placeholder="t('repo.fieldUserPlaceholder')"
                      :disabled="envLocked"
                    />
                  </n-form-item>
                </n-gi>
                <n-gi>
                  <n-form-item :label="t('repo.fieldFolderPath')">
                    <n-input
                      v-model:value="repoForm.path"
                      :placeholder="t('repo.fieldFolderPathPlaceholder')"
                      :disabled="envLocked"
                    />
                  </n-form-item>
                </n-gi>
              </n-grid>
              <n-text v-if="repoForm.type === 'sftp'" depth="3" class="repo__note">{{ t('repo.urlHintSftp') }}</n-text>
              <n-text v-if="repoForm.type === 's3'" depth="3" class="repo__note">{{ t('repo.urlHintS3') }}</n-text>

              <!-- rest：REST 服务器完整地址 -->
              <n-form-item v-else-if="repoForm.type === 'rest'">
                <template #label><ReqLabel :label="t('repo.fieldRestUrl')" /></template>
                <n-input
                  v-model:value="repoForm.url"
                  :placeholder="t('repo.dest.restPlaceholder')"
                  :disabled="envLocked"
                />
              </n-form-item>

              <!-- rclone：远端名 + 远端路径（无凭据字段；conf 宿主机托管，Q17 只读探测） -->
              <n-grid v-else-if="repoForm.type === 'rclone'" cols="1 m:2" :x-gap="12" responsive="screen">
                <n-gi>
                  <n-form-item>
                    <template #label><ReqLabel :label="t('repo.fieldRemote')" /></template>
                    <n-input
                      v-model:value="repoForm.remote"
                      :placeholder="t('repo.fieldRemotePlaceholder')"
                      :disabled="envLocked"
                    />
                  </n-form-item>
                </n-gi>
                <n-gi>
                  <n-form-item :label="t('repo.fieldRemotePath')">
                    <n-input
                      v-model:value="repoForm.path"
                      :placeholder="t('repo.fieldRemotePathPlaceholder')"
                      :disabled="envLocked"
                    />
                  </n-form-item>
                </n-gi>
                <n-gi>
                  <n-alert
                    v-if="rcloneRemotes"
                    :type="rcloneRemotes.conf_present ? 'success' : 'warning'"
                    :show-icon="false"
                  >
                    {{
                      rcloneRemotes.conf_present
                        ? t('repo.rcloneConfOk', { list: rcloneRemotes.remotes.join('、') || '—' })
                        : t('repo.rcloneConfMissing')
                    }}
                  </n-alert>
                  <n-alert v-else-if="rcloneRemotesQuery.error.value" type="error" :show-icon="false">
                    {{ t('repo.rcloneProbeFailed', { reason: String(rcloneRemotesQuery.error.value) }) }}
                  </n-alert>
                </n-gi>
              </n-grid>
              <n-text v-if="repoForm.type === 'rclone'" depth="3" class="repo__note">{{ t('repo.urlHintRclone') }}</n-text>

              <!-- rclone 预设快捷卡（改版前弹窗填入项的恢复；点卡只填「远端名」，路径用户自补）。
                   样式复用原类型卡的 dest-grid/dest-card（类名沿用原弹窗类型卡样式，仍在使用）。 -->
              <n-form-item v-if="repoForm.type === 'rclone'" :label="t('repo.dest.rclonePresetLabel')">
                <div class="repo__dest-grid">
                  <button
                    v-for="preset in RCLONE_PRESETS"
                    :key="preset.key"
                    type="button"
                    class="repo__dest-card"
                    :class="{ 'repo__dest-card--active': rclonePresetKey === preset.key }"
                    :title="preset.note"
                    @click="selectRclonePreset(preset.key)"
                  >
                    {{ preset.label }}
                  </button>
                </div>
              </n-form-item>

              <!-- 已存地址解析失败：原文回退编辑 -->
              <n-form-item v-if="repoForm.raw" :label="t('repo.url')">
                <n-input v-model:value="repoForm.raw" :disabled="envLocked" />
                <n-text depth="3" class="repo__note">{{ t('repo.rawUrlFallback') }}</n-text>
              </n-form-item>

              <!-- 主机标识（restic host tag，全类型通用） -->
              <n-form-item :label="t('repo.hostTag')">
                <n-input v-model:value="repoForm.hostTag" :placeholder="t('repo.hostTagPlaceholder')" />
              </n-form-item>

              <!-- 仓库 ID（仅新建态；留空服务端自动生成 repo-<ULID>） -->
              <n-form-item v-if="creatingNew" :label="t('repo.colId')">
                <n-space vertical :size="6" class="repo__cred">
                  <n-space :size="8" align="center">
                    <n-input v-model:value="repoForm.id" :placeholder="t('repo.idAutoHint')" class="repo__id-input" />
                    <n-button size="small" secondary :loading="generatingId" @click="regenerateRepoId">
                      {{ t('repo.regenerate') }}
                    </n-button>
                  </n-space>
                  <n-text depth="3" class="repo__note">{{ t('repo.idAutoNote') }}</n-text>
                </n-space>
              </n-form-item>

              <!-- 凭据（脱敏：只回 has_value + source；env 覆盖的键置灰并给原因）。
                   按 formType（下拉所选类型）渲染；新建态为纯输入（不带状态标签——
                   itemOf 读的是当前仓库命名空间，新建时无意义），凭据随创建以
                   cred.{created_id}.* 落盘（见 runCreate），无需二次进编辑态补配。 -->
              <template v-if="credFields.length">
                <n-text strong>{{ t('repo.credentials') }}</n-text>
                <n-form-item v-for="field in credFields" :key="field.suffix" :label="t(field.labelKey)">
                  <n-space vertical :size="4" class="repo__cred">
                    <!-- 编辑态才显示已配置/未配置与 env 覆盖（itemOf 读当前仓库命名空间，新建态无意义） -->
                    <n-space v-if="!creatingNew" align="center" :size="6">
                      <StatusTag
                        size="tiny"
                        :kind="itemOf(field.suffix)?.has_value ? 'success' : 'default'"
                        :label="itemOf(field.suffix)?.has_value ? t('repo.credSet') : t('repo.credUnset')"
                      />
                      <StatusTag
                        v-if="itemOf(field.suffix)?.source === 'env'"
                        size="tiny"
                        kind="warning"
                        :label="t('repo.credSourceEnv')"
                      />
                    </n-space>
                    <n-tooltip v-if="!creatingNew && itemOf(field.suffix)?.source === 'env'" trigger="hover">
                      <template #trigger>
                        <n-input
                          :value="''"
                          :placeholder="t('repo.credEnvNote')"
                          type="password"
                          show-password-on="click"
                          disabled
                        />
                      </template>
                      {{ t('repo.credEnvNote') }}
                    </n-tooltip>
                    <!-- 常用值建议（可下拉选择也可手动输入）：AWS 默认区域。
                         get-show 恒真：聚焦即展开（默认要输入才展开） -->
                    <n-auto-complete
                      v-else-if="credOptions(field.kind)"
                      v-model:value="credInput[field.suffix]"
                      :options="credOptions(field.kind)!"
                      :get-show="() => true"
                      :placeholder="t('repo.credNewPlaceholder')"
                    />
                    <n-input
                      v-else
                      v-model:value="credInput[field.suffix]"
                      :placeholder="t('repo.credNewPlaceholder')"
                      :type="field.kind === 'secret' ? 'password' : 'text'"
                      show-password-on="click"
                    />
                  </n-space>
                </n-form-item>
              </template>
            </template>
          </n-form>

          <!-- 内联连接测试区（对齐 Duplicati app-test-url 状态机；源码中 editBaseUrl 为空时
               整块不渲染）：
               未测 → 提示条 + 立即测试；testing → 进行中文案；结果 → 分类 alert + 内联动作按钮。
               URL/凭据变更后结论由写路径作废回 idle（E1），与 Duplicati 的 URL 变更重置 testSignal 同构。
               仅编辑态：新建流无已存仓库，后端 testRepo 读的是已落库配置，测不了未创建的仓库。 -->
          <template v-if="repoForm.type && !creatingNew">
            <div class="repo__test-block">
              <n-alert v-if="testing" type="info" :show-icon="false">{{ t('repo.testing') }}</n-alert>
              <n-alert v-else-if="repoTestState === 'success'" type="success" :show-icon="false">
                {{ t('repo.testOk', { n: testResult?.latency_ms ?? 0 }) }}
              </n-alert>
              <n-alert v-else-if="repoTestState === 'error'" type="error" :show-icon="false">
                <n-space align="center" justify="space-between">
                  <span>{{ testResult?.reason || t('repo.testFail') }}</span>
                  <n-button size="tiny" type="primary" :loading="testing" @click="onTest">
                    {{ t('repo.testAgain') }}
                  </n-button>
                </n-space>
                <!-- 地址可达但仓库尚未初始化（新建后首次测试的典型结论）：就地给初始化入口 -->
                <div v-if="testNotInitialized" class="repo__test-init">
                  <n-text depth="3" class="repo__note">{{ t('repo.testNotInitializedHint') }}</n-text>
                  <n-tooltip v-if="passfileMissing" trigger="hover">
                    <template #trigger>
                      <n-button size="tiny" type="warning" disabled>{{ t('repo.initBtn') }}</n-button>
                    </template>
                    {{ t('repo.initBlockedByPassfile') }}
                  </n-tooltip>
                  <n-button
                    v-else
                    size="tiny"
                    type="warning"
                    :loading="initializing"
                    @click="openInit(currentRepoId)"
                  >
                    {{ t('repo.initBtn') }}
                  </n-button>
                </div>
              </n-alert>
              <n-alert v-else type="info" :show-icon="false">
                <n-space align="center" justify="space-between">
                  <span>{{ t('repo.testIdleHint') }}</span>
                  <n-button size="tiny" type="primary" :loading="testing" @click="onTest">
                    {{ t('repo.testNow') }}
                  </n-button>
                </n-space>
              </n-alert>
            </div>

            <!-- E1：改过配置但未复验 → 明确告知并挡住不可逆动作（init） -->
            <n-alert v-if="verifyRequired" type="warning" :show-icon="false">
              {{ t('repo.verifyRequired') }}
            </n-alert>

            <n-text depth="3">{{ t('repo.passfileStatus') }}：
              {{ repo?.passfile_status === 'configured'
                ? t('repo.passfileConfigured')
                : t('repo.passfileMissing') }}
            </n-text>
          </template>

          <!-- 底部动作（对齐 Duplicati form-footer 智能主按钮：
               新建 = 创建仓库；有变更且未验证 = 测试并保存（warn，失败转 error）；已验证/无变更 = 保存） -->
          <div class="repo__form-footer">
            <n-button quaternary @click="cancelEdit">{{ t('common.cancel') }}</n-button>
            <!-- 新建/编辑共用一枚主按钮（语义见 saveButton）：新建态 = 创建并测试 -->
            <n-button :type="saveButton.type" :loading="saving || testing || creating" @click="saveButton.run">
              {{ saveButton.label }}
            </n-button>
          </div>
        </div>
      </n-card>
    </template>

    <!-- 本地路径目录选择（E33；对齐 Duplicati FolderTree + Browse... 弹窗交互） -->
    <n-modal
      v-model:show="browseShow"
      preset="card"
      :title="t('repo.browseTitle')"
      style="width: min(640px, calc(100vw - 32px))"
    >
      <n-space vertical :size="10">
        <n-space :size="6" class="repo__mounts">
          <n-tag size="small" round @click="jumpTo(CONTAINER_ROOT)">
            {{ t('repo.mountRoot') }}
          </n-tag>
          <n-tag
            v-for="mount in mountChips"
            :key="mount"
            size="small"
            round
            type="info"
            class="repo__mount"
            @click="jumpTo(mount)"
          >
            {{ mount }}
          </n-tag>
        </n-space>
        <PathBreadcrumb :path="browsePath" @select="browsePath = $event" />
        <VirtualTree
          :root-path="browsePath"
          :load-children="fsLoader"
          :height="360"
          select-dir
          :selected-path="browsePicked"
          @select="onTreeSelect"
        />
        <n-space justify="end">
          <n-button quaternary @click="browseShow = false">{{ t('common.cancel') }}</n-button>
          <n-button type="primary" :disabled="!browsePicked" @click="confirmBrowse">
            {{ t('repo.browseConfirm') }}
          </n-button>
        </n-space>
      </n-space>
    </n-modal>

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
      v-model:show="initShow"
      :title="t('repo.initBtn')"
      :keyword="'INIT'"
      :description="t('repo.initDesc')"
      :loading="initializing"
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

    <!-- 删除仓库：三步确认门 -->
    <ConfirmGate
      v-model:show="deleteShow"
      :title="t('repo.delete')"
      :keyword="'DELETE'"
      :description="t('repo.deleteConfirm')"
      :loading="deleting"
      @confirm="confirmDelete"
    >
      <template #preview>
        <n-text depth="3">{{ t('repo.deleteConfirm') }}</n-text>
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
      <n-space vertical :size="10">
        <n-text depth="3">{{ t('repo.copyDesc') }}</n-text>
        <n-form label-placement="left" label-width="120" size="small">
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
          <n-alert v-if="copyResult.ok" type="success" :show-icon="false">{{ t('repo.copyDone') }}</n-alert>
          <n-alert v-else type="error" :show-icon="false">
            {{ t('repo.copyFailed', { code: copyResult.exit_code, message: copyResult.message }) }}
          </n-alert>
        </template>
      </n-space>
    </n-modal>

    <!-- E44 设置仓库密码：NModal（与 C4 轮换共用 PasswordEntryModal 两段式输入）。
         生成 = 后端 secrets 生成并已写入口令文件后一次性回显（确认 = 我已备份，关闭）；
         自填 = 直接提交写入。API 与一次性明文语义不变。 -->
    <PasswordEntryModal
      v-model:show="passwordShow"
      :title="t('repo.passwordTitle')"
      :intro="t('repo.passwordIntro')"
      :generated-hint="t('repo.passwordGeneratedHint')"
      :custom-label="t('repo.passwordCustomLabel')"
      :custom-placeholder="t('repo.passwordCustomPlaceholder')"
      :generate-label="t('repo.passwordGenerate')"
      :submit-label="t('repo.passwordUseCustom')"
      :confirm-label="t('repo.passwordSavedClose')"
      :generated="generatedPassword"
      :loading="passwordSaving"
      :min-length="1"
      :show-generated-cancel="false"
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
      :generate-label="t('repo.passwordGenerate')"
      :submit-label="t('repo.rotateUseCustom')"
      :confirm-label="t('repo.rotateConfirmLabel')"
      :generated="rotateGenerated"
      :loading="rotating"
      :min-length="10"
      @generate="generateRotatePw"
      @submit-custom="onRotate"
      @confirm-generated="onRotate(rotateGenerated ?? '')"
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
  gap: 10px;
  margin-top: 8px;
}

.repo__cred {
  width: 100%;
}

.repo__mounts {
  flex-wrap: wrap;
  gap: 6px;
}

.repo__mount {
  cursor: pointer;
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
  padding: 10px 0;
  border-top: 1px solid var(--n-divider-color, rgba(128, 128, 140, 0.16));
}

.repo__maint-row:first-of-type {
  border-top: none;
}

.repo__maint-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

@media (max-width: 640px) {
  .repo__maint-row {
    align-items: flex-start;
    flex-direction: column;
  }
}

/* A5 目的地类型卡片网格（对齐 Duplicati 选择器：图标 + 名称竖排） */
.repo__dest-search {
  margin-bottom: 8px;
}

.repo__dest-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(96px, 1fr));
  gap: 8px;
  width: 100%;
}

.repo__dest-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 10px 6px;
  border: 1px solid var(--n-border-color, #e0e0e6);
  border-radius: 5px;
  background: transparent;
  color: inherit;
  font-size: 13px;
  line-height: 1.2;
  text-align: center;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}

.repo__dest-card:hover {
  border-color: var(--n-color-target, #2080f0);
}

.repo__dest-card--active {
  border-color: var(--n-color-target, #2080f0);
  background: var(--n-color-target-transparent, rgba(32, 128, 240, 0.12));
  font-weight: 600;
}

/* ---------------------------------------------------------------- 目标位置列表（对齐 Duplicati） */

/* 仓库状态行内元素：容量文案（挨着锁标签）+ 最右「设置仓库密码」入口 */
.repo__capacity-inline {
  font-size: 13px;
}

.repo__status-action {
  margin-left: auto;
}

/* 仓库行卡：[类型图标][名称+状态标签 / 地址·主机标识][类型][删除]（Duplicati destination-list-item） */
.repo__dest {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  padding: 10px 12px;
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
  gap: 2px;
  flex: 1 1 auto;
  min-width: 0;
}

.repo__dest-name {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
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
.repo__edit-header {
  display: flex;
}

.repo__form-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding-top: 12px;
  border-top: 1px solid var(--n-divider-color, rgba(128, 128, 140, 0.16));
}

@media (max-width: 640px) {
  .repo__dest-type {
    display: none;
  }
}
</style>
