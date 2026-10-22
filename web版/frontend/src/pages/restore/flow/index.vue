<script setup lang="ts">
import { useQuery } from '@tanstack/vue-query'
import {
  NAlert,
  NButton,
  NCard,
  NCheckbox,
  NDataTable,
  NGrid,
  NGi,
  NInput,
  NLog,
  NRadioButton,
  NRadioGroup,
  NSelect,
  NSpace,
  NSpin,
  NTag,
  NText,
  useMessage,
  type DataTableColumns,
} from 'naive-ui'
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import { getJobLog } from '@/api/jobs'
import { listSnapshotFiles, listSnapshots, searchSnapshotFiles } from '@/api/snapshots'
import {
  INPLACE_TARGET_ROOT,
  RESTORE_TARGET_ROOT,
  previewRestore,
  restoreSnapshot,
} from '@/api/restore'
import type { JobDonePayload, RestorePreviewItem, SnapshotFileOut } from '@/api/types'
import ConfirmGate from '@/components/ConfirmGate.vue'
import JobProgress from '@/components/JobProgress.vue'
import RestoreProgressRing from '@/components/restore/RestoreProgressRing.vue'
import RestoreStepper from '@/components/restore/RestoreStepper.vue'
import VirtualTree, { type TreeItem, type TreePage } from '@/components/VirtualTree.vue'
import { DATETIME_FALLBACK, formatDateTimeOr } from '@/utils/datetime'
import { formatBytesOr } from '@/utils/format'
import { useUiStore } from '@/stores/ui'

/** E20 覆盖预览单页条数（游标分页，配合「加载更多」续拉）。 */
const PREVIEW_PAGE_SIZE = 200
/** 确认门预览区最多列出的路径条数（完整清单在步骤 2 的虚拟滚动表里）。 */
const GATE_PREVIEW_LIMIT = 20

const { t } = useI18n()
const router = useRouter()
const route = useRoute()
const ui = useUiStore()

const message = useMessage()
ui.setMessageApi(message)
onUnmounted(() => ui.setMessageApi(null))

/** 1=选快照与目标 2=三步确认 3=执行与结果 */
const step = ref(1)
/** 当前选中快照；列表加载后默认取最新版本，之后用户可自由改选。 */
const snapshotId = ref<string | null>(null)
/** 预选源（P8 路由重排后两个入口并存，语义一致：命中即预选，不命中回落最新）：
 *  ① `?snapshot=<id>` —— 快照页「恢复此快照」老直达（PRD §5 页面 8，兼容保留）；
 *  ② 路由参数 `:id` —— `/restore/:id/select-files` 新直达（入口页行动卡 + V-1 重定向）。
 *  缺省 null。 */
function firstString(...values: unknown[]): string | null {
  for (const value of values) {
    if (typeof value === 'string' && value) {
      return value
    }
  }
  return null
}

const requestedSnapshotId = ref<string | null>(firstString(route.query.snapshot, route.params.id))
const gateShow = ref(false)
const restoring = ref(false)
const result = ref<{
  jobId: string
  restoreTarget: string
  filesDone: number | null
  summary: string | null
} | null>(null)
/** 任务是否已到达终态（用于区分「还没回报」与「回报了但没有计数」）。 */
const jobFinished = ref(false)
/** 终态是否为失败（P11：完成态圆环按此取语义色）。 */
const jobFailed = ref(false)

/** B1 文件级恢复：已选文件/目录（展示形态，如 /etc、/home/u/x）；
 *  空集合 = 整快照恢复（E19 缺省行为，回归零影响）。 */
const selectedFiles = ref<Set<string>>(new Set())

/** 恢复目标模式：inplace = 原位置（A7 起默认，对齐 Duplicati）；
 *  timestamp = 自定义目录（/restore 下时间戳子目录，零覆盖语义，E19 传统流）。 */
const targetMode = ref<'timestamp' | 'inplace'>('inplace')
const isInPlace = computed(() => targetMode.value === 'inplace')

/** A7 覆盖布尔（Duplicati --overwrite）：默认不勾 = never（跳过已存在文件）；
 *  勾选 = always（以快照内容覆盖）。仅原位流有意义。 */
const overwriteExisting = ref(false)

/** 原位恢复不可用的原因（空串 = 可用）：快照无原目录。
 *  后端有双重锁兜底，这里只是提示性限制——原因逐条对应，便于用户知道该改什么。
 *  （A0 模式收敛：原 ro 档 / 非 host 模式两分支随 host_mount_mode 与 mode 字段删除。） */
const inPlaceBlockReason = computed(() => {
  if ((selectedSnapshot.value?.paths.length ?? 0) === 0) {
    return t('restore.inplace.noPathHint')
  }
  return ''
})

const snapshotsQuery = useQuery({ queryKey: ['snapshots', 'restore-picker'], queryFn: () => listSnapshots(50) })

/** 列表按 time 降序：后端 E17 已保证最新在前，前端再兜底排序一次，首项即最新版本。 */
const snapshotItems = computed(() =>
  [...(snapshotsQuery.data.value?.items ?? [])].sort((a, b) => (b.time ?? '').localeCompare(a.time ?? '')),
)

