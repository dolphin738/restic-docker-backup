<script setup lang="ts">
import { useQuery } from '@tanstack/vue-query'
import { NAlert, NSpace, NText, useMessage } from 'naive-ui'
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import { listSnapshots } from '@/api/snapshots'
import {
  INPLACE_TARGET_ROOT,
  RESTORE_TARGET_ROOT,
  previewRestore,
  restoreSnapshot,
} from '@/api/restore'
import type { JobDonePayload, RestorePreviewItem } from '@/api/types'
import ConfirmGate from '@/components/ConfirmGate.vue'
import RestoreStepper from '@/components/restore/RestoreStepper.vue'
import Options from '@/pages/restore/flow/options.vue'
import Progress from '@/pages/restore/flow/progress.vue'
import SelectFiles from '@/pages/restore/flow/select-files.vue'
import { DATETIME_FALLBACK, formatDateTimeOr } from '@/utils/datetime'
import { formatBytesOr } from '@/utils/format'
import { useRuntimeStore } from '@/stores/runtime'
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

/** 恢复到哪（P16 对齐 Duplicati options 单选语义，替原 targetMode 二态）：
 *  original = 原位置（A7 起默认，对齐 Duplicati Original location）；
 *  pickLocation = 选择位置（/restore 子树内任选，服务端仍建时间戳子目录，零覆盖语义不变）。 */
const restoreFrom = ref<'original' | 'pickLocation'>('original')
const isInPlace = computed(() => restoreFrom.value === 'original')

/** 选择位置流的目标（容器形态，restoreRoot 子树内）；服务端在其下新建
 *  `<stem>-<stamp>` 时间戳子目录（E19 零覆盖，PRD G3）。默认取运行时下发的
 *  restoreRoot（部署形态单一事实源，《源数据过滤与树双模式设计方案》§3.2）。 */
const runtime = useRuntimeStore()
const customTarget = ref(RESTORE_TARGET_ROOT)
// pinia setup store 的属性在 TS 里已解包为 string——watch 源须用 getter 形式
watch(
  () => runtime.restoreRoot,
  (root) => {
    if (root) {
      customTarget.value = root
    }
  },
  { immediate: true },
)

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

/** 入口被置灰时把模式回落到选择位置：否则用户会停在必然 403 的非法态，
 *  而提示链路要绕到后端报错才看得到原因（radio 置灰本身不可点）。
 *
 *  监听对象必须是 `selectedSnapshot` 而不是 `inPlaceDisabled`：列表加载前
 *  `inPlaceDisabled` 已恒为 true（无选中快照 → paths 为空），path-less 快照
 *  加载后依然是 true——watcher 只认「变化」，真空初态会把回落整个吞掉，
 *  用户卡在「inplace 置灰却仍选中」的非法态（A7 起的潜伏缺陷，P11 护栏
 *  用例④ 走完整流程才暴露）。改为 watch 选中快照本身：切到（含默认选中）
 *  无 paths 的快照时才回落；有 paths 时不打扰 A7 的 inplace 默认。 */
watch(selectedSnapshot, (snapshot) => {
  if (restoreFrom.value === 'original' && (snapshot?.paths.length ?? 0) === 0) {
    restoreFrom.value = 'pickLocation'
  }
})

/** 模式切换 effect（P16 复刻 Duplicati `#selectDefaultRestoreModeEffect`）：
 *  watch `restoreFrom` **数据本身**（同 P11 教训——派生布尔会因真空初态被吞）。
 *  切到「选择位置」时把 customTarget 复位到 restoreRoot：不带着上一个子目录
 *  提交（用户上次选了 /restore/a，这次重新进来应回到根，而不是复用一个可能
 *  已消失的旧子目录）。 */
