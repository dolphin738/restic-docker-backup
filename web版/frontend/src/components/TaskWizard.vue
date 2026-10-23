<script setup lang="ts">
/** 新建/编辑任务五步向导（增量设计 §5.3）。
 *
 * 步骤：①基础（名称/启停/运行模式/密码文件状态）→ ②备份源（SourcesPicker）
 * → ③存储目标（仓库只读 + 测试连接）→ ④调度（ScheduleRetentionForm）
 * → ⑤保留与首跑（dry-run 预览门 + 保存/首跑）。
 *
 * 编辑流：E34b 预填；tag 只读；sources/excludes 变更后服务端重置 dry_run_pending
 * （UI 拦截双保险，R-026-08）。
 */

import { useQuery, useQueryClient } from '@tanstack/vue-query'
import {
  NAlert,
  NButton,
  NCard,
  NIcon,
  NInput,
  NInputNumber,
  NSelect,
  NSpace,
  NStep,
  NSteps,
  NSwitch,
  NTag,
  NText,
  NTooltip,
  useMessage,
} from 'naive-ui'
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { onBeforeRouteLeave, useRouter } from 'vue-router'
import {
  createBackupTask,
  dryRunBackupTask,
  dryRunSources,
  getBackupTask,
  updateBackupTask,
} from '@/api/backupTasks'
import { getConfig } from '@/api/config'
import { hasCode, isAppError } from '@/api/http'
import { createJob } from '@/api/jobs'
import { getRepo, getRepoStatus, listRepos, testRepo } from '@/api/repo'
import type { BackupTask, DryRunResponse, RepoTestResponse, TaskUpsertRequest } from '@/api/types'
import {
  IconArrowLeft,
  IconCheckCircle,
  IconChevronRight,
  IconPlay,
  IconSearch,
  IconServer,
} from '@/components/icons/icons'
import ScheduleRetentionForm, {
  type ScheduleRetentionValue,
} from '@/components/task/ScheduleRetentionForm.vue'
import SourcesPicker from '@/components/task/SourcesPicker.vue'
import { dryRunBlockReason, isDryRunOk } from '@/components/task/dryRunGate'
import StatusTag from '@/components/StatusTag.vue'
import TierCard from '@/components/TierCard.vue'
import { useUiStore } from '@/stores/ui'
import { formatDateTime } from '@/utils/datetime'

const props = defineProps<{
  /** 编辑模式的任务 id；缺省 = 新建。 */
  taskId?: number | null
}>()

const { t } = useI18n()
const router = useRouter()
const ui = useUiStore()
const queryClient = useQueryClient()

const message = useMessage()
ui.setMessageApi(message)
onUnmounted(() => ui.setMessageApi(null))

const isEdit = computed(() => props.taskId != null)
const step = ref(1)

// ---------------------------------------------------------------- 表单状态
interface TaskFormState extends ScheduleRetentionValue {
  name: string
  enabled: boolean
  sources: string[]
  excludes: string[]
  /** 以下高级字段不在向导 UI 暴露：新建取缺省，编辑时原样往返（TaskUpsertRequest 全量语义）。 */
  group_by: string
  one_file_system: boolean
  compression: string
  check_sample_ratio: number
  monthly_full_day: number
  prune_interval_days: number
  /** 限速（owner 2026-10-22 任务级裁决）：任务级配置、创建时生效——值随任务保存，
   *  下次备份进程启动时读入 restic argv（--limit-upload/--limit-download），
   *  运行中的任务不可改（restic CLI 无控制通道）。单位 KiB/s，0 = 不限。
   *  **类型含 null**：n-input-number 清空即为 null，提交前由 buildPayload 兜底 0（B5）。 */
  limit_up: number | null
  limit_down: number | null
  /** C3 调度触发失败后的最大重试次数（0 = 不重试）；同样可能被清空为 null。 */
  retry_count: number | null
  /** C4 多目的地：主备份成功后镜像到的仓库 id。 */
  mirror_repo_ids: string[]
}

function defaultForm(): TaskFormState {
  return {
    name: '',
    enabled: true,
    sources: [],
    excludes: [],
    cron: '',
    keep_last: 0,
    keep_hourly: 0,
    keep_daily: 7,
    keep_weekly: 4,
    keep_monthly: 6,
    keep_yearly: 1,
    // A2 时间线保留：新建默认不启用（空串 = 未启用；与计数档 0 口径一致）
    keep_within: '',
    keep_within_hourly: '',
    keep_within_daily: '',
    keep_within_weekly: '',
    keep_within_monthly: '',
    keep_within_yearly: '',
    keep_tags_text: '',
    group_by: 'host,paths,tags',
    one_file_system: false,
    compression: 'auto',
    check_sample_ratio: 0.02,
    monthly_full_day: 1,
    prune_interval_days: 7,
    limit_up: 0,
    limit_down: 0,
    retry_count: 2,
    mirror_repo_ids: [],
  }
}

const form = reactive<TaskFormState>(defaultForm())