const snapshotOptions = computed(() =>
  snapshotItems.value.map((snapshot) => ({
    // 缺陷 C：选择项时间收口为本地可读形式；无 time 时回落「无」。
    // P10（R5）：补 size（对标 Duplicati 版本下拉的 SourceSizeString），
    // 与仪表盘共用 `@/utils/format` 的口径，缺值回落「无」。
    label: `${formatDateTimeOr(snapshot.time, t('common.none'))} · ${formatBytesOr(snapshot.size, t('common.none'))} · ${snapshot.paths.join(', ') || snapshot.id.slice(0, 8)}`,
    value: snapshot.id,
  })),
)

const selectedSnapshot = computed(() => snapshotItems.value.find((snapshot) => snapshot.id === snapshotId.value) ?? null)

const inPlaceDisabled = computed(() => inPlaceBlockReason.value !== '')

/** 入口被置灰时把模式回落到时间戳流：否则用户会停在必然 403 的非法态，
 *  而提示链路要绕到后端报错才看得到原因（radio 置灰本身不可点）。
 *
 *  监听对象必须是 `selectedSnapshot` 而不是 `inPlaceDisabled`：列表加载前
 *  `inPlaceDisabled` 已恒为 true（无选中快照 → paths 为空），path-less 快照
 *  加载后依然是 true——watcher 只认「变化」，真空初态会把回落整个吞掉，
 *  用户卡在「inplace 置灰却仍选中」的非法态（A7 起的潜伏缺陷，P11 护栏
 *  用例④ 走完整流程才暴露）。改为 watch 选中快照本身：切到（含默认选中）
 *  无 paths 的快照时才回落；有 paths 时不打扰 A7 的 inplace 默认。 */
watch(selectedSnapshot, (snapshot) => {
  if (targetMode.value === 'inplace' && (snapshot?.paths.length ?? 0) === 0) {
    targetMode.value = 'timestamp'
  }
})

// ---------------------------------------------------------------- 原位目标（限快照 paths 命中的目录）

/** 已选原位目标（展示形态，如 /etc/nginx）；null = 未选。 */
const inplaceTarget = ref<string | null>(null)

/** 候选目标 = 该快照的 paths（E17 已映射为展示形态），即「快照 paths 命中的目录」。 */
const inplaceTargetOptions = computed(() =>
  (selectedSnapshot.value?.paths ?? []).map((path) => ({ label: path, value: path })),
)

/** 提交给后端的容器形态目标：`/sources` + 展示路径（双重锁白名单根，后端再校验一次）。 */
const inplaceTargetFull = computed(() => {
  const display = inplaceTarget.value
  if (!display) {
    return null
  }
  return `${INPLACE_TARGET_ROOT}${display.startsWith('/') ? '' : '/'}${display}`
})

/** 换快照后把原位目标收敛到该快照的 paths 内（跨快照的原目录不一定命中）。 */
watch(
  () => selectedSnapshot.value?.paths ?? [],
  (paths) => {
    if (inplaceTarget.value && paths.includes(inplaceTarget.value)) {
      return
    }
    inplaceTarget.value = paths[0] ?? null
  },
  { immediate: true },
)

/** 默认选中：列表就绪后，?snapshot= 预选命中则用之，否则取最新版本；
 *  当前选择仍有效（含用户改选）时不打扰；预选 id 不存在时忽略并回落默认最新。 */
watch(
  snapshotItems,
  (items) => {
    if (snapshotId.value && items.some((item) => item.id === snapshotId.value)) {
      return
    }
    const requested = requestedSnapshotId.value
    snapshotId.value = (requested ? items.find((item) => item.id === requested)?.id : undefined) ?? items[0]?.id ?? null
  },
  { immediate: true },
)

/** 换快照即清空已选文件：文件路径是快照内相对语义，跨快照不通用。 */
watch(snapshotId, () => {
  selectedFiles.value = new Set()
})

// ---------------------------------------------------------------- 文件树多选（B1，E18）
function treeLoader(path: string, cursor?: string): Promise<TreePage> {
  const id = snapshotId.value ?? ''
  return listSnapshotFiles(id, path, cursor).then((data) => ({
    items: data.items.map((item) => ({
      key: item.path,
      name: item.name,
      type: item.type,
      path: item.display_path,
    })),
    nextCursor: data.next_cursor,
  }))
}

/** 点目录名（selectDir）/文件名 = 勾选/取消勾选；展开由行首三角形负责。 */
function onFileSelect(item: TreeItem): void {
  const next = new Set(selectedFiles.value)
  if (next.has(item.path)) {
    next.delete(item.path)
  } else {
    next.add(item.path)
  }
  selectedFiles.value = next
}

const selectedFileList = computed(() => [...selectedFiles.value])
const selectedFileCount = computed(() => selectedFiles.value.size)

// ---------------------------------------------------------------- P10 文件搜索（R6 / B3 契约）

/** B3 前端契约（2026-10-23 定）：debounce ≥300ms **且**关键词 ≥2 字符才发请求——
 *  后端已有「快照+关键词」30s 缓存，但前端仍须防逐键触发。 */
const SEARCH_DEBOUNCE_MS = 300
const SEARCH_MIN_QUERY = 2

const searchInput = ref('')
const searchHits = ref<SnapshotFileOut[]>([])
const searchTotal = ref<number | null>(null)
const searching = ref(false)
let searchTimer: ReturnType<typeof setTimeout> | null = null

