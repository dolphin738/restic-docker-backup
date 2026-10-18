<script setup lang="ts">
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import {
  NAlert,
  NButton,
  NCard,
  NDataTable,
  NForm,
  NFormItem,
  NGrid,
  NGi,
  NInput,
  NModal,
  NRadio,
  NRadioGroup,
  NSelect,
  NSpace,
  NStatistic,
  NTabPane,
  NTabs,
  NTag,
  NText,
  NTooltip,
  useMessage,
  type DataTableColumns,
} from 'naive-ui'
import { computed, h, onUnmounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { browseFs } from '@/api/fs'
import { getConfig, putConfig } from '@/api/config'
import { createJob } from '@/api/jobs'
// E25 缓存端点已在 api/settings.ts（GET /api/cache、POST /api/cache/clear），
// 设置页有专区；维护区复用同一对函数，避免第二套路径（/api/ops/cache 并不存在）。
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
  RepoTestResponse,
  RepoUpdateRequest,
} from '@/api/types'
import ConfirmGate from '@/components/ConfirmGate.vue'
import JobProgress from '@/components/JobProgress.vue'
import PathBreadcrumb from '@/components/PathBreadcrumb.vue'
import StatusTag from '@/components/StatusTag.vue'
import VirtualTree, { type TreeItem, type TreePage } from '@/components/VirtualTree.vue'
import { useUiStore } from '@/stores/ui'

const { t } = useI18n()
const ui = useUiStore()
const queryClient = useQueryClient()

const message = useMessage()
ui.setMessageApi(message)
onUnmounted(() => ui.setMessageApi(null))

// ---------------------------------------------------------------- 仓库列表（R-023）
const reposQuery = useQuery({ queryKey: ['repos'], queryFn: listRepos })

function onSetCurrent(id: string): void {
  ui.setCurrentRepoId(id)
  // 切换仓库：所有仓库域查询（快照/任务/状态/仪表盘）带新 X-Repo-Id 重取
  void queryClient.invalidateQueries()
}

// ---------------------------------------------------------------- 新建仓库（R-023 / A8 / A5）
const createShow = ref(false)
const creating = ref(false)
const generatingId = ref(false)
const createForm = reactive({ id: '', name: '', url: '', hostTag: '', destType: 'local', rclonePreset: '' })

// A5 目的地类型卡片网格（纯 UI 选择辅助；类型仍由后端从 url 派生，零后端改动）。
// 点选仅驱动 URL 占位/格式提示与按类型说明；rclone 预设卡预填 rclone:<remote>:<path> 模板。
const DEST_TYPES = computed(() => [
  { key: 'local', label: t('repo.dest.local'), prefix: '', placeholder: t('repo.dest.localPlaceholder'), note: t('repo.dest.localNote') },
  { key: 's3', label: t('repo.dest.s3'), prefix: 's3:', placeholder: t('repo.dest.s3Placeholder'), note: t('repo.dest.s3Note') },
  { key: 'b2', label: t('repo.dest.b2'), prefix: 'b2:', placeholder: t('repo.dest.b2Placeholder'), note: t('repo.dest.b2Note') },
  { key: 'sftp', label: t('repo.dest.sftp'), prefix: 'sftp:', placeholder: t('repo.dest.sftpPlaceholder'), note: t('repo.dest.sftpNote') },
  { key: 'azure', label: t('repo.dest.azure'), prefix: 'azure:', placeholder: t('repo.dest.azurePlaceholder'), note: t('repo.dest.azureNote') },
  { key: 'gcs', label: t('repo.dest.gcs'), prefix: 'gs:', placeholder: t('repo.dest.gcsPlaceholder'), note: t('repo.dest.gcsNote') },
  { key: 'rest', label: t('repo.dest.rest'), prefix: 'rest:', placeholder: t('repo.dest.restPlaceholder'), note: t('repo.dest.restNote') },
  { key: 'rclone', label: t('repo.dest.rclone'), prefix: 'rclone:', placeholder: t('repo.dest.rclonePlaceholder'), note: t('repo.dest.rcloneNote') },
])