// ---------------------------------------------------------------- E2 离开保护的脏判定基线
/** 表单基线快照：预填完成 / 草稿恢复 / 保存成功后刷新，用于「是否有未保存改动」判定。
 *  用快照比对而非 deep watch 标志位——预填与草稿恢复都是批量赋值，deep watch 会误判为脏。 */
const formBaseline = ref(JSON.stringify(defaultForm()))
const dirty = computed(() => JSON.stringify(form) !== formBaseline.value)

function markClean(): void {
  formBaseline.value = JSON.stringify(form)
}

// ---------------------------------------------------------------- 草稿持久化（P4：仅新建模式）
/** 新建任务要连过五步、中途离开即全丢——本地续填（localStorage）。
 *  编辑模式不参与：库里有权威态，草稿回填会与服务端值混淆（且 hydrated 预填会覆盖草稿）。 */
const DRAFT_KEY = 'restic-web:wizard-draft:v1'

interface DraftEnvelope {
  step: number
  form: TaskFormState
  savedAt: string
}

/** 待决草稿：只提示、不自动回填（自动覆盖用户正在填的内容更糟，交由用户点「恢复」）。 */
const draftPending = ref<DraftEnvelope | null>(null)

function readDraft(): DraftEnvelope | null {
  try {
    const raw = localStorage.getItem(DRAFT_KEY)
    if (!raw) {
      return null
    }
    const parsed: unknown = JSON.parse(raw)
    if (typeof parsed !== 'object' || parsed === null) {
      return null
    }
    const envelope = parsed as Partial<DraftEnvelope>
    return typeof envelope.form === 'object' && envelope.form !== null ? (envelope as DraftEnvelope) : null
  } catch {
    // 坏草稿 / 隐私模式禁写：静默忽略，不打断向导
    return null
  }
}

if (!isEdit.value) {
  draftPending.value = readDraft()
}

/** 恢复：先整份重置再套用草稿，避免旧草稿缺字段时留下半新半旧状态。 */
function restoreDraft(): void {
  const draft = draftPending.value
  if (!draft) {
    return
  }
  Object.assign(form, defaultForm(), draft.form)
  step.value = Math.min(5, Math.max(1, Number(draft.step) || 1))
  draftPending.value = null
  markClean()
}

function discardDraft(): void {
  try {
    localStorage.removeItem(DRAFT_KEY)
  } catch {
    // 忽略：无法写入不影响继续填写
  }
  draftPending.value = null
}

function clearDraft(): void {
  discardDraft()
}

/** 防抖写盘（500ms）：reactive 深度监听；草稿待决期间不写，避免覆盖用户尚未决定的那份。 */
let draftTimer: ReturnType<typeof setTimeout> | undefined
watch(
  form,
  () => {
    if (isEdit.value || draftPending.value) {
      return
    }
    clearTimeout(draftTimer)
    draftTimer = setTimeout(() => {
      const envelope: DraftEnvelope = {
        step: step.value,
        form: { ...form } as TaskFormState,
        savedAt: new Date().toISOString(),
      }
      try {
        localStorage.setItem(DRAFT_KEY, JSON.stringify(envelope))
      } catch {
        // 配额/隐私模式：静默降级为不持久化
      }
    }, 500)
  },
  { deep: true },
)

onUnmounted(() => clearTimeout(draftTimer))

/** 编辑预填时的源集合快照：用于「变更后需重新预览」的本地提示。 */
const originalSources = ref<string[]>([])
const originalExcludes = ref<string[]>([])
const loadedTask = ref<BackupTask | null>(null)

// ---------------------------------------------------------------- 编辑预填（E34b）
const taskQuery = useQuery({
  queryKey: ['backup-task', props.taskId ?? 0],
  queryFn: () => (props.taskId == null ? Promise.resolve(null) : getBackupTask(props.taskId)),
})

/** 预填只执行一次：onDryRun 成功后会 invalidate 本 query 触发 refetch，
 *  refetch 返回的新对象引用会再次命中本 watch——若放行会用服务端存量值覆盖
 *  用户正在编辑的表单（改动丢失），并把 originalSources 重置导致 sourcesChanged 恒 false
 *  （S-1 stale 判定随之失效）。服务端权威态由保存路径回填，此处只管首次进表单。 */
let hydrated = false

watch(
  () => taskQuery.data.value,
  (task) => {
    if (!task || hydrated) {
      return
    }
    hydrated = true
    loadedTask.value = task
    form.name = task.name
    form.enabled = task.enabled
    form.sources = [...task.display_sources]
    form.excludes = [...task.display_excludes]
    form.cron = task.cron ?? ''
    form.keep_last = task.keep_last
    form.keep_hourly = task.keep_hourly
    form.keep_daily = task.keep_daily
    form.keep_weekly = task.keep_weekly
    form.keep_monthly = task.keep_monthly
    form.keep_yearly = task.keep_yearly
    // A2 时间线保留原样往返；keep_tags 列表 → 表单层逗号分隔文本
    form.keep_within = task.keep_within
    form.keep_within_hourly = task.keep_within_hourly
    form.keep_within_daily = task.keep_within_daily
    form.keep_within_weekly = task.keep_within_weekly
    form.keep_within_monthly = task.keep_within_monthly
    form.keep_within_yearly = task.keep_within_yearly
    form.keep_tags_text = task.keep_tags.join(', ')
    form.group_by = task.group_by
    form.one_file_system = task.one_file_system
    form.compression = task.compression
    form.check_sample_ratio = task.check_sample_ratio
    form.monthly_full_day = task.monthly_full_day
    form.prune_interval_days = task.prune_interval_days
    form.limit_up = task.limit_up
    form.limit_down = task.limit_down
    form.retry_count = task.retry_count
    form.mirror_repo_ids = [...task.mirror_repo_ids]
    originalSources.value = [...task.display_sources]
    originalExcludes.value = [...task.display_excludes]
    // 预填是一次批量赋值，不构成「用户改动」→ 立刻刷新脏判定基线（E2）
    markClean()
  },
  { immediate: true },
)