async function runSearch(keyword: string): Promise<void> {
  const id = snapshotId.value
  if (!id) {
    searching.value = false
    return
  }
  try {
    const data = await searchSnapshotFiles(id, keyword)
    searchHits.value = data.items
    searchTotal.value = data.total
  } catch {
    // 失败即清空结果区（错误提示走全局消息，不在此吞掉语义）
    searchHits.value = []
    searchTotal.value = null
  } finally {
    searching.value = false
  }
}

watch(searchInput, (value) => {
  if (searchTimer) {
    clearTimeout(searchTimer)
    searchTimer = null
  }
  const keyword = value.trim()
  if (keyword.length < SEARCH_MIN_QUERY) {
    // 未达触发门槛：清空结果且不显示 spinner（避免"刚输 1 个字符就转圈"）
    searchHits.value = []
    searchTotal.value = null
    searching.value = false
    return
  }
  searching.value = true
  searchTimer = setTimeout(() => void runSearch(keyword), SEARCH_DEBOUNCE_MS)
})

/** 换快照即清空搜索：命中是快照内语义，跨快照不可复用。 */
watch(snapshotId, () => {
  searchInput.value = ''
  searchHits.value = []
  searchTotal.value = null
})

/** 命中项与文件树共用同一套勾选集合：key 取 display_path（与 treeLoader 口径一致）。 */
function toggleHit(hit: SnapshotFileOut): void {
  onFileSelect({ key: hit.path, name: hit.name, type: hit.type, path: hit.display_path })
}

onUnmounted(() => {
  if (searchTimer) {
    clearTimeout(searchTimer)
    searchTimer = null
  }
})

// ---------------------------------------------------------------- E20 覆盖预览（原位流专用）

/** 字节数展示统一走 `@/utils/format`（单一实现点，仪表盘与恢复页共用同一口径）；
 *  缺值回落 `common.none`——不复用 DATETIME_FALLBACK，避免「字节数缺值」与「时间缺值」共用同一占位符。 */

const previewItems = ref<RestorePreviewItem[]>([])
const previewCursor = ref<string | null>(null)
const previewLoading = ref(false)
const previewLoadingMore = ref(false)
const previewError = ref('')
/** 整次预览的汇总值（非当前页）：overwrite_count / total_bytes / truncated。 */
const previewStats = ref<{ overwriteCount: number; totalBytes: number; truncated: boolean } | null>(null)

/** 快照、目标或模式一变，已加载的清单即刻失效（避免拿旧清单去确认覆盖）。 */
watch([snapshotId, inplaceTarget, targetMode], () => {
  previewItems.value = []
  previewCursor.value = null
  previewStats.value = null
  previewError.value = ''
})

/** reset=true 拉首页并清空；false 用 next_cursor 续拉追加。 */
async function loadPreview(reset: boolean): Promise<void> {
  const id = snapshotId.value
  const target = inplaceTargetFull.value
  if (!id || !target) {
    return
  }
  if (reset) {
    previewItems.value = []
    previewCursor.value = null
    previewStats.value = null
    previewError.value = ''
    previewLoading.value = true
  } else {
    if (previewLoadingMore.value || previewCursor.value === null) {
      return
    }
    previewLoadingMore.value = true
  }
  try {
    const data = await previewRestore(id, target, {
      limit: PREVIEW_PAGE_SIZE,
      cursor: reset ? null : previewCursor.value,
    })
    previewItems.value = previewItems.value.concat(data.items)
    previewCursor.value = data.next_cursor
    previewStats.value = {
      overwriteCount: data.overwrite_count,
      totalBytes: data.total_bytes,
      truncated: data.truncated,
    }
  } catch (error) {
    previewError.value = error instanceof Error ? error.message : String(error)
  } finally {
    previewLoading.value = false
    previewLoadingMore.value = false
  }
}

const previewSizeText = computed(() =>
  previewStats.value === null
    ? ''
    : formatBytesOr(previewStats.value.totalBytes, t('common.none')),
)

const previewColumns = computed<DataTableColumns<RestorePreviewItem>>(() => [
  {
    title: t('restore.preview.colPath'),
    key: 'display_path',
    // 虚拟滚动下必须定宽：否则列宽随内容抖动、横向滚动条宽度算不准
    width: 300,
    ellipsis: { tooltip: true },
    // S1：只展示原路径语义，容器形态 path 不面向用户
    render: (row) => row.display_path,
  },
  {
    title: t('restore.preview.colSnapshotMtime'),
    key: 'snapshot_mtime',
    width: 170,
    render: (row) => formatDateTimeOr(row.snapshot_mtime, DATETIME_FALLBACK),
  },
  {
    title: t('restore.preview.colDiskMtime'),
    key: 'disk_mtime',
    width: 170,
    render: (row) => formatDateTimeOr(row.disk_mtime, DATETIME_FALLBACK),
  },
  {
    title: t('restore.preview.colSnapshotSize'),
    key: 'snapshot_size',
    width: 110,
    render: (row) => formatBytesOr(row.snapshot_size, t('common.none')),
  },
  {
    title: t('restore.preview.colDiskSize'),
    key: 'disk_size',
    width: 110,
    render: (row) => formatBytesOr(row.disk_size, t('common.none')),
  },
])

/** 原位流必须先有预览结果才允许开门（传统流无此约束，保持既有行为）。 */
const canOpenGate = computed(() => (isInPlace.value ? previewStats.value !== null : true))