// rclone 预设快捷卡（A5：OneDrive/Google Drive/S3 兼容/其它，均为 rclone 后端，视觉等价 Duplicati、实际可用）
const RCLONE_PRESETS = computed(() => [
  { key: 'onedrive', label: t('repo.dest.rcloneOnedrive'), url: 'rclone:onedrive:', note: t('repo.dest.rcloneOnedriveNote') },
  { key: 'googledrive', label: t('repo.dest.rcloneGoogledrive'), url: 'rclone:googledrive:', note: t('repo.dest.rcloneGoogledriveNote') },
  { key: 's3compat', label: t('repo.dest.rcloneS3'), url: 'rclone:s3compat:', note: t('repo.dest.rcloneS3Note') },
  { key: 'other', label: t('repo.dest.rcloneOther'), url: 'rclone:remote:', note: t('repo.dest.rcloneOtherNote') },
])

const currentDest = computed(() => DEST_TYPES.value.find((d) => d.key === createForm.destType) ?? DEST_TYPES.value[0])

function selectDestType(key: string): void {
  createForm.destType = key
  if (key !== 'rclone') {
    createForm.rclonePreset = ''
    const def = DEST_TYPES.value.find((d) => d.key === key)
    // 仅在 url 为空时预填 scheme 前缀，避免覆盖用户已输入内容
    if (def?.prefix && !createForm.url.trim()) {
      createForm.url = def.prefix
    }
  }
}

function selectRclonePreset(presetKey: string): void {
  const preset = RCLONE_PRESETS.value.find((p) => p.key === presetKey)
  if (!preset) {
    return
  }
  createForm.rclonePreset = presetKey
  createForm.url = preset.url
}

/** A8：向服务端要一个候选仓库标识（repo-<ULID>）预填；失败不阻断（用户可手填/留空）。 */
async function regenerateRepoId(): Promise<void> {
  generatingId.value = true
  try {
    const res = await suggestRepoId()
    createForm.id = res.id
  } catch (error) {
    if (!isAppError(error)) {
      throw error
    }
  } finally {
    generatingId.value = false
  }
}

function openCreate(): void {
  createForm.id = ''
  createForm.name = ''
  createForm.url = ''
  createForm.hostTag = ''
  createForm.destType = 'local'
  createForm.rclonePreset = ''
  createShow.value = true
  void regenerateRepoId()
}

async function onCreate(): Promise<void> {
  if (!createForm.name.trim()) {
    message.warning(t('repo.nameRequired'))
    return
  }
  creating.value = true
  try {
    const created = await createRepo({
      id: createForm.id.trim() || undefined,
      name: createForm.name.trim(),
      url: createForm.url.trim() || null,
      host_tag: createForm.hostTag.trim() || null,
    })
    message.success(t('repo.created'))
    createShow.value = false
    await reposQuery.refetch()
    onSetCurrent(created.id)
  } catch (error) {
    // AppError（如 id 重复 → 409 / 422）已由全局提示展示
    if (!isAppError(error)) {
      throw error
    }
  } finally {
    creating.value = false
  }
}

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
const passwordShow = ref(false)
const passwordSaving = ref(false)
const passwordInput = ref('')
/** 生成场景的一次性明文：仅在展开面板存活期展示，收起即丢弃（不落库、不落日志）。 */
const generatedPassword = ref<string | null>(null)

const passfileMissing = computed(() => (repo.value?.passfile_status ?? 'missing') !== 'configured')

/** E4：行内展开的开关（再点一次按钮 = 收起）。展开/收起都清掉一次性明文与自填草稿。 */
function openPassword(): void {
  passwordShow.value = !passwordShow.value
  passwordInput.value = ''
  generatedPassword.value = null
}

function closePassword(): void {
  passwordShow.value = false
  passwordInput.value = ''
  generatedPassword.value = null
}