// ---------------------------------------------------------------- 仓库与配置（E30/E06/R-023）
const configQuery = useQuery({ queryKey: ['config'], queryFn: getConfig })
const repoQuery = useQuery({
  queryKey: ['repo', ui.currentRepoId],
  queryFn: () => getRepo(ui.currentRepoId),
})
const repoStatusQuery = useQuery({
  queryKey: ['repo', 'status', ui.currentRepoId],
  queryFn: () => getRepoStatus(ui.currentRepoId),
  refetchInterval: 60_000,
})

// R-023：仓库地址落在 repos 表（E07 已拒收 repo.url 配置键），从 repo 对象读取
const repoUrl = computed(() => repoQuery.data.value?.url ?? '')
const passfileConfigured = computed(() => configQuery.data.value?.passfile_status === 'configured')

// C4 多目的地：镜像仓候选 = 其它仓库（自身已排除，由提交前校验双保险）
const reposQuery = useQuery({ queryKey: ['repos'], queryFn: listRepos })
const mirrorOptions = computed(() =>
  (reposQuery.data.value ?? [])
    .filter((repo) => repo.id !== ui.currentRepoId)
    .map((repo) => ({
      label: `${repo.name} (${repo.id})`,
      value: repo.id,
      disabled: !(repo.url ?? '').trim() || repo.passfile_status !== 'configured',
    })),
)

/** 源集合是否较预填值发生变化（保存后服务端会重置 dry_run_pending）。 */
const sourcesChanged = computed(() => {
  if (!isEdit.value) {
    return false
  }
  return (
    form.sources.join('\n') !== originalSources.value.join('\n') ||
    form.excludes.join('\n') !== originalExcludes.value.join('\n')
  )
})

// ---------------------------------------------------------------- 步骤导航
function validateStep(current: number): boolean {
  if (current === 1 && form.name.trim() === '') {
    message.warning(t('wizard.needName'))
    return false
  }
  if (current === 2 && form.sources.length === 0) {
    message.warning(t('wizard.needSources'))
    return false
  }
  return true
}

function goNext(): void {
  if (!validateStep(step.value)) {
    return
  }
  // G3/E1 强门禁：新建流程第③步必须先通过连接测试（无逃生门）。
  // 与 Duplicati `next()` 同口径——逐个目的地顺序验证，失败即停在当前步。
  if (step3Blocked.value) {
    message.warning(t('wizard.testRequired'))
    void onTestConnection()
    return
  }
  step.value = Math.min(5, step.value + 1)
}

function goPrev(): void {
  step.value = Math.max(1, step.value - 1)
}

// ---------------------------------------------------------------- E3/G7 步骤条跳步
/** 跳步规则：向后随意（不丢数据）；向前须逐级通过校验（含第③步连接测试门禁），
 *  首个不通过的步即为落点——与「提交时回流首个未过步骤」同一套判定，避免两处口径漂移。 */
function onStepJump(target: number): void {
  if (target === step.value) {
    return
  }
  if (target < step.value) {
    step.value = target
    return
  }
  for (let cursor = step.value; cursor < target; cursor += 1) {
    if (!validateStep(cursor)) {
      step.value = cursor
      return
    }
    if (cursor === 3 && !isEdit.value && !testPassed.value) {
      message.warning(t('wizard.testRequired'))
      step.value = 3
      return
    }
  }
  step.value = target
}

/** 键盘可达（修复模版步骤条 tabindex=-1 的缺陷）：←/→ 前后翻步，数字键 1–5 直达。
 *  焦点挂在包裹 nav 上——naive 的步骤节点是 div（不可聚焦），容器级键盘导航是最小改动解。 */
function onStepsKeydown(event: KeyboardEvent): void {
  if (event.key === 'ArrowLeft') {
    event.preventDefault()
    goPrev()
    return
  }
  if (event.key === 'ArrowRight') {
    event.preventDefault()
    goNext()
    return
  }
  if (/^[1-5]$/.test(event.key)) {
    event.preventDefault()
    onStepJump(Number(event.key))
  }
}

/** G7：提交前串行校验 ①–③，失败则**回流到首个未通过的步骤**
 *  （原先只在第⑤步弹一句「请填写任务名」，用户还要自己猜是哪一步出错）。 */