watch(restoreFrom, (mode) => {
  if (mode === 'pickLocation') {
    customTarget.value = runtime.restoreRoot || RESTORE_TARGET_ROOT
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

// ---------------------------------------------------------------- 文件勾选（B1，E18；树与搜索命中项共用）

/** 点目录名（selectDir）/文件名/搜索命中项 = 勾选/取消勾选；展开由行首三角形负责。
 *  勾选集合由父级持有——确认门预览插槽（已选文件清单）与提交请求都要读。 */
function onToggleFile(path: string): void {
  const next = new Set(selectedFiles.value)
  if (next.has(path)) {
    next.delete(path)
  } else {
    next.add(path)
  }
  selectedFiles.value = next
}

const selectedFileList = computed(() => [...selectedFiles.value])
const selectedFileCount = computed(() => selectedFiles.value.size)

// ---------------------------------------------------------------- 子组件状态回写（三步骤组件拆分）

/** 快照列表错误文案：查询错误对象只在父级转一次字符串，子组件不收 unknown。 */
const snapshotsErrorText = computed(() => {
  const error = snapshotsQuery.error.value
  return error ? String(error) : null
})

function onSnapshotIdUpdate(value: string | null): void {
  snapshotId.value = value
}

function onRestoreFromUpdate(value: 'original' | 'pickLocation'): void {
  restoreFrom.value = value
}

function onCustomTargetUpdate(value: string): void {
  customTarget.value = value
}

function onInplaceTargetUpdate(value: string | null): void {
  inplaceTarget.value = value
}

function onOverwriteExistingUpdate(value: boolean): void {
  overwriteExisting.value = value
}

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
watch([snapshotId, inplaceTarget, restoreFrom], () => {
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

/** 快照/已选路径是否含 Windows 分隔符（restic 快照 paths 为绝对路径，
 *  分隔符即操作系统指纹；本项目运行态恒 Linux '/'）。 */
function hasForeignSeparator(): boolean {
  const paths =
    selectedFileList.value.length > 0 ? selectedFileList.value : (selectedSnapshot.value?.paths ?? [])
  return paths.some((path) => path.includes('\\'))
}

function goConfirm(): void {
  if (!snapshotId.value) {
    message.warning(t('restore.needSnapshot'))
    return
  }
  if (isInPlace.value && !inplaceTarget.value) {
    message.warning(t('restore.preview.needTarget'))
    return
  }
  // P16：跨 OS 确认（复刻 Duplicati submit() 前置门）——原位置恢复且快照路径
  // 含 Windows 分隔符时，先确认再进门（备份来自其它操作系统，原位置落点
  // 可能出乎意料）。沿用 R10 离开确认的 window.confirm 模式，不新造弹窗。
  if (isInPlace.value && hasForeignSeparator() && !window.confirm(t('restore.options.crossOsConfirm'))) {
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
 *  原位置流带 overwrite_mode（never/always 布尔，A7 起无 keyword）；
 *  选择位置流请求体与 P1 逐字一致（target = 自定义 /restore 子目录，服务端建
 *  时间戳子目录，无任何覆盖语义字段，零回归）。 */
async function onConfirmed(): Promise<void> {
  const id = snapshotId.value
  if (!id) {
    return
  }
  const target = isInPlace.value ? inplaceTargetFull.value : customTarget.value
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

// ---------------------------------------------------------------- 三步骤组件拆分后的父级编排说明
//
// 步骤 1（select-files）/ 步骤 2（options）/ 步骤 3（progress）均已拆为独立组件
// （2026-10-23 补做 P10/P11 遗留项）。父级保留的是**跨步骤共享**的状态与动作：
// 快照查询与默认选中、勾选集合、目标模式与预览 fetch（确认门插槽共用）、
// result 与作业终态、确认门与提交、步骤↔路由同步、离开确认。
// 各步骤私有不共享的逻辑（B3 搜索 / 日志拉取 / summary 计数解析）已随组件内聚。

function goJobs(): void {
  void router.push({ name: 'jobs' })
}

// ---------------------------------------------------------------- P9 向导外壳：步骤 ↔ 路由

/** 步骤 ↔ 路由名映射（P8 建路由骨架，P9 让 URL 与当前步骤真正同步）。
 *  语义映射：1=选快照/文件(select-files) 2=确认(options) 3=执行与结果(progress)。
 *  P10 步骤重组、P11 progress 三卡、2026-10-23 三步骤组件拆出均只改步骤内容，
 *  本映射与外壳不动。 */
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
 *  （属 P14 视觉层），故这里以「文件树高度上探」落地，是当前唯一有实际效果的做法。
 *  注：文件树具体高度值（520）已随 select-files 组件内聚，此处只保留布局类名信号。 */
const isFillHeight = computed(() => step.value === 1)

/** 满宽：步骤 1/3（Duplicati isFullWidthPage：progress/select-files → 列宽 500→1000）；
 *  步骤 2（options）保持 500px 居中列。 */
const isWidePage = computed(() => step.value !== 2)

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
  <!-- P16 外壳（D5=A）：去 n-card，改 Duplicati「sh-stepper + 居中列」——
       options 步骤 500px 居中；select-files/progress 满宽 1000px（模版 isFullWidthPage）；
       ≤960 断点列内 padding 0 32（模版 -milli 同值，项目断点惯例 §4.3）。 -->
  <div
    class="restore-flow"
    :class="{ 'restore-flow--fill': isFillHeight, 'restore-flow--wide': isWidePage }"
  >
    <RestoreStepper
      class="restore-flow__stepper"
      :steps="stepItems"
      :current="step"
      :enabled="stepEnabled"
      :aria-label="t('restore.flow.stepsAria')"
      @select="onStepSelect"
    />

    <section class="restore-flow__section">
      <n-space vertical :size="14" class="restore__body">
        <!-- 步骤 1：select-files 组件（P10/P11 遗留拆分项，2026-10-23 补做）。
             快照下拉 + B3 搜索 + 文件树多选自成组件；状态（snapshotId/selectedFiles）
             留父级，确认门预览与提交请求共用。 -->
        <SelectFiles
          v-if="step === 1"
          :snapshot-id="snapshotId"
          :snapshot-options="snapshotOptions"
          :snapshots-loading="snapshotsQuery.isFetching.value"
          :snapshots-error="snapshotsErrorText"
          :selected-files="selectedFiles"
          @update:snapshot-id="onSnapshotIdUpdate"
          @toggle-file="onToggleFile"
          @next="goConfirm"
          @back="goEntry"
        />

        <!-- 步骤 2：options 组件（P16 按 Duplicati options 重写：三折叠卡 +
             两端页脚；E20 预览并入「已存在文件」卡 D4=A）。预览 fetch 状态留父级
             （确认门插槽共用），组件经 props 读、emit 动作。 -->
        <Options
          v-else-if="step === 2"
          :restore-from="restoreFrom"
          :is-in-place="isInPlace"
          :in-place-disabled="inPlaceDisabled"
          :in-place-block-reason="inPlaceBlockReason"
          :inplace-target="inplaceTarget"
          :inplace-target-options="inplaceTargetOptions"
          :custom-target="customTarget"
          :overwrite-existing="overwriteExisting"
          :submitting="restoring"
          :can-open-gate="canOpenGate"
          :preview-items="previewItems"
          :preview-stats="previewStats"
          :preview-cursor="previewCursor"
          :preview-loading="previewLoading"
          :preview-loading-more="previewLoadingMore"
          :preview-error="previewError"
          :preview-size-text="previewSizeText"
          :preview-policy="previewPolicy"
          @update:restore-from="onRestoreFromUpdate"
          @update:inplace-target="onInplaceTargetUpdate"
          @update:custom-target="onCustomTargetUpdate"
          @update:overwrite-existing="onOverwriteExistingUpdate"
          @reload-preview="loadPreview(true)"
          @load-more-preview="loadPreview(false)"
          @back="step = 1"
          @next="gateShow = true"
        />

        <!-- 步骤 3：progress 组件（P10/P11 遗留拆分项，2026-10-23 补做）。
             三卡（进度 + 完成态圆环 / 结果摘要 / 任务日志）；日志拉取与 summary
             计数解析内聚于此（只服务本步骤），SSE 终态事件上抛父级落 result。 -->
        <Progress
          v-else
          :result="result"
          :job-finished="jobFinished"
          :job-failed="jobFailed"
          :is-in-place="isInPlace"
          @done="onJobDone"
          @failed="onJobFailed"
          @go-jobs="goJobs"
        />
      </n-space>
    </section>

    <!-- 确认门：时间戳（选择位置）流 = 预览 → 输入 RESTORE → 红色二次确认；
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
          <n-text>{{ t('restore.gateTarget') }}：{{ isInPlace ? inplaceTargetFull : customTarget }}</n-text>
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
  </div>
</template>

<style scoped>
/* P16 外壳（D5=A，Duplicati restore-flow 同款）：stepper 直接落在页面底色上，
   内容区居中列——options 500px / select-files·progress 1000px 双档。 */
.restore-flow__stepper {
  display: block;
  margin: 24px 0 16px;
}

.restore-flow__section {
  /* border-box：断点内的 32px padding 计入 500px 列宽（否则 content-box 下实测
     564px，比 Duplicati 的 section 宽 64px——P16 真机探针 p16-options-mobile.cjs 抓到） */
  box-sizing: border-box;
  max-width: 500px;
  margin: 0 auto 24px;
}

.restore-flow--wide .restore-flow__section {
  max-width: 1000px;
}

/* 移动端（项目断点惯例 ≤960，§4.3）：列内 padding 0 32（模版 -milli 同值）。
   ⚠️ 不能对 stepper 容器无条件 overflow-x:auto——滚动容器收住子元素 margin 会
   致桌面漂移（§4.3 实测教训）；RestoreStepper 列表本身 flex-wrap，窄屏换行即可。 */
@media (max-width: 960px) {
  .restore-flow__section {
    padding: 0 32px;
  }
}

.restore__body {
  margin-top: 0;
}

/* P9 满高：步骤 1 的主体给到更大可用高度（文件树高度同步上探，见 select-files 组件）。
   等 P14 给布局层高度链后，这里可换成真实 flex 撑满（见脚本处注释）。 */
.restore-flow--fill .restore__body {
  min-height: 60vh;
}

.restore__gate-file {
  display: block;
  font-size: 12px;
}
</style>