/** 确认门关键词：时间戳流保持既有 RESTORE 关键词门；原位流 A7 起**无关键词**
 *  （S7 v1.4：三重确认门 → 单次确认，对齐 Duplicati 无关键词确认）。 */
const gateKeyword = computed(() => (isInPlace.value ? undefined : 'RESTORE'))
const gateTitle = computed(() =>
  isInPlace.value ? t('restore.confirm.title') : t('restore.gateTitle'),
)
/** 原位流描述随覆盖布尔变化（never = 跳过 / always = 覆盖）。 */
const gateDesc = computed(() => {
  if (!isInPlace.value) {
    return t('restore.gateDesc')
  }
  return overwriteExisting.value ? t('restore.confirm.descOverwrite') : t('restore.confirm.descSkip')
})

/** 预览区策略行：同一份「已存在文件清单」在两种覆盖布尔下的不同归宿。 */
const previewPolicy = computed(() =>
  overwriteExisting.value ? t('restore.preview.policyOverwrite') : t('restore.preview.policySkip'),
)

function goConfirm(): void {
  if (!snapshotId.value) {
    message.warning(t('restore.needSnapshot'))
    return
  }
  if (isInPlace.value && !inplaceTarget.value) {
    message.warning(t('restore.preview.needTarget'))
    return
  }
  step.value = 2
  if (isInPlace.value) {
    // 原位流：先在步骤 2 摊开覆盖清单，用户复核后再自己开门
    void loadPreview(true)
    return
  }
  gateShow.value = true
}

/** 确认门通过后提交：paths 为展示形态；空集合 = 整快照；restore_target 由服务端返回。
 *  时间戳流请求体与 P1 逐字一致（无任何覆盖语义字段，零回归）；
 *  原位流带 overwrite_mode（never/always 布尔，A7 起无 keyword）。 */
async function onConfirmed(): Promise<void> {
  const id = snapshotId.value
  if (!id) {
    return
  }
  const target = isInPlace.value ? inplaceTargetFull.value : RESTORE_TARGET_ROOT
  if (!target) {
    return
  }
  restoring.value = true
  try {
    const data = await restoreSnapshot(
      id,
      target,
      selectedFileList.value,
      isInPlace.value ? { overwrite_mode: overwriteExisting.value ? 'always' : 'never' } : {},
    )
    result.value = { jobId: data.job_id, restoreTarget: data.restore_target, filesDone: null, summary: null }
    jobFinished.value = false
    step.value = 3
  } finally {
    restoring.value = false
  }
}

/** SSE 终态帧带回 files_done / summary：结果区展示本次恢复的文件数与（原位流）计数回报。 */
function onJobDone(payload: JobDonePayload): void {
  if (result.value) {
    result.value.filesDone = payload.files_done
    result.value.summary = payload.summary
  }
  jobFinished.value = true
  jobFailed.value = false
}

function onJobFailed(payload: JobDonePayload): void {
  if (result.value) {
    result.value.summary = payload.summary
  }
  jobFinished.value = true
  jobFailed.value = true
}

// ---------------------------------------------------------------- P11 进度页三卡（R8/R9）

/** 完成态圆环中心文案（调用方注入 i18n，圆环组件本身不依赖 i18n）。 */
const ringLabel = computed(() =>
  jobFailed.value ? t('restore.progress.failed') : t('restore.progress.done'),
)

/** 日志卡内容：复用既有 `getJobLog`（V-4：不新造数据源），与 `JobLogDrawer` 同一口径。 */
const logContent = ref('')
const logTruncated = ref(false)

async function loadLog(jobId: string): Promise<void> {
  try {
    const data = await getJobLog(jobId)
    logContent.value = data.content
    logTruncated.value = data.truncated
  } catch {
    // 日志拉不到不阻断主流程，卡片回落空态文案
    logContent.value = ''
    logTruncated.value = false
  }
}

/** 进入步骤 3 拉一次（看起始输出），到达终态再拉一次（拿到最终日志）。 */
watch(
  () => result.value?.jobId,
  (jobId) => {
    logContent.value = ''
    logTruncated.value = false
    if (jobId) {
      void loadLog(jobId)
    }
  },
  { immediate: true },
)

watch(jobFinished, (finished) => {
  if (finished && result.value) {
    void loadLog(result.value.jobId)
  }
})

/** jobs.summary 的实际覆盖/失败计数（Q4 口径：TOCTOU 窗口内以执行后实际值为准）。
 *
 * 后端 T08 尚未冻结 summary 的落盘形态，这里做「有则显示、无则不显示」的容错：
 * 只有 summary 是可解析的 JSON 对象且含数值型 overwritten/failed 时才取用，
 * 其余形态（纯文本、缺字段）一律返回 null —— 前端不展示任何数字，绝不编造。 */
const summaryCounts = computed<{ overwritten: number | null; failed: number | null } | null>(() => {
  const raw = result.value?.summary
  if (!raw) {
    return null
  }
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    return null
  }
  if (typeof parsed !== 'object' || parsed === null) {
    return null
  }
  const record = parsed as Record<string, unknown>
  const overwritten = readCount(record['overwritten'])
  const failed = readCount(record['failed'])
  return overwritten === null && failed === null ? null : { overwritten, failed }
})