function focusFirstInvalidStep(): boolean {
  for (let cursor = 1; cursor <= 3; cursor += 1) {
    if (!validateStep(cursor)) {
      step.value = cursor
      return false
    }
  }
  if (!isEdit.value && !testPassed.value) {
    message.warning(t('wizard.testRequired'))
    step.value = 3
    return false
  }
  return true
}

// ---------------------------------------------------------------- E2 离开保护
/** 路由级离开确认（侧栏导航、浏览器后退均覆盖）。 */
onBeforeRouteLeave(() => {
  if (!dirty.value) {
    return true
  }
  // 原生确认：无需新增弹窗组件，且不打断 naive 的消息队列
  return window.confirm(t('wizard.leaveConfirm'))
})

/** 关闭标签页/刷新：浏览器只认 preventDefault + returnValue，文案由浏览器提供。 */
function onBeforeUnload(event: BeforeUnloadEvent): void {
  if (!dirty.value) {
    return
  }
  event.preventDefault()
  event.returnValue = ''
}

onMounted(() => window.addEventListener('beforeunload', onBeforeUnload))
onUnmounted(() => window.removeEventListener('beforeunload', onBeforeUnload))

// ---------------------------------------------------------------- dry-run 预览门
const dryRunLoading = ref(false)
const dryRunResult = ref<DryRunResponse | null>(null)
const dryRunError = ref('')

/** 预览时的源集合指纹：stale 判定基准（S-1）。
 *  口径是「预览后源集合是否又变过」——改源→预览→立即首跑是放行的（预览即当前值）；
 *  预览后再改源 → 指纹失配 → 旧预览作废须重扫。与 sourcesChanged（表单 vs 库中存量）
 *  是两个语义，不得混用。 */
const previewedFingerprint = ref<string | null>(null)

function sourcesFingerprint(): string {
  return `${form.sources.join('\n')}\u0000${form.excludes.join('\n')}`
}

/** 旧预览是否失效：预览过且当前源集合与预览时不一致。 */
const stalePreview = computed(
  () => previewedFingerprint.value !== null && sourcesFingerprint() !== previewedFingerprint.value,
)

/** 预览成功才可「确认并立即首跑」：无 error **且未截断**（F1，QA 复验 Low 项），
 *  且预览后源集合未再变更（S-1：stale 预览不得放行首跑）。
 *  与服务端契约对齐——E36 仅在「无 error 且未截断」时才清 dry_run_pending；
 *  截断 = 只看到部分扫描范围（DRY_RUN_MAX_ITEMS 上限），服务端保守保持 pending=1，
 *  前端同样禁止在盲区下启动首跑。判定逻辑单一实现点见 ./task/dryRunGate.ts。 */
const dryRunOk = computed(() => isDryRunOk(dryRunResult.value, stalePreview.value))

/** 按钮禁用时的工具提示（零新增 i18n 键）：stale → rescan_required；
 *  截断 → 截断警告 + 通用提示；其余 → 通用提示。 */
const dryRunBlockHint = computed(() => {
  switch (dryRunBlockReason(dryRunResult.value, stalePreview.value)) {
    case 'stale':
      return t('wizard.rescan_required')
    case 'truncated':
      return `${t('wizard.dry_run_truncated')} · ${t('wizard.dry_run_hint')}`
    default:
      return t('wizard.dry_run_hint')
  }
})

async function onDryRun(): Promise<void> {
  if (form.sources.length === 0) {
    message.warning(t('wizard.needSources'))
    return
  }
  dryRunLoading.value = true
  dryRunError.value = ''
  dryRunResult.value = null
  try {
    dryRunResult.value = isEdit.value
      ? await dryRunBackupTask(props.taskId!, { sources: form.sources, excludes: form.excludes })
      : await dryRunSources(form.sources, form.excludes)
    // 记录本次预览的源集合指纹（S-1 stale 判定基准）；无论成功与否都按本次请求口径记
    previewedFingerprint.value = sourcesFingerprint()
    if (isEdit.value && isDryRunOk(dryRunResult.value)) {
      // E36 预览成功（无 error 且未截断，服务端已清 dry_run_pending）：刷新本地任务态，
      // 让门禁提示即时消失；截断时服务端保持 pending=1，无需刷新（F1）。
      // stale 与此处无关——invalidate 只回填服务端权威态，预填由 hydrated 标志挡住。
      await queryClient.invalidateQueries({ queryKey: ['backup-task', props.taskId ?? 0] })
    }
  } catch (error) {
    dryRunError.value = isAppError(error) ? error.message : t('errors.UNKNOWN')
  } finally {
    dryRunLoading.value = false
  }
}

// ---------------------------------------------------------------- 保存 / 首跑
const saving = ref(false)