async function generatePassword(): Promise<void> {
  await submitPassword({})
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
    passwordInput.value = ''
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

async function copyGenerated(): Promise<void> {
  if (!generatedPassword.value) {
    return
  }
  try {
    await navigator.clipboard.writeText(generatedPassword.value)
    message.success(t('repo.passwordCopied'))
  } catch {
    message.warning(t('repo.passwordCopyFailed'))
  }
}

const columns = computed<DataTableColumns<RepoOut>>(() => [
  {
    title: t('repo.colId'),
    key: 'id',
    render: (row) =>
      h(NSpace, { align: 'center', size: 6 }, () => [
        row.id,
        row.id === 'default'
          ? h(StatusTag, { kind: 'warning', label: t('repo.defaultTag'), size: 'tiny' })
          : null,
        row.id === ui.currentRepoId
          ? h(StatusTag, { kind: 'success', label: t('repo.currentTag'), size: 'tiny' })
          : null,
      ]),
    },
  { title: t('repo.colName'), key: 'name' },
  { title: t('repo.colType'), key: 'type', render: (row) => typeLabel(row.type) },
  { title: t('repo.colUrl'), key: 'url', render: (row) => row.url ?? '—' },
  { title: t('repo.colHostTag'), key: 'host_tag', render: (row) => row.host_tag ?? '—' },
  {
    title: t('repo.colPassfile'),
    key: 'passfile_status',
    render: (row) =>
      h(StatusTag, {
        kind: row.passfile_status === 'configured' ? 'success' : 'warning',
        label:
          row.passfile_status === 'configured'
            ? t('repo.passfileConfigured')
            : t('repo.passfileMissingShort'),
        size: 'tiny',
      }),
  },
  {
    title: '',
    key: 'actions',
    width: 170,
    render: (row) =>
      h(NSpace, { size: 6 }, () => [
        h(
          NButton,
          {
            size: 'tiny',
            secondary: true,
            type: row.id === ui.currentRepoId ? 'primary' : 'default',
            disabled: row.id === ui.currentRepoId,
            onClick: () => onSetCurrent(row.id),
          },
          () => t('repo.setCurrent'),
        ),
        h(
          NButton,
          {
            size: 'tiny',
            secondary: true,
            type: 'error',
            disabled: row.id === 'default',
            onClick: () => onDeleteClick(row.id),
          },
          () => t('repo.delete'),
        ),
      ]),
  },
])

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
const repoType = computed<RepoTypeKey>(() => {
  const ty = repo.value?.type
  return ty === 's3' || ty === 'b2' || ty === 'sftp' || ty === 'azure' || ty === 'gcs' || ty === 'rest' || ty === 'rclone'
    ? ty
    : 'local'
})

// A1 rclone 只读探测（Q17）：仅 rclone 类型仓库按需拉取（spawn 子进程，60s 内不重取）。
// 定义须晚于 repoType（enabled 依赖它）——vue-query 求值 enabled 是即时的。
const rcloneRemotesQuery = useQuery({
  queryKey: ['rclone', 'remotes'],
  queryFn: listRcloneRemotes,
  enabled: computed(() => repoType.value === 'rclone'),
  staleTime: 60_000,
})
const rcloneRemotes = computed<RcloneRemotesOut | null>(() => rcloneRemotesQuery.data.value ?? null)

const form = reactive({ url: '', hostTag: '' })
const initial = reactive({ url: '', hostTag: '' })
watch(
  () => repo.value,
  (r) => {
    if (!r) {
      return
    }
    form.url = r.url ?? ''
    form.hostTag = r.host_tag ?? ''
    initial.url = form.url
    initial.hostTag = form.hostTag
  },
  { immediate: true },
)

// 切换仓库时清空凭据输入与浏览态，避免残留；连接结论同样作废（仓库已换）
watch(currentRepoId, () => {
  clearCredInput()
  urlMode.value = 'manual'
  repoTestState.value = 'idle'
  testResult.value = null
  verifyRequired.value = false
})

// ---------------------------------------------------------------- 凭据（按仓库命名空间）
const configQuery = useQuery({ queryKey: ['config'], queryFn: getConfig })

type RepoTypeKey = 'local' | 's3' | 'b2' | 'sftp' | 'azure' | 'gcs' | 'rest' | 'rclone'
interface CredField {
  suffix: string
  labelKey: string
}

/** 凭据键表（§4.3）：基础后缀；完整键 = cred.{repo_id}.{suffix}（R-023 命名空间化）。
 * A1 新增后端（restic 0.19.1 官方 env 逐一登记；sftp 无凭据字段——免密登录；
 * rclone 无凭据字段——conf 宿主机托管，Q17 只读档位）。 */
const CRED_FIELDS: Record<RepoTypeKey, CredField[]> = {
  local: [],
  s3: [
    { suffix: 'aws_access_key_id', labelKey: 'repo.credAwsAccessKeyId' },
    { suffix: 'aws_secret_access_key', labelKey: 'repo.credAwsSecretAccessKey' },
    { suffix: 'aws_default_region', labelKey: 'repo.credAwsDefaultRegion' },
    { suffix: 'aws_endpoint_url', labelKey: 'repo.credAwsEndpointUrl' },
  ],
  b2: [
    { suffix: 'b2_account_id', labelKey: 'repo.credB2AccountId' },
    { suffix: 'b2_account_key', labelKey: 'repo.credB2AccountKey' },
  ],
  sftp: [],
  azure: [
    { suffix: 'azure_account_name', labelKey: 'repo.credAzureAccountName' },
    { suffix: 'azure_account_key', labelKey: 'repo.credAzureAccountKey' },
  ],
  gcs: [
    { suffix: 'google_project_id', labelKey: 'repo.credGoogleProjectId' },
    { suffix: 'google_application_credentials', labelKey: 'repo.credGoogleApplicationCredentials' },
  ],
  rest: [
    { suffix: 'restic_rest_username', labelKey: 'repo.credRestUsername' },
    { suffix: 'restic_rest_password', labelKey: 'repo.credRestPassword' },
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

const credFields = computed<CredField[]>(() => CRED_FIELDS[repoType.value])

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
const rotateShow = ref(false)
const rotating = ref(false)
const rotatePw = ref('')
const rotatePwConfirm = ref('')
const copyShow = ref(false)
const copying = ref(false)
const copyTarget = ref<string | null>(null)
const copyResult = ref<{ ok: boolean; exit_code: number; message: string } | null>(null)

const rotateReady = computed(
  () =>
    rotatePw.value.length >= 10 &&
    rotatePw.value === rotatePwConfirm.value,
)

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

async function onRotate(): Promise<void> {
  if (!currentRepoId.value || !rotateReady.value) {
    return
  }
  rotating.value = true
  try {
    await rotateRepoKey({ repo_id: currentRepoId.value, new_password: rotatePw.value })
    message.success(t('repo.rotateOk'))
    rotateShow.value = false
    rotatePw.value = ''
    rotatePwConfirm.value = ''
  } catch {
    // AppError 已有全局提示
  } finally {
    rotating.value = false
  }
}

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

// ---------------------------------------------------------------- 本地路径浏览器（E33）
const CONTAINER_ROOT = '/'
const browsePath = ref(CONTAINER_ROOT)
const mountChips = ref<string[]>([])
const urlMode = ref<'browse' | 'manual'>('manual')

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
  form.url = item.path
  if (item.type === 'dir') {
    browsePath.value = item.path
  }
}

function useCurrentDir(): void {
  form.url = browsePath.value
}

function jumpTo(path: string): void {
  browsePath.value = path
}

// ---------------------------------------------------------------- 保存 / 测试 / 解锁 / 初始化
async function onSave(): Promise<void> {
  const repoPatch: RepoUpdateRequest = {}
  if (form.url !== initial.url) {
    repoPatch.url = form.url || null
  }
  if (form.hostTag !== initial.hostTag) {
    repoPatch.host_tag = form.hostTag || null
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
  } finally {
    testing.value = false
  }
}

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

const capacityText = computed(() => {
  const size = repoStatus.value?.capacity.size
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
    <!-- 仓库列表（R-023） -->
    <n-card :title="t('repo.listTitle')" size="small">
      <template #header-extra>
        <n-button size="small" type="primary" secondary @click="openCreate">{{ t('repo.newRepo') }}</n-button>
      </template>
      <n-data-table
        size="small"
        :scroll-x="760"
        :columns="columns"
        :data="reposQuery.data.value ?? []"
        :loading="reposQuery.isFetching.value"
        :row-key="(row: RepoOut) => row.id"
      />
    </n-card>

    <!-- 当前仓库配置（R-023） -->
    <!-- 响应式断点只能用命名档（s/m/l/xl/xxl）：naive 的 responsive="screen" 查询的是断点布尔集，
         数字形式（如 "1100:2"）永不命中 → 永远单列（P4 实测修正，原写法静默失效）。 -->
    <n-grid cols="1 l:2" :x-gap="16" :y-gap="16" responsive="screen">
      <n-gi>
        <n-card :title="t('repo.currentConfigTitle')" size="small">
          <n-space vertical :size="12">
            <n-form label-placement="top" size="small">
              <n-form-item :label="t('repo.type')">
                <n-space vertical :size="4" class="repo__cred">
                  <n-space align="center" :size="6">
                    <!-- P3c 收口：类型描述 / env 来源提示（size 沿用原值 tiny/small） -->
                    <StatusTag size="small" :kind="repoType === 'local' ? 'default' : 'info'" :label="typeLabel(repoType)" />
                    <StatusTag v-if="repo?.url_source === 'env'" size="tiny" kind="warning" :label="t('repo.credSourceEnv')" />
                  </n-space>
                  <n-text depth="3">{{ t('repo.typeReadonlyNote') }}</n-text>
                </n-space>
              </n-form-item>
              <n-form-item :label="t('repo.url')">
                <n-space vertical :size="6" class="repo__cred">
                  <n-tabs
                    v-if="repoType === 'local'"
                    v-model:value="urlMode"
                    type="segment"
                    size="small"
                  >
                    <n-tab-pane name="browse" :tab="t('repo.urlBrowseTab')" />
                    <n-tab-pane name="manual" :tab="t('repo.urlManualTab')" />
                  </n-tabs>

                  <template v-if="repoType === 'local' && urlMode === 'browse'">
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
                      :height="280"
                      select-dir
                      :selected-path="form.url"
                      @select="onTreeSelect"
                    />
                    <n-space align="center" :size="8">
                      <n-button size="small" secondary @click="useCurrentDir">
                        {{ t('repo.useThisDir', { path: browsePath }) }}
                      </n-button>
                      <n-input v-model:value="form.url" size="small" class="repo__picked" />
                    </n-space>
                    <n-text depth="3" class="repo__note">{{ t('repo.browseHint') }}</n-text>
                  </template>

                  <n-input
                    v-else
                    v-model:value="form.url"
                    :placeholder="t('repo.urlPlaceholder')"
                  />
                  <!-- 分类型地址提示（A1 新后端）：sftp 免密登录 / rclone 远端托管 -->
                  <n-text v-if="repoType === 'sftp'" depth="3" class="repo__note">
                    {{ t('repo.urlHintSftp') }}
                  </n-text>
                  <n-text v-else-if="repoType === 'rclone'" depth="3" class="repo__note">
                    {{ t('repo.urlHintRclone') }}
                  </n-text>
                  <!-- rclone conf 只读状态（Q17：UI 不生成、不编辑、不展示凭据） -->
                  <n-alert
                    v-if="repoType === 'rclone' && rcloneRemotes"
                    :type="rcloneRemotes.conf_present ? 'success' : 'warning'"
                    :show-icon="false"
                  >
                    {{
                      rcloneRemotes.conf_present
                        ? t('repo.rcloneConfOk', { list: rcloneRemotes.remotes.join('、') || '—' })
                        : t('repo.rcloneConfMissing')
                    }}
                  </n-alert>
                  <n-alert
                    v-else-if="repoType === 'rclone' && rcloneRemotesQuery.error.value"
                    type="error"
                    :show-icon="false"
                  >
                    {{ t('repo.rcloneProbeFailed', { reason: String(rcloneRemotesQuery.error.value) }) }}
                  </n-alert>
                </n-space>
              </n-form-item>
              <n-form-item :label="t('repo.hostTag')">
                <n-input v-model:value="form.hostTag" :placeholder="t('repo.hostTagPlaceholder')" />
              </n-form-item>

              <!-- 凭据（脱敏：只回 has_value + source；env 覆盖的键置灰并给原因） -->
              <template v-if="credFields.length">
                <n-text strong>{{ t('repo.credentials') }}</n-text>
                <n-form-item
                  v-for="field in credFields"
                  :key="field.suffix"
                  :label="t(field.labelKey)"
                >
                  <n-space vertical :size="4" class="repo__cred">
                    <n-space align="center" :size="6">
                      <!-- P3c 收口：凭据已设/未设 + env 覆盖提示（tiny 档，密集表单） -->
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
                    <n-tooltip v-if="itemOf(field.suffix)?.source === 'env'" trigger="hover">
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
                    <n-input
                      v-else
                      v-model:value="credInput[field.suffix]"
                      :placeholder="t('repo.credNewPlaceholder')"
                      type="password"
                      show-password-on="click"
                    />
                  </n-space>
                </n-form-item>
              </template>
            </n-form>

            <n-space align="center">
              <n-button type="primary" :loading="saving" @click="onSave">{{ t('repo.save') }}</n-button>
              <n-button secondary :loading="testing" @click="onTest">{{ t('repo.testBtn') }}</n-button>
              <!-- E1 验证态一眼可见：未验证/已通过/失败（替代原先「只有点过测试才有反馈」） -->
              <StatusTag
                :kind="repoTestState === 'success' ? 'success' : repoTestState === 'error' ? 'error' : 'warning'"
                :label="repoTestState === 'success' ? t('repo.testVerified') : repoTestState === 'error' ? t('repo.testFail') : t('repo.testPending')"
                size="tiny"
              />
            </n-space>

            <n-alert v-if="testResult?.ok" type="success" :show-icon="false">
              {{ t('repo.testOk', { n: testResult.latency_ms }) }}
            </n-alert>
            <n-alert v-else-if="testResult && !testResult.ok" type="error" :show-icon="false">
              {{ testResult.reason || t('repo.testFail') }}
            </n-alert>
            <!-- E1：改过配置但未复验 → 明确告知并挡住不可逆动作（init） -->
            <n-alert v-if="verifyRequired" type="warning" :show-icon="false">
              {{ t('repo.verifyRequired') }}
            </n-alert>

            <n-text depth="3">{{ t('repo.passfileStatus') }}：
              {{ repo?.passfile_status === 'configured'
                ? t('repo.passfileConfigured')
                : t('repo.passfileMissing') }}
            </n-text>
          </n-space>
        </n-card>
      </n-gi>

      <!-- 仓库状态 -->
      <n-gi>
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
            </n-space>
            <n-space :size="24">
              <n-statistic :label="t('dashboard.repoCapacity')" :value="capacityText" />
            </n-space>
            <n-alert v-if="repoStatus?.last_error" type="error" :show-icon="false">
              {{ t('dashboard.repoLastError') }}：{{ repoStatus.last_error }}
            </n-alert>
            <!--
              密码文件缺失 = 任何仓库操作都注定失败（restic 拿不到密码）。
              与其让用户点了 init 才看到 exit=1，不如在这里直接挡住并给可操作入口。
            -->
            <n-alert v-if="passfileMissing" type="warning" :show-icon="false">
              {{ t('repo.passfileMissingAlert') }}
            </n-alert>
            <n-button v-if="passfileMissing" size="small" type="primary" @click="openPassword">
              {{ t('repo.passwordSetBtn') }}
            </n-button>
            <!--
              E4：设置仓库密码为**行内展开面板**（原 NModal → 卡内展开，API 不变）。
              对齐 Duplicati 向导 Step1 的行内形态——密码缺失本就发生在读当前状态卡的语境里，
              弹窗会打断「看到问题 → 当场处理」的动线；行内展开保留同样的两段式流程
              （生成→复制 / 自填），一次性明文仍只在面板存活期可见，关闭即丢弃。
            -->
            <div
              v-if="passwordShow"
              class="repo__password-panel"
              role="region"
              :aria-label="t('repo.passwordTitle')"
            >
              <n-text strong>{{ t('repo.passwordTitle') }}</n-text>
              <template v-if="generatedPassword">
                <n-alert type="success" :show-icon="false">{{ t('repo.passwordGeneratedHint') }}</n-alert>
                <n-input :value="generatedPassword" readonly class="repo__password-out" />
                <n-space justify="end" class="repo__password-actions">
                  <n-button size="small" secondary @click="copyGenerated">
                    {{ t('repo.passwordCopy') }}
                  </n-button>
                  <n-button size="small" type="primary" @click="closePassword">
                    {{ t('repo.passwordSavedClose') }}
                  </n-button>
                </n-space>
              </template>
              <template v-else>
                <n-alert type="warning" :show-icon="false">{{ t('repo.passwordIntro') }}</n-alert>
                <n-form label-placement="top" size="small" class="repo__password-form">
                  <n-form-item :label="t('repo.passwordCustomLabel')">
                    <n-input
                      v-model:value="passwordInput"
                      type="password"
                      show-password-on="click"
                      :placeholder="t('repo.passwordCustomPlaceholder')"
                    />
                  </n-form-item>
                </n-form>
                <n-space justify="end" class="repo__password-actions">
                  <n-button size="small" secondary @click="closePassword">
                    {{ t('common.cancel') }}
                  </n-button>
                  <n-button
                    size="small"
                    :disabled="!passwordInput.trim()"
                    :loading="passwordSaving"
                    @click="submitPassword({ password: passwordInput })"
                  >
                    {{ t('repo.passwordUseCustom') }}
                  </n-button>
                  <n-button size="small" type="primary" :loading="passwordSaving" @click="generatePassword">
                    {{ t('repo.passwordGenerate') }}
                  </n-button>
                </n-space>
              </template>
            </div>
            <!-- 锁残留属状态信号（留在状态卡）；对应动作按钮已收进下方「仓库维护」分区 -->
            <n-alert v-if="repoStatus?.locked" type="warning" :show-icon="false">
              {{ t('repo.lockedNote') }}
            </n-alert>
          </n-space>
        </n-card>
      </n-gi>
    </n-grid>

    <!--
      仓库维护（G6：对齐 Duplicati `backup/database` 的 Maintenance 分区范式——
      「先解释风险，再给动作」：分区标题 → 风险说明段 → 动作行）。
      原散落在状态卡内的 init / unlock / repair 与 C4 轮换、copy 统一收进本分区，
      状态卡回归纯展示；并补上 B3 recover（G1：后端早已支持、前端此前零入口）与
      E25 清空缓存（设置页已有专区的第二入口，复用 api/settings 的同一对函数）。
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

        <!-- 动作行 4：初始化（仅未连接时可操作；密码文件缺失时禁用并给原因） -->
        <div class="repo__maint-row">
          <div class="repo__maint-text">
            <n-text strong>{{ t('repo.initBtn') }}</n-text>
            <n-text depth="3" class="repo__note">{{ t('repo.initHint') }}</n-text>
          </div>
          <n-tooltip v-if="passfileMissing" trigger="hover">
            <template #trigger>
              <n-button size="small" type="warning" disabled>{{ t('repo.initBtn') }}</n-button>
            </template>
            {{ t('repo.initBlockedByPassfile') }}
          </n-tooltip>
          <n-tooltip v-else-if="verifyRequired" trigger="hover">
            <template #trigger>
              <n-button size="small" type="warning" disabled>{{ t('repo.initBtn') }}</n-button>
            </template>
            {{ t('repo.initBlockedByUnverified') }}
          </n-tooltip>
          <n-button
            v-else
            size="small"
            type="warning"
            :disabled="!!repoStatus?.connected"
            :loading="initializing"
            @click="initShow = true"
          >
            {{ t('repo.initBtn') }}
          </n-button>
        </div>

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
            @click="rotateShow = true"
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

        <!-- 动作行 7：E25 清空本地缓存（后端 S6 危险操作，有活跃任务时 409） -->
        <div class="repo__maint-row">
          <div class="repo__maint-text">
            <n-text strong>{{ t('repo.cacheBtn') }}</n-text>
            <n-text depth="3" class="repo__note">{{ t('repo.cacheHint') }}</n-text>
            <n-text depth="3" class="repo__note">{{ cacheUsageText }}</n-text>
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

    <!-- 新建仓库 -->
    <n-modal
      v-model:show="createShow"
      :title="t('repo.newRepoTitle')"
      preset="card"
      style="width: 480px"
    >
      <n-form label-placement="top" size="small">
        <!-- A5：目的地类型卡片网格 -->
        <n-form-item :label="t('repo.dest.label')">
          <div class="repo__dest-grid">
            <button
              v-for="dest in DEST_TYPES"
              :key="dest.key"
              type="button"
              class="repo__dest-card"
              :class="{ 'repo__dest-card--active': createForm.destType === dest.key }"
              @click="selectDestType(dest.key)"
            >
              {{ dest.label }}
            </button>
          </div>
          <n-text depth="3" class="repo__note">{{ t('repo.dest.hint') }}</n-text>
        </n-form-item>

        <!-- rclone 预设快捷卡（A5） -->
        <n-form-item v-if="createForm.destType === 'rclone'" :label="t('repo.dest.rclonePresetLabel')">
          <div class="repo__dest-grid">
            <button
              v-for="preset in RCLONE_PRESETS"
              :key="preset.key"
              type="button"
              class="repo__dest-card"
              :class="{ 'repo__dest-card--active': createForm.rclonePreset === preset.key }"
              @click="selectRclonePreset(preset.key)"
            >
              {{ preset.label }}
            </button>
          </div>
          <n-text depth="3" class="repo__note">{{ currentDest.note }}</n-text>
        </n-form-item>

        <n-form-item :label="t('repo.colId')">
          <n-space vertical :size="6" class="repo__cred">
            <n-space :size="8" align="center">
              <n-input
                v-model:value="createForm.id"
                :placeholder="t('repo.idAutoHint')"
                class="repo__id-input"
              />
              <n-button size="small" secondary :loading="generatingId" @click="regenerateRepoId">
                {{ t('repo.regenerate') }}
              </n-button>
            </n-space>
            <n-text depth="3" class="repo__note">{{ t('repo.idAutoNote') }}</n-text>
          </n-space>
        </n-form-item>
        <n-form-item :label="t('repo.colName')">
          <n-input v-model:value="createForm.name" :placeholder="t('repo.nameRequired')" />
        </n-form-item>
        <n-form-item :label="t('repo.url')">
          <n-input v-model:value="createForm.url" :placeholder="currentDest.placeholder" />
          <n-text depth="3" class="repo__note">{{ currentDest.note }}</n-text>
        </n-form-item>
        <n-form-item :label="t('repo.hostTag')">
          <n-input v-model:value="createForm.hostTag" :placeholder="t('repo.hostTagPlaceholder')" />
        </n-form-item>
      </n-form>
      <n-space justify="end">
        <n-button secondary @click="createShow = false">{{ t('common.cancel') }}</n-button>
        <n-button type="primary" :loading="creating" @click="onCreate">{{ t('common.confirm') }}</n-button>
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

    <!-- C4 密钥轮换：新密码须输入两次且 ≥10 位；成功后旧密码作废，口令文件同步重写 -->
    <n-modal v-model:show="rotateShow" preset="card" :title="t('repo.rotateBtn')" class="repo__modal">
      <n-space vertical :size="10">
        <n-alert type="warning" :show-icon="false">{{ t('repo.rotateDesc') }}</n-alert>
        <n-form label-placement="left" label-width="120" size="small">
          <n-form-item :label="t('repo.rotateNewPw')">
            <n-input
              v-model:value="rotatePw"
              type="password"
              show-password-on="click"
              :placeholder="t('repo.rotateNewPwPlaceholder')"
            />
          </n-form-item>
          <n-form-item :label="t('repo.rotateConfirmPw')">
            <n-input
              v-model:value="rotatePwConfirm"
              type="password"
              show-password-on="click"
              :placeholder="t('repo.rotateConfirmPwPlaceholder')"
            />
          </n-form-item>
        </n-form>
        <n-text v-if="rotatePwConfirm && !rotateReady" depth="3" type="error">
          {{ t('repo.rotateMismatch') }}
        </n-text>
        <n-space justify="end">
          <n-button quaternary @click="rotateShow = false">{{ t('common.cancel') }}</n-button>
          <n-button type="primary" :loading="rotating" :disabled="!rotateReady" @click="onRotate">
            {{ t('repo.rotateBtn') }}
          </n-button>
        </n-space>
      </n-space>
    </n-modal>

    <!-- C4 手动 copy：把当前仓快照增量复制到目标仓（增量只传新快照，可中断续传） -->
    <n-modal v-model:show="copyShow" preset="card" :title="t('repo.copyBtn')" class="repo__modal">
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
  </n-space>
</template>

<style scoped>
.repo__note {
  display: block;
  font-size: 12px;
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

/* 一次性密码展示：等宽字体便于人工抄录，只读态由 n-input 的 readonly 提供 */
.repo__password-out {
  margin-top: 8px;
  /* 等宽常量收口：theme/tokens.ts fontFamilyMono（经 --font-family-mono 全局变量） */
  font-family: var(--font-family-mono);
}

.repo__password-actions {
  margin-top: 12px;
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

/* E4 密码行内展开面板：嵌在状态卡 passfile 告警与按钮之后的次级面板
   （1px 边框 + 弱分隔底色，与卡片内容区分层；不再使用模态弹窗）。 */
.repo__password-panel {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px;
  border: 1px solid var(--n-border-color, rgba(128, 128, 140, 0.24));
  border-radius: 8px;
  background: var(--n-action-color, transparent);
}

.repo__password-form {
  margin: 0;
}

.repo__password-actions {
  margin-top: 2px;
}

/* A5 目的地类型卡片网格 */
.repo__dest-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(96px, 1fr));
  gap: 8px;
  width: 100%;
}

.repo__dest-card {
  padding: 10px 6px;
  border: 1px solid var(--n-border-color, #e0e0e6);
  border-radius: 8px;
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
</style>