function readCount(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

const overwrittenCount = computed(() => summaryCounts.value?.overwritten ?? null)
const failedCount = computed(() => summaryCounts.value?.failed ?? null)
/** 终态已到但后端没回计数：明确说没回报，不猜数字。 */
const summaryPending = computed(
  () => isInPlace.value && jobFinished.value && summaryCounts.value === null,
)

function goJobs(): void {
  void router.push({ name: 'jobs' })
}

// ---------------------------------------------------------------- P9 向导外壳：步骤 ↔ 路由

/** 步骤 ↔ 路由名映射（P8 建路由骨架，P9 让 URL 与当前步骤真正同步）。
 *  语义映射：1=选快照/目标/文件树(select-files) 2=确认(options) 3=执行与结果(progress)。
 *  注：P10 会把步骤内容重组（options 四分区）、P11 重做 progress 三卡，
 *  届时只调整本映射与步骤内容，外壳不动。 */
const STEP_ROUTE: Record<number, string> = {
  1: 'restore-select-files',
  2: 'restore-options',
  3: 'restore-progress',
}
const ROUTE_STEP: Record<string, number> = {
  'restore-select-files': 1,
  'restore-options': 2,
  'restore-progress': 3,
}

const stepItems = computed(() => [
  { key: 1, title: t('restore.flow.stepSelectFiles') },
  { key: 2, title: t('restore.flow.stepOptions') },
  { key: 3, title: t('restore.flow.stepProgress') },
])

/** V-2 跳步校验：未满足条件者置 inactive（RestoreStepper 内鼠标与键盘双向不可达）。 */
const stepEnabled = computed<Record<number, boolean>>(() => ({
  1: true,
  2: Boolean(snapshotId.value),
  3: Boolean(result.value),
}))

/** 路由 → 步骤：首次进入、浏览器前进/后退、入口页跳转都经此。 */
watch(
  () => route.name,
  (name) => {
    const next = ROUTE_STEP[String(name)]
    if (next && next !== step.value) {
      step.value = next
    }
  },
  { immediate: true },
)

/** 步骤 → 路由：跳步与内部推进都经此，URL 始终反映当前步骤。 */
watch(step, (value) => {
  const name = STEP_ROUTE[value]
  if (name && route.name !== name) {
    void router.replace({ name, params: { id: route.params.id ?? snapshotId.value ?? '' } })
  }
})

function onStepSelect(key: number): void {
  step.value = key
}

/** 返回恢复入口页（页脚左侧动作，P10 页脚两端对齐）。 */
function goEntry(): void {
  void router.push({ name: 'restore' })
}

// ---------------------------------------------------------------- P9 布局信号（对标 isSelectFilesPage）

/** 满高：仅步骤 1——文件树需要确定高度才能内部滚动。
 *  诚实边界：BasicLayout 内容区没有 height 继承链，真实 flex 撑满需先给布局层高度
 *  （属 P14 视觉层），故这里以「文件树高度上探」落地，是当前唯一有实际效果的做法。 */
const isFillHeight = computed(() => step.value === 1)
const FILE_TREE_HEIGHT_FILL = 520
const FILE_TREE_HEIGHT_DEFAULT = 300
const treeHeight = computed(() => (isFillHeight.value ? FILE_TREE_HEIGHT_FILL : FILE_TREE_HEIGHT_DEFAULT))

// ---------------------------------------------------------------- P9 离开确认（R10）

/** 脏判定：已选快照/文件但未走完，或作业未到终态（走到终态即放行）。 */
const leaveDirty = computed(() => {
  if (step.value === 3) {
    return !jobFinished.value
  }
  return Boolean(snapshotId.value) || selectedFiles.value.size > 0
})

/** 路由级离开确认（侧栏导航、浏览器后退均覆盖）。
 *  沿用 `TaskWizard` 的 window.confirm 模式（E2 离开保护）：不新造弹窗组件，
 *  也不打断 naive 的消息队列。 */
onBeforeRouteLeave((to) => {
  // 向导**内部**步骤切换（跳步 / 内部推进写回 URL）不算「离开」，直接放行——
  // 否则点步骤条跳一步就弹一次「确定离开吗」，是误报。
  if (ROUTE_STEP[String(to.name)]) {
    return true
  }
  if (!leaveDirty.value) {
    return true
  }
  return window.confirm(t('restore.flow.leaveConfirm'))
})

/** 关闭标签页/刷新：浏览器只认 preventDefault + returnValue，文案由浏览器提供。 */
function onBeforeUnload(event: BeforeUnloadEvent): void {
  if (!leaveDirty.value) {
    return
  }
  event.preventDefault()
  event.returnValue = ''
}

onMounted(() => window.addEventListener('beforeunload', onBeforeUnload))
onUnmounted(() => window.removeEventListener('beforeunload', onBeforeUnload))
</script>

<template>
  <n-space vertical :size="16" class="restore-flow" :class="{ 'restore-flow--fill': isFillHeight }">
    <n-card :title="t('nav.restore')" size="small">
      <RestoreStepper
        :steps="stepItems"
        :current="step"
        :enabled="stepEnabled"
        :aria-label="t('restore.flow.stepsAria')"
        @select="onStepSelect"
      />

      <n-space vertical :size="14" class="restore__body">
        <!-- 步骤 1：选快照 + 目标模式 + 文件多选 -->
        <template v-if="step === 1">
          <n-select
            v-model:value="snapshotId"
            :options="snapshotOptions"
            :loading="snapshotsQuery.isFetching.value"
            :placeholder="t('restore.pickPlaceholder')"
            filterable
          />
          <n-alert v-if="snapshotsQuery.error.value" type="error" :show-icon="false">
            {{ String(snapshotsQuery.error.value) }}
          </n-alert>

          <!-- P10 步骤重组：目标模式与目标目录已移至 options（步骤 2）的「恢复到哪」分区，
               覆盖策略移至「已存在文件」分区——步骤 1 只负责「选快照 + 选文件」，
               对齐 Duplicati 的 select-files / options 划分。 -->

          <!-- B1：文件级多选（可选）；不选 = 整快照 -->
          <n-space vertical :size="6">
            <n-text strong>{{ t('restore.filesTitle') }}</n-text>
            <n-text depth="3">{{ t('restore.filesHint') }}</n-text>

            <!-- P10 搜索框（R6 / B3 契约）：debounce 300ms + 关键词 ≥2 字符才发请求；
                 命中靠服务端过滤（V-3），前端绝不拉全量再过滤。 -->
            <n-input
              v-model:value="searchInput"
              class="restore__search"
              :placeholder="t('restore.search.placeholder')"
              clearable
              size="small"
            >
              <template #suffix>
                <n-spin v-if="searching" size="small" />
              </template>
            </n-input>
            <n-text v-if="searchTotal !== null" depth="2">
              {{ t('restore.search.resultCount', { n: searchTotal }) }}
            </n-text>
            <n-space v-if="searchHits.length" vertical :size="4">
              <n-text depth="3">{{ t('restore.search.hint') }}</n-text>
              <div
                v-for="hit in searchHits"
                :key="hit.path"
                class="restore__hit"
                :class="{ 'is-selected': selectedFiles.has(hit.display_path) }"
                @click="toggleHit(hit)"
              >
                {{ hit.display_path }}
              </div>
            </n-space>

            <VirtualTree
              v-if="snapshotId"
              :key="`files-${snapshotId}`"
              root-path="/"
              :load-children="treeLoader"
              :height="treeHeight"
              select-dir
              :selected-paths="selectedFiles"
              @select="onFileSelect"
            />
            <n-text v-if="selectedFileCount > 0" depth="2">
              {{ t('restore.filesSelected', { n: selectedFileCount }) }}
            </n-text>
            <n-text v-else depth="3">{{ t('restore.filesWhole') }}</n-text>
          </n-space>

          <!-- P10 页脚两端对齐（对标 Duplicati 向导页脚）：左返回入口 / 右下一步 -->
          <n-space justify="space-between" align="center">
            <n-button quaternary @click="goEntry">{{ t('restore.flow.backToEntry') }}</n-button>
            <n-button type="primary" :disabled="!snapshotId" @click="goConfirm">
              {{ t('confirm.next') }}
            </n-button>
          </n-space>
        </template>

        <!-- 步骤 2：options —— 分区一「恢复到哪」+ 分区二「已存在文件」（P10 重组）。
             注：Duplicati 的 options 是**四**分区（恢复到哪 / 已存在文件 / Metadata / 高级）；
             本项目后端 RestoreRequest 仅 snapshot_id / target / paths / overwrite_mode 四个字段、
             restic 层无 metadata 参数 → Metadata 与「高级」两分区**无后端支撑**，
             故不做占位死控件（待后端加契约后再补），此处只落地有后端支撑的两分区。 -->
        <template v-else-if="step === 2">
          <!-- 分区一：恢复到哪 -->
          <n-space vertical :size="6">
            <n-text strong>{{ t('restore.options.whereTitle') }}</n-text>
            <n-radio-group v-model:value="targetMode" size="small">
              <!-- A7：原位置为默认选项（对齐 Duplicati）；自定义目录 = 时间戳子目录零覆盖流 -->
              <n-radio-button value="inplace" :disabled="inPlaceDisabled">
                {{ t('restore.targetInPlace') }}
              </n-radio-button>
              <n-radio-button value="timestamp">{{ t('restore.targetTimestamp') }}</n-radio-button>
            </n-radio-group>
            <n-text depth="3">
              {{ isInPlace ? t('restore.targetInPlaceNote') : t('restore.targetTimestampNote') }}
            </n-text>
            <!-- 原位恢复不可用时置灰 + 原因（后端双重锁兜底，前端只做提示性限制） -->
            <n-alert v-if="inPlaceDisabled" type="warning" :show-icon="false">
              <n-text depth="3">{{ inPlaceBlockReason }}</n-text>
            </n-alert>
            <!-- 原位目标：限该快照 paths 命中的目录（后端双重锁兜底，前端只做提示性限制） -->
            <template v-if="isInPlace">
              <n-text strong>{{ t('restore.inplace.targetTitle') }}</n-text>
              <n-select
                v-model:value="inplaceTarget"
                :options="inplaceTargetOptions"
                :placeholder="t('restore.inplace.targetPlaceholder')"
              />
              <n-text depth="3">{{ t('restore.inplace.targetHint') }}</n-text>
            </template>
          </n-space>

          <!-- 分区二：已存在文件（覆盖策略 + E20 预览清单） -->
          <n-space vertical :size="6">
            <n-text strong>{{ t('restore.options.existingTitle') }}</n-text>
            <template v-if="isInPlace">
              <!-- A7 覆盖布尔（Duplicati --overwrite）：默认不勾 = never 跳过已存在文件 -->
              <n-checkbox v-model:checked="overwriteExisting">{{ t('restore.overwrite.label') }}</n-checkbox>
              <n-text depth="3">{{ t('restore.overwrite.hint') }}</n-text>
            </template>
            <n-text v-else depth="3">{{ t('restore.options.timestampNoOverwrite') }}</n-text>
          </n-space>

          <template v-if="isInPlace">
            <n-space vertical :size="8">
              <n-space align="center" :size="10">
                <n-text strong>{{ t('restore.preview.title') }}</n-text>
                <n-button size="tiny" quaternary :loading="previewLoading" @click="loadPreview(true)">
                  {{ t('restore.preview.reload') }}
                </n-button>
              </n-space>
              <n-text v-if="previewStats" depth="2">
                {{ t('restore.preview.overwriteCount', { n: previewStats.overwriteCount }) }}
                · {{ t('restore.preview.totalBytes', { size: previewSizeText }) }}
              </n-text>
              <n-text v-if="previewStats" depth="3">{{ previewPolicy }}</n-text>
              <n-alert v-if="previewStats?.truncated" type="warning" :show-icon="false">
                {{ t('restore.preview.truncated') }}
              </n-alert>
              <n-alert v-if="previewError" type="error" :show-icon="false">{{ previewError }}</n-alert>
              <!-- 大列表纪律：虚拟滚动 + 固定行高；游标分页靠下方「加载更多」续拉 -->
              <n-data-table
                v-if="previewItems.length > 0"
                size="small"
                :columns="previewColumns"
                :data="previewItems"
                :max-height="320"
                :loading="previewLoading"
                virtual-scroll
                :row-key="(row: RestorePreviewItem) => row.path"
              />
              <n-text v-else-if="!previewLoading && !previewError" depth="3">
                {{ t('restore.preview.empty') }}
              </n-text>
              <n-button
                v-if="previewCursor"
                size="tiny"
                quaternary
                :loading="previewLoadingMore"
                @click="loadPreview(false)"
              >
                {{ t('restore.preview.loadMore') }}
              </n-button>
            </n-space>
          </template>
          <n-text v-else depth="3">{{ t('restore.stepConfirm') }}</n-text>
          <!-- P10 页脚两端对齐：左上一步 / 右开门 -->
          <n-space justify="space-between" align="center">
            <n-button quaternary @click="step = 1">{{ t('confirm.back') }}</n-button>
            <n-button type="primary" :disabled="!canOpenGate" @click="gateShow = true">
              {{ t('confirm.next') }}
            </n-button>
          </n-space>
        </template>

        <!-- 步骤 3：执行结果（P11 三卡 + 完成态圆环）。
             注：Duplicati 的 progress 是「进度 / 通知 / 日志」三卡；本项目**无通知记录
             数据源**（`api/notify.ts` 只有配置读写，通知页亦只消费配置），故第二卡以
             **结果摘要**落地——复用既有 result / jobs.summary，V-4「不新造数据源」
             的实质仍被遵守（不为此新增端点）。 -->
        <template v-else>
          <n-alert type="success" :show-icon="false">{{ t('restore.submitted') }}</n-alert>
          <n-grid :cols="'1 m:2'" responsive="screen" :x-gap="12" :y-gap="12">
            <!-- 卡一：进度 + 完成态圆环 -->
            <n-gi>
              <n-card size="small" :title="t('restore.progress.title')">
                <n-space align="center" :size="12">
                  <RestoreProgressRing
                    v-if="jobFinished"
                    :percent="100"
                    :status="jobFailed ? 'error' : 'success'"
                    :label="ringLabel"
                  />
                  <n-space vertical :size="4">
                    <n-text>
                      {{ t('restore.restoreTarget') }}：<n-tag size="small">{{ result?.restoreTarget }}</n-tag>
                    </n-text>
                    <n-text v-if="result?.filesDone !== null && result?.filesDone !== undefined" depth="3">
                      {{ t('job.filesOnly', { done: result.filesDone }) }}
                    </n-text>
                    <n-text depth="3">
                      {{ isInPlace ? t('restore.inplace.runNote') : t('restore.targetNote') }}
                    </n-text>
                  </n-space>
                </n-space>
                <JobProgress
                  v-if="result"
                  :job-id="result.jobId"
                  :title="result.jobId"
                  @done="onJobDone"
                  @failed="onJobFailed"
                />
              </n-card>
            </n-gi>

            <!-- 卡二：结果摘要（替代无数据源的通知卡） -->
            <n-gi>
              <n-card size="small" :title="t('restore.progress.summaryTitle')">
                <n-space vertical :size="4">
                  <n-text v-if="jobFailed" type="error">{{ t('restore.progress.failedHint') }}</n-text>
                  <n-text v-else-if="jobFinished">{{ t('restore.progress.finished') }}</n-text>
                  <n-text v-else depth="3">{{ t('restore.progress.running') }}</n-text>
                  <!-- 原位流：任务回报的实际覆盖/失败计数（后端未回填则不显示，不编造数字） -->
                  <template v-if="isInPlace">
                    <n-text v-if="overwrittenCount !== null" depth="2">
                      {{ t('restore.summary.overwritten', { n: overwrittenCount }) }}
                    </n-text>
                    <n-text v-if="failedCount !== null" depth="2">
                      {{ t('restore.summary.failed', { n: failedCount }) }}
                    </n-text>
                    <n-text v-if="summaryPending" depth="3">{{ t('restore.summary.pending') }}</n-text>
                  </template>
                </n-space>
              </n-card>
            </n-gi>

            <!-- 卡三：任务日志（复用既有 getJobLog，与 JobLogDrawer 同一口径） -->
            <n-gi span="1 m:2">
              <n-card size="small" :title="t('restore.progress.logTitle')">
                <n-space vertical :size="6">
                  <n-log v-if="logContent" :log="logContent" :rows="12" trim />
                  <n-text v-else depth="3">{{ t('restore.progress.logEmpty') }}</n-text>
                  <n-text v-if="logTruncated" depth="3">{{ t('restore.progress.logTruncated') }}</n-text>
                </n-space>
              </n-card>
            </n-gi>
          </n-grid>
          <n-space justify="end">
            <n-button secondary @click="goJobs">{{ t('restore.goJobs') }}</n-button>
          </n-space>
        </template>
      </n-space>
    </n-card>

    <!-- 确认门：时间戳流 = 预览 → 输入 RESTORE → 红色二次确认；
         原位流（A7）= 预览 → 红色二次确认（无关键词，对齐 Duplicati） -->
    <ConfirmGate
      v-model:show="gateShow"
      :title="gateTitle"
      :keyword="gateKeyword"
      :description="gateDesc"
      :confirm-label="isInPlace ? t('restore.confirm.confirmLabel') : undefined"
      :loading="restoring"
      @confirm="onConfirmed"
    >
      <template #preview>
        <n-space vertical :size="4">
          <!-- 缺陷 C：确认门快照时间同样收口；无 time 时回落快照 id（保持原兜底语义） -->
          <n-text>{{ t('restore.gateSnapshot') }}：{{ formatDateTimeOr(selectedSnapshot?.time, selectedSnapshot ? DATETIME_FALLBACK : (snapshotId ?? DATETIME_FALLBACK)) }}</n-text>
          <n-text>{{ t('restore.gatePaths') }}：{{ selectedSnapshot?.paths.join(', ') }}</n-text>
          <n-text>{{ t('restore.gateTarget') }}：{{ isInPlace ? inplaceTargetFull : RESTORE_TARGET_ROOT }}</n-text>
          <!-- 原位流：停止服务提示 + 已存在文件汇总 + 策略行 + 清单摘要（完整清单在步骤 2） -->
          <template v-if="isInPlace">
            <n-alert type="error" :show-icon="false">{{ t('restore.stop_services') }}</n-alert>
            <template v-if="previewStats">
              <n-text strong>
                {{ t('restore.confirm.overwriteCount', { n: previewStats.overwriteCount }) }}
              </n-text>
              <n-text depth="3">
                {{ t('restore.confirm.totalBytes', { size: previewSizeText }) }}
              </n-text>
              <n-text depth="3">{{ previewPolicy }}</n-text>
            </template>
            <template v-if="previewItems.length > 0">
              <n-text strong>
                {{ t('restore.confirm.previewList', { n: GATE_PREVIEW_LIMIT, m: previewStats?.overwriteCount ?? previewItems.length }) }}
              </n-text>
              <n-text
                v-for="item in previewItems.slice(0, GATE_PREVIEW_LIMIT)"
                :key="item.path"
                depth="3"
                class="restore__gate-file"
              >
                {{ item.display_path }}
              </n-text>
            </template>
          </template>
          <!-- B1：已选文件清单（前 N 条 + 共 M 条）；空 = 整快照 -->
          <template v-if="selectedFileCount > 0">
            <n-text strong>{{ t('restore.gateFiles', { n: GATE_PREVIEW_LIMIT, m: selectedFileCount }) }}</n-text>
            <n-text
              v-for="path in selectedFileList.slice(0, GATE_PREVIEW_LIMIT)"
              :key="path"
              depth="3"
              class="restore__gate-file"
            >
              {{ path }}
            </n-text>
          </template>
          <n-text v-else depth="3">{{ t('restore.filesWhole') }}</n-text>
        </n-space>
      </template>
    </ConfirmGate>
  </n-space>
</template>

<style scoped>
.restore__body {
  margin-top: 16px;
}

/* P9 满高：步骤 1 的主体给到更大可用高度（文件树高度同步上探，见 treeHeight）。
   等 P14 给布局层高度链后，这里可换成真实 flex 撑满（见脚本处注释）。 */
.restore-flow--fill .restore__body {
  min-height: 60vh;
}

.restore__gate-file {
  display: block;
  font-size: 12px;
}

/* P10 搜索命中项：点击即切换勾选（与文件树共用 selectedFiles 集合）；
   选中态加粗，hover 用主题 action 色（不硬编码）。 */
.restore__hit {
  padding: 4px 8px;
  border-radius: 8px;
  font-size: 12px;
  cursor: pointer;
  word-break: break-all;
}

.restore__hit:hover {
  background: var(--n-action-color, rgba(128, 128, 128, 0.12));
}

.restore__hit.is-selected {
  font-weight: 600;
}
</style>