function buildPayload(): TaskUpsertRequest {
  return {
    name: form.name.trim(),
    enabled: form.enabled,
    sources: form.sources,
    excludes: form.excludes,
    keep_last: form.keep_last,
    keep_hourly: form.keep_hourly,
    keep_daily: form.keep_daily,
    keep_weekly: form.keep_weekly,
    keep_monthly: form.keep_monthly,
    keep_yearly: form.keep_yearly,
    // A2 时间线保留原样提交；keep_tags 表单层逗号分隔文本 → 数组（去空白/丢空；
    // 去重与注入校验由服务端 normalize 终判——前端不做第二套校验）
    keep_within: form.keep_within,
    keep_within_hourly: form.keep_within_hourly,
    keep_within_daily: form.keep_within_daily,
    keep_within_weekly: form.keep_within_weekly,
    keep_within_monthly: form.keep_within_monthly,
    keep_within_yearly: form.keep_within_yearly,
    keep_tags: form.keep_tags_text
      .split(',')
      .map((item) => item.trim())
      .filter((item) => item !== ''),
    group_by: form.group_by,
    cron: form.cron.trim() || null,
    one_file_system: form.one_file_system,
    compression: form.compression,
    check_sample_ratio: form.check_sample_ratio,
    monthly_full_day: form.monthly_full_day,
    prune_interval_days: form.prune_interval_days,
    // B5（owner 2026-10-23 裁决）：n-input-number 支持清空 → 值为 null，直接提交会 422
    // （后端 TaskUpsertRequest 的 limit_up/limit_down 是 int 非 Optional）→ 此处兜底 0。
    limit_up: form.limit_up ?? 0,
    limit_down: form.limit_down ?? 0,
    retry_count: form.retry_count ?? 0,
    mirror_repo_ids: form.mirror_repo_ids,
  }
}

async function afterSaved(): Promise<void> {
  // 落库成功 → 草稿作废（留着会让下次新建误提示一份已完成的草稿）
  clearDraft()
  // 已落库 → 不再是「未保存改动」，放行离开（E2 的 confirm 不应在成功跳转后再弹一次）
  markClean()
  await queryClient.invalidateQueries({ queryKey: ['backup-tasks'] })
  void router.push({ name: 'dashboard' })
}

/** 仅保存：不触发 dry-run 门（首跑前的预览拦截由仪表盘 Start 按钮负责）。 */
async function onSaveOnly(): Promise<void> {
  // G7：失败时回流到首个未通过的步骤，而不是停在第⑤步空弹一句
  if (!focusFirstInvalidStep()) {
    return
  }
  saving.value = true
  try {
    if (isEdit.value) {
      await updateBackupTask(props.taskId!, buildPayload())
      message.success(t('wizard.updated'))
    } else {
      await createBackupTask(buildPayload())
      message.success(t('wizard.created'))
    }
    await afterSaved()
  } finally {
    saving.value = false
  }
}

/** 确认并立即首跑：先过 dry-run 预览门，再保存并提交 backup job（R-026-08）。 */
async function onSaveAndRun(): Promise<void> {
  if (!focusFirstInvalidStep()) {
    return
  }
  if (!dryRunOk.value) {
    message.warning(dryRunBlockHint.value)
    return
  }
  saving.value = true
  try {
    const taskId = isEdit.value ? props.taskId! : (await createBackupTask(buildPayload())).id
    if (isEdit.value) {
      await updateBackupTask(taskId, buildPayload())
    }
    await createJob('backup', { taskId })
    message.success(t('wizard.firstRunSubmitted'))
    await afterSaved()
  } catch (error) {
    if (hasCode(error, 'JOB_CONFLICT')) {
      const raw = isAppError(error) ? error.data?.['running_task_name'] : null
      const name = typeof raw === 'string' && raw ? raw : t('common.none')
      message.error(t('task.conflict', { name }))
      return
    }
    if (!isAppError(error)) {
      throw error
    }
  } finally {
    saving.value = false
  }
}

// ---------------------------------------------------------------- 测试连接（E29 + G3/E1 强门禁）
/** 测试状态机（对齐 Duplicati `destination.component.ts:325-352` 的 testStates 顺序验证）：
 *  idle → testing → success | error。success 会**绑定仓库标识**缓存（切仓即失效），
 *  用于「已验证则跳过重测」——同一仓库不必为前进反复打一次网络请求。 */
type TestState = 'idle' | 'testing' | 'success' | 'error'
const testState = ref<TestState>('idle')
const testResult = ref<RepoTestResponse | null>(null)
/** success 缓存归属的仓库（切仓/改址后必须重测，避免拿旧结论放行）。 */
const testedRepoId = ref<string | null>(null)

const testing = computed(() => testState.value === 'testing')

/** 当前仓库是否已通过连接测试（仓库标识一致才算数）。 */
const testPassed = computed(
  () => testState.value === 'success' && testedRepoId.value === ui.currentRepoId,
)

/** 是否拦在第③步：仅新建任务（isEdit=false）走强门禁——编辑时仓库已验收过，无需重测
 *  （与 Duplicati `next()` 的 `isNew()` 分支同口径）。无逃生门：不提供「仍然继续」。 */
const step3Blocked = computed(() => step.value === 3 && !isEdit.value && !testPassed.value)

// 切换仓库 → 旧结论作废（配置信息已变，缓存不可复用）
watch(
  () => ui.currentRepoId,
  () => {
    testState.value = 'idle'
    testResult.value = null
    testedRepoId.value = null
  },
)

async function onTestConnection(): Promise<void> {
  testState.value = 'testing'
  testResult.value = null
  try {
    const result = await testRepo(ui.currentRepoId)
    testResult.value = result
    testedRepoId.value = ui.currentRepoId
    testState.value = result.ok ? 'success' : 'error'
  } catch {
    // http 层保证只抛 AppError（网络错误也包装，api/http.ts 统一出口）；全局副作用已提示，
    // 本页状态机落 error 即可——不再 rethrow（void 调用链上无人接，只会变成 unhandled rejection）
    testState.value = 'error'
  } finally {
    if (testState.value === 'testing') {
      testState.value = 'error'
    }
  }
}

const taskLoadFailed = computed(() => {
  const error = taskQuery.error.value
  return error ? (isAppError(error) ? error.message : t('errors.UNKNOWN')) : ''
})

// ---------------------------------------------------------------- 调度/保留子表单（显式收发，类型安全）
const scheduleValue = computed<ScheduleRetentionValue>(() => ({
  cron: form.cron,
  keep_last: form.keep_last,
  keep_hourly: form.keep_hourly,
  keep_daily: form.keep_daily,
  keep_weekly: form.keep_weekly,
  keep_monthly: form.keep_monthly,
  keep_yearly: form.keep_yearly,
  keep_within: form.keep_within,
  keep_within_hourly: form.keep_within_hourly,
  keep_within_daily: form.keep_within_daily,
  keep_within_weekly: form.keep_within_weekly,
  keep_within_monthly: form.keep_within_monthly,
  keep_within_yearly: form.keep_within_yearly,
  keep_tags_text: form.keep_tags_text,
}))

function onScheduleUpdate(value: ScheduleRetentionValue): void {
  form.cron = value.cron
  form.keep_last = value.keep_last
  form.keep_hourly = value.keep_hourly
  form.keep_daily = value.keep_daily
  form.keep_weekly = value.keep_weekly
  form.keep_monthly = value.keep_monthly
  form.keep_yearly = value.keep_yearly
  form.keep_within = value.keep_within
  form.keep_within_hourly = value.keep_within_hourly
  form.keep_within_daily = value.keep_within_daily
  form.keep_within_weekly = value.keep_within_weekly
  form.keep_within_monthly = value.keep_within_monthly
  form.keep_within_yearly = value.keep_within_yearly
  form.keep_tags_text = value.keep_tags_text
}
</script>

<template>
  <n-space vertical :size="16">
    <n-card :title="isEdit ? t('task.edit') : t('wizard.submitCreate')" size="small">
      <!-- E3：步骤条可点击跳步（naive 在提供 @update:current 时自动加 --clickable 态）+ 键盘可达
           （←/→ 翻步、数字键 1–5 直达）。nav 承载键盘焦点与 aria 语义。 -->
      <nav
        class="wizard__steps-nav"
        :aria-label="t('wizard.stepsNavLabel')"
        tabindex="0"
        @keydown="onStepsKeydown"
      >
        <n-steps :current="step" size="small" class="wizard__steps" @update:current="onStepJump">
          <n-step :title="t('wizard.step1')" />
          <n-step :title="t('wizard.step2')" />
          <n-step :title="t('wizard.step3')" />
          <n-step :title="t('wizard.step4')" />
          <n-step :title="t('wizard.step5')" />
        </n-steps>
      </nav>

      <n-alert v-if="isEdit && taskLoadFailed" type="error" :show-icon="false">
        {{ t('dashboard.loadFailed', { message: taskLoadFailed }) }}
      </n-alert>

      <!-- P4：草稿续填提示（仅新建；不自动回填，交由用户点「恢复」） -->
      <n-alert v-if="draftPending" type="info" :show-icon="false" class="wizard__draft">
        <n-space align="center" :size="8">
          <n-text depth="2">
            {{ t('wizard.draftFound', { time: formatDateTime(draftPending.savedAt) }) }}
          </n-text>
          <n-button size="tiny" type="primary" secondary @click="restoreDraft">
            {{ t('wizard.draftRestore') }}
          </n-button>
          <n-button size="tiny" quaternary @click="discardDraft">
            {{ t('wizard.draftDiscard') }}
          </n-button>
        </n-space>
      </n-alert>

      <n-space vertical :size="16" class="wizard__body">
        <!-- ① 基础信息 -->
        <template v-if="step === 1">
          <n-space vertical :size="4">
            <n-text strong>{{ t('wizard.name') }}</n-text>
            <n-input
              v-model:value="form.name"
              :placeholder="t('wizard.namePlaceholder')"
              :maxlength="64"
            />
          </n-space>

          <n-space align="center" :size="8">
            <n-switch v-model:value="form.enabled" size="small" :aria-label="t('wizard.enabled')" />
            <n-text depth="3">{{ t('wizard.enabled') }}</n-text>
          </n-space>

          <n-text strong>{{ t('wizard.modeCard') }}</n-text>
          <TierCard :user="ui.me?.user ?? ''" />

          <n-space align="center" :size="8">
            <n-text depth="3">{{ t('wizard.passfile') }}：</n-text>
            <n-tag size="small" round :type="passfileConfigured ? 'success' : 'warning'">
              {{ passfileConfigured ? t('wizard.passfileConfigured') : t('wizard.passfileMissing') }}
            </n-tag>
          </n-space>

          <!-- C3 任务级失败重试 -->
          <n-space align="center" :size="8">
            <n-text depth="3">{{ t('wizard.retryCount') }}</n-text>
            <n-input-number
              v-model:value="form.retry_count"
              :min="0"
              :max="5"
              size="small"
              class="wizard__num"
            />
            <n-text depth="3">{{ t('wizard.retryHint') }}</n-text>
          </n-space>

          <!-- 限速（owner 2026-10-22 任务级裁决：任务级配置、创建时生效；运行中不可改） -->
          <n-space vertical :size="4">
            <n-space align="center" :size="8">
              <n-text depth="3">{{ t('wizard.limitUp') }}</n-text>
              <n-input-number v-model:value="form.limit_up" :min="0" size="small" class="wizard__num" />
              <n-text depth="3">{{ t('wizard.limitDown') }}</n-text>
              <n-input-number v-model:value="form.limit_down" :min="0" size="small" class="wizard__num" />
            </n-space>
            <n-text depth="3">{{ t('wizard.limitHint') }}</n-text>
          </n-space>
        </template>

        <!-- ② 备份源 -->
        <template v-else-if="step === 2">
          <SourcesPicker v-model:sources="form.sources" v-model:excludes="form.excludes" />
        </template>

        <!-- ③ 存储目标 -->
        <template v-else-if="step === 3">
          <n-space vertical :size="4">
            <n-text strong>{{ t('wizard.repoReadonly') }}</n-text>
            <n-input :value="repoUrl" readonly :placeholder="t('wizard.repoReadonly')" />
            <n-space align="center" :size="8">
              <n-tag size="small" round :type="repoStatusQuery.data.value?.connected ? 'success' : 'error'">
                {{
                  repoStatusQuery.data.value?.connected
                    ? t('dashboard.repoConnected')
                    : t('dashboard.repoDisconnected')
                }}
              </n-tag>
              <n-text depth="3">{{ ui.currentRepoId }}</n-text>
            </n-space>
            <n-text depth="3">{{ t('dashboard.repoCacheNote') }}</n-text>
          </n-space>

          <!-- G3/E1：测试连接状态机（idle/testing/success/error），成功结果按仓库缓存 -->
          <n-space align="center" :size="8">
            <n-button size="small" secondary :loading="testing" @click="onTestConnection">
              <template #icon>
                <n-icon :size="16"><IconServer /></n-icon>
              </template>
              {{ t('wizard.testConnection') }}
            </n-button>
            <StatusTag
              v-if="testState === 'success'"
              kind="success"
              :label="t('wizard.testOk', { n: testResult?.latency_ms ?? 0 })"
            />
            <StatusTag v-else-if="testState === 'error'" kind="error" :label="t('wizard.testFail')" />
            <StatusTag v-else kind="warning" :label="t('wizard.testPending')" />
          </n-space>
          <n-alert v-if="testResult && !testResult.ok" type="error" :show-icon="false">
            {{ testResult.reason || t('wizard.testFail') }}
          </n-alert>
          <!-- 门禁提示（aria-live：屏幕阅读器在点「继续」时也能听到拦截原因） -->
          <n-text v-if="step3Blocked" depth="3" class="wizard__note" aria-live="polite">
            {{ t('wizard.testRequired') }}
          </n-text>

          <!-- C4 多目的地镜像（主备份成功后增量复制） -->
          <n-space vertical :size="4">
            <n-text strong>{{ t('wizard.mirrorTitle') }}</n-text>
            <n-text depth="3">{{ t('wizard.mirrorHint') }}</n-text>
            <n-select
              v-model:value="form.mirror_repo_ids"
              multiple
              clearable
              :max-tag-count="3"
              :options="mirrorOptions"
              :placeholder="t('wizard.mirrorPlaceholder')"
            />
            <n-text depth="3">{{ t('wizard.mirrorMax') }}</n-text>
          </n-space>
        </template>

        <!-- ④ 调度 -->
        <template v-else-if="step === 4">
          <ScheduleRetentionForm :model-value="scheduleValue" @update:model-value="onScheduleUpdate" />
        </template>

        <!-- ⑤ 保留与首跑 -->
        <template v-else>
          <n-space v-if="isEdit" align="center" :size="8">
            <n-text depth="3">{{ t('wizard.tagReadonly') }}：</n-text>
            <n-tag size="small" round>{{ loadedTask?.tag ?? '' }}</n-tag>
          </n-space>

          <n-space vertical :size="8">
            <n-text strong>{{ t('wizard.retention') }}</n-text>
            <n-text depth="3">
              keep-last {{ form.keep_last }} · keep-hourly {{ form.keep_hourly }} · keep-daily
              {{ form.keep_daily }} · keep-weekly {{ form.keep_weekly }} · keep-monthly
              {{ form.keep_monthly }} · keep-yearly {{ form.keep_yearly }}
            </n-text>
          </n-space>

          <n-alert v-if="isEdit && loadedTask?.dry_run_pending" type="warning" :show-icon="false">
            {{ t('wizard.rescan_required') }}
          </n-alert>
          <n-alert v-else-if="sourcesChanged" type="warning" :show-icon="false">
            {{ t('wizard.rescan_required') }}
          </n-alert>

          <n-space vertical :size="8">
            <n-button size="small" secondary :loading="dryRunLoading" @click="onDryRun">
              <template #icon>
                <n-icon :size="16"><IconSearch /></n-icon>
              </template>
              {{ dryRunLoading ? t('wizard.dry_run_running') : t('wizard.dry_run') }}
            </n-button>
            <n-text depth="3" class="wizard__hint">{{ t('wizard.dry_run_hint') }}</n-text>
          </n-space>

          <n-alert v-if="dryRunError" type="error" :show-icon="false">{{ dryRunError }}</n-alert>
          <template v-if="dryRunResult">
            <n-alert v-if="dryRunResult.error" type="error" :show-icon="false">
              {{ dryRunResult.error }}
            </n-alert>
            <n-alert v-if="dryRunResult.truncated" type="warning" :show-icon="false">
              {{ t('wizard.dry_run_truncated') }}
            </n-alert>
            <n-alert
              v-if="dryRunResult.warnings.length"
              type="warning"
              :title="t('wizard.dry_run_pseudo')"
              :show-icon="false"
            >
              <n-space vertical :size="4">
                <n-text v-for="warning in dryRunResult.warnings" :key="warning.path" depth="3">
                  {{ warning.path }}
                </n-text>
              </n-space>
            </n-alert>
            <n-text depth="3">{{ t('wizard.dry_run_count', { n: dryRunResult.items.length }) }}</n-text>
            <n-space :size="8" class="wizard__preview">
              <n-tag
                v-for="item in dryRunResult.items.slice(0, 200)"
                :key="item.display_path"
                size="small"
              >
                {{ item.display_path }}
              </n-tag>
            </n-space>
          </template>
        </template>

        <!-- G7：页脚两端对齐（返回← 在左 / 继续→ 在右），对齐 Duplicati form-actions 形态 -->
        <n-space justify="space-between" align="center" class="wizard__footer">
          <n-button v-if="step > 1" quaternary @click="goPrev">
            <template #icon>
              <n-icon :size="16"><IconArrowLeft /></n-icon>
            </template>
            {{ t('confirm.back') }}
          </n-button>
          <n-space v-else />
          <n-space align="center" :size="8">
            <n-button v-if="step < 5" type="primary" @click="goNext">
              <template #icon>
                <n-icon :size="16"><IconChevronRight /></n-icon>
              </template>
              {{ t('confirm.next') }}
            </n-button>
            <template v-else>
              <n-button quaternary :loading="saving" @click="onSaveOnly">
                <template #icon>
                  <n-icon :size="16"><IconCheckCircle /></n-icon>
                </template>
                {{ isEdit ? t('wizard.submitUpdate') : t('wizard.save_only') }}
              </n-button>
              <n-tooltip v-if="!dryRunOk" trigger="hover">
                <template #trigger>
                  <n-button type="primary" disabled>
                    <template #icon>
                      <n-icon :size="16"><IconPlay /></n-icon>
                    </template>
                    {{ t('wizard.dry_run_confirm') }}
                  </n-button>
                </template>
                {{ dryRunBlockHint }}
              </n-tooltip>
              <n-button v-else type="primary" :loading="saving" @click="onSaveAndRun">
                <template #icon>
                  <n-icon :size="16"><IconPlay /></n-icon>
                </template>
                {{ t('wizard.dry_run_confirm') }}
              </n-button>
            </template>
          </n-space>
        </n-space>
      </n-space>
    </n-card>
  </n-space>
</template>

<style scoped>
/* E3：步骤条容器承载键盘焦点——焦点环只在此容器上出现，避免与按钮焦点态混淆 */
.wizard__steps-nav {
  border-radius: 6px;
}

/* 移动端走查：5 步在 390px 放不下，横向滚动优于裁切。
   ⚠️ 不能无条件加 overflow-x:auto——滚动容器会「收住」子元素外边距（不再与父级折叠），
   实测桌面端 steps 导航高度 22→30、下方整体下移 8px（像素 diff 抓出）。
   故只在移动断点启用；桌面保持原盒模型。 */
@media (max-width: 960px) {
  .wizard__steps-nav {
    overflow-x: auto;
  }
}

.wizard__steps-nav:focus-visible {
  outline: 2px solid var(--n-color-target, #2080f0);
  outline-offset: 2px;
}

.wizard__steps {
  margin-bottom: 8px;
}

/* G7：页脚与表单主体分开一行，返回/继续分列两端 */
.wizard__footer {
  margin-top: 8px;
}

.wizard__body {
  margin-top: 16px;
}

.wizard__draft {
  margin-top: 12px;
}

.wizard__hint {
  display: block;
  font-size: 12px;
}

/* G3/E1 门禁说明：与 wizard__hint 同规格（12px 辅助文本），单独类名便于定位 */
.wizard__note {
  display: block;
  font-size: 12px;
}

.wizard__num {
  width: 120px;
}

.wizard__preview {
  max-height: 200px;
  overflow-y: auto;
}
</style>
