<script setup lang="ts">
import { useQuery } from '@tanstack/vue-query'
import {
  NAlert,
  NButton,
  NCard,
  NCollapse,
  NCollapseItem,
  NDataTable,
  NDrawer,
  NDrawerContent,
  NEmpty,
  NIcon,
  NInput,
  NInputNumber,
  NPagination,
  NSelect,
  NSpace,
  NSwitch,
  NTag,
  NText,
  useMessage,
  type DataTableColumns,
} from 'naive-ui'
import { computed, h, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { useMediaQuery } from '@vueuse/core'
import { getBackupTask, listBackupTasks } from '@/api/backupTasks'
import { isAppError } from '@/api/http'
import { createJob } from '@/api/jobs'
import { listSnapshotFiles, listSnapshots, previewForget, tagSnapshots } from '@/api/snapshots'
import type {
  ForgetPreviewResponse,
  ForgetPreviewSnapshot,
  PrunePreviewResponse,
  SnapshotOut,
  TagChanged,
  TagSnapshotsResponse,
} from '@/api/types'
import { previewPrune } from '@/api/prune'
import { IconEdit, IconRestore, IconSearch, IconServer, IconUpload } from '@/components/icons/icons'
import FilterBar from '@/components/FilterBar.vue'
import ConfirmGate from '@/components/ConfirmGate.vue'
import VirtualTree, { type TreePage } from '@/components/VirtualTree.vue'
import RestorePanel from '@/pages/snapshots/restore/panel.vue'
import { formatDateTimeOr } from '@/utils/datetime'
import { formatBytes, formatBytesOr } from '@/utils/format'

const PAGE_SIZE = 20
const { t } = useI18n()
const router = useRouter()
const route = useRoute()
const message = useMessage()

const page = ref(1)
/** R-026-10：按任务标签筛选（E17 ?tag=；空 = 全部）。 */
const tagFilter = ref<string | null>(null)
/** R-023 多主机视图：按快照主机标识筛选（E17 ?host=；空 = 全部）。 */
const hostFilter = ref<string | null>(null)

/** 筛选草稿值（对齐任务历史页）：只有点「应用」才落到查询参数。 */
const draftTag = ref<string | null>(null)
const draftHost = ref<string | null>(null)

/** 应用筛选：落到查询参数并回到第 1 页（否则深页切筛选沿用旧 offset → 空列表误导）。 */
function applyFilters(): void {
  tagFilter.value = draftTag.value
  hostFilter.value = draftHost.value
  page.value = 1
}

/** 重置：清空草稿并立即应用（与任务历史页「重置」同语义）。 */
function resetFilters(): void {
  draftTag.value = null
  draftHost.value = null
  applyFilters()
}

/** 标签选项：E34 任务列表 {id,name,tag} + 当前页快照中出现的历史遗留 tag。 */
const tasksQuery = useQuery({
  queryKey: ['backup-tasks', 'tag-options'],
  queryFn: () => listBackupTasks(50),
})

/** 归档仓库只读视图（删除保护方案 D13）：`?repo=<id>` 覆盖全局 X-Repo-Id。
 *  归档仓库不在切换器里、也不在 /api/repos 列表里，只能用这个 query 参数定位；
 *  该视图**只读**——恢复入口会明确拒绝（否则会误恢复当前仓库的快照）。 */
const repoOverride = computed(() => (typeof route.query.repo === 'string' ? route.query.repo : ''))

const snapshotsQuery = useQuery({
  queryKey: ['snapshots', page, tagFilter, hostFilter, repoOverride],
  queryFn: () =>
    listSnapshots(
      PAGE_SIZE,
      (page.value - 1) * PAGE_SIZE,
      tagFilter.value ?? undefined,
      hostFilter.value ?? undefined,
      repoOverride.value || undefined,
    ),
  placeholderData: (previous) => previous,
})

const snapshots = computed(() => snapshotsQuery.data.value?.items ?? [])
const total = computed(() => snapshotsQuery.data.value?.total ?? 0)

/** 历史遗留 tag（无对应任务）累积归并：只在见到时并入、**永不移除** ——
 *  原实现从「当前已筛选的快照页」归并，选中某 tag 后其他历史 tag 会从下拉消失
 *  （过滤后本页不再含它们），用户切不回去；累积集合杜绝该自噬。 */
const legacyTags = ref<string[]>([])
watch(
  snapshots,
  (list) => {
    for (const snapshot of list) {
      for (const tag of snapshot.tags) {
        if (!legacyTags.value.includes(tag)) {
          legacyTags.value.push(tag)
        }
      }
    }
  },
  { immediate: true },
)

const tagOptions = computed(() => {
  const options = [{ label: t('snapshot.allTags'), value: '' }]
  for (const task of tasksQuery.data.value?.items ?? []) {
    options.push({ label: `${task.name}（${task.tag}）`, value: task.tag })
  }
  // 历史遗留 tag（无对应任务）：来自累积集合（筛选后不缩水），去重
  const known = new Set(options.map((option) => option.value))
  for (const tag of legacyTags.value) {
    if (!known.has(tag)) {
      known.add(tag)
      options.push({ label: tag, value: tag })
    }
  }
  return options
})

/** 主机选项（R-023 多主机视图）：累积归并 + 全部主机兜底。
 *  与 tag 同款缺陷原样存在过——原实现从「当前已筛选的快照页」归并，
 *  选中某主机后其它主机从下拉消失（本页不再含它们），用户切不回去。 */
const knownHosts = ref<string[]>([])
watch(
  snapshots,
  (list) => {
    for (const snapshot of list) {
      const host = snapshot.host
      if (host && !knownHosts.value.includes(host)) {
        knownHosts.value.push(host)
      }
    }
  },
  { immediate: true },
)

const hostOptions = computed(() => {
  const options = [{ label: t('snapshot.allHosts'), value: '' }]
  for (const host of knownHosts.value) {
    options.push({ label: host, value: host })
  }
  return options
})

/** 分组头（筛选态摘要）：任务名（未知标签兜底）· 快照数 = total · 最新时间 = 首条 time。 */
const groupHeaderText = computed(() => {
  if (!tagFilter.value) {
    return ''
  }
  const task = (tasksQuery.data.value?.items ?? []).find((item) => item.tag === tagFilter.value)
  const name = task?.name ?? t('snapshot.unknownTag')
  const latest = formatDateTimeOr(snapshots.value[0]?.time ?? null, t('common.none'))
  return t('snapshot.groupHeader', { name, n: total.value, time: latest })
})

// ---------------------------------------------------------------- 文件树抽屉
const treeShow = ref(false)
const activeSnapshot = ref<SnapshotOut | null>(null)
/** E18 输出上限截断标记：任一层命中即在抽屉顶部明示，避免「看着像全的」误判。 */
const treeTruncated = ref(false)

function openTree(snapshot: SnapshotOut): void {
  activeSnapshot.value = snapshot
  treeTruncated.value = false
  treeShow.value = true
}

// ---------------------------------------------------------------- 恢复流程抽屉（2026-10-28 并入快照页）
// 原 `/restore` 入页与三步向导的独立路由已整体删除：三步流程组件（select-files /
// options / progress）原样复用，容器换成快照页右侧宽抽屉，由**列表行内「恢复」**与
// **文件树抽屉「恢复此快照」**两个入口唤醒。深链 `?restore=<id>` 同时是旧 `/restore*`
// 地址的转址落点（见 router/index.ts 的两条 redirect）。

const restoreShow = ref(false)
/** 打开抽屉时的预选快照 id；null = 面板回落最新版本。 */
const restoreSeedId = ref<string | null>(null)
const restorePanelRef = ref<InstanceType<typeof RestorePanel> | null>(null)
/** 抽屉宽度：桌面 1080（三步流程本身要 1000px 满宽列），移动端全屏。 */
const isMobile = useMediaQuery('(max-width: 959px)')
const restoreDrawerWidth = computed(() => (isMobile.value ? '100%' : '1080px'))

function openRestore(snapshotId: string): void {
  // 归档仓库视图只读：恢复会落到「当前仓库」，误操作代价太大 → 明确拒绝并指路
  if (repoOverride.value) {
    message.warning(t('archive.readOnlyRestore'))
    return
  }
  restoreSeedId.value = snapshotId
  restoreShow.value = true
  // 从文件树抽屉点「恢复此快照」时先收起它，避免两层抽屉叠遮罩
  treeShow.value = false
  if (route.query.restore !== snapshotId) {
    void router.replace({ name: 'snapshots', query: { ...route.query, restore: snapshotId } })
  }
}

/** 关闭（遮罩 / ESC / 右上角 X / 面板内「关闭」都汇到此处）：脏态先确认。
 *  脏判定在面板内部（只有面板知道自己选了什么），这里只消费 `isDirty()`。 */
function requestCloseRestore(): void {
  if (restorePanelRef.value?.isDirty() && !window.confirm(t('restore.flow.leaveConfirm'))) {
    return
  }
  restoreShow.value = false
  restoreSeedId.value = null
  if (route.query.restore) {
    const query = { ...route.query }
    delete query.restore
    void router.replace({ name: 'snapshots', query })
  }
}

/** n-drawer 的 show 统一经此：mask / ESC / X 都会触发 update:show(false)。 */
function onRestoreShowUpdate(value: boolean): void {
  if (value) {
    restoreShow.value = true
    return
  }
  requestCloseRestore()
}

/** 深链落点：`?restore=<id>`（含旧 `/restore*` 转址）自动开抽屉并预选。 */
watch(
  () => route.query.restore,
  (value) => {
    if (typeof value === 'string' && value) {
      restoreSeedId.value = value
      restoreShow.value = true
    }
  },
  { immediate: true },
)

/** 「恢复此快照」（文件树抽屉内）：打开恢复面板（内部会收起文件树抽屉）。 */
function restoreActiveSnapshot(): void {
  const snapshotId = activeSnapshot.value?.id
  if (!snapshotId) {
    return
  }
  openRestore(snapshotId)
}

/** 配置恢复入口（原恢复入口页「从配置恢复」卡）：复用设置页「配置导入导出」分区。 */
function goSettingsTransfer(): void {
  void router.push({ path: '/settings', hash: '#sec-transfer' })
}

/** 其他仓库恢复入口（原入口页「从其他仓库恢复」卡）：先换激活仓库，回本页再选快照。 */
function goRepo(): void {
  void router.push({ name: 'repo' })
}

function treeLoader(path: string, cursor?: string): Promise<TreePage> {
  const snapshotId = activeSnapshot.value?.id ?? ''
  return listSnapshotFiles(snapshotId, path, cursor, 200, repoOverride.value || undefined).then((data) => {
    if (data.truncated) {
      treeTruncated.value = true
    }
    return {
      items: data.items.map((item) => ({
        key: item.path,
        name: item.name,
        type: item.type,
        path: item.display_path,
        // 浏览页核心价值：E18 已返回 size/mtime，原实现整段丢弃
        meta: fileMeta(item.size, item.mtime),
      })),
      nextCursor: data.next_cursor,
    }
  })
}

/** 行末元信息：size（1024 进制）+ 修改时间，二者皆空则不渲染 meta 区。 */
function fileMeta(size: number | null, mtime: string | null): string | undefined {
  const parts: string[] = []
  if (size !== null && size !== undefined) {
    parts.push(formatBytes(size))
  }
  if (mtime) {
    const text = formatDateTimeOr(mtime, '')
    if (text) {
      parts.push(text)
    }
  }
  return parts.length > 0 ? parts.join(' · ') : undefined
}

/** 从快照 tags 中取 task-<id> 反查任务名（任务已删除时回退显示原始 tag，无任务标签显示 —）。 */
function taskNameForRow(row: SnapshotOut): string {
  const taskTag = row.tags.find((tag) => /^task-\d+$/.test(tag))
  if (!taskTag) return '—'
  const task = (tasksQuery.data.value?.items ?? []).find((item) => item.tag === taskTag)
  return task?.name ?? taskTag
}

const columns = computed<DataTableColumns<SnapshotOut>>(() => [
  {
    title: t('snap.task'),
    key: 'task',
    width: 180,
    render: (row) => taskNameForRow(row),
  },
  {
    title: t('snap.id'),
    key: 'id',
    width: 120,
    render: (row) => row.id.slice(0, 8),
  },
  {
    title: t('snap.time'),
    key: 'time',
    width: 180,
    // 缺陷 C：ISO 原始串（含纳秒/UTC 偏移）收口为本地可读时间，空/解析失败回落 —
    render: (row) => formatDateTimeOr(row.time, '—'),
  },
  {
    title: t('snap.host'),
    key: 'host',
    width: 110,
    render: (row) => row.host ?? '—',
  },
  {
    title: t('snap.tags'),
    key: 'tags',
    render: (row) =>
      row.tags.length === 0
        ? '—'
        : h(NSpace, { size: 4 }, () => row.tags.map((tag) => h(NTag, { size: 'tiny', round: true }, () => tag))),
  },
  {
    title: t('snap.paths'),
    key: 'paths',
    render: (row) => (row.paths.length === 0 ? '—' : row.paths.join(', ')),
  },
  {
    // E17 成本口径：size 恒为 null，优雅显示为 —
    // P5 后续修复（原 90px → 110px）：td padding 8→9 后内容宽 74→72px，9 位数字
    // （约 72.9px）被换行成两行、行高 40→61；加宽后内容宽 92px 容 11 位数字，
    // 并列级 ellipsis（nowrap + 溢出省略）兜底防回归。
    title: t('snap.size'),
    key: 'size',
    width: 110,
    ellipsis: true,
    // 与恢复向导版本下拉同口径（utils/format 单一实现点），不再裸显字节数
    render: (row) => formatBytesOr(row.size, '—'),
  },
  {
    title: '',
    key: 'actions',
    // 110 → 190：2026-10-28 恢复流程并入本页，行内新增「恢复」按钮。
    // 两个 tiny 按钮并排后宽度不足会被挤成两行、行高 40→61，故必须同步加宽。
    width: 190,
    render: (row) =>
      h(NSpace, { size: 8, wrap: false, justify: 'end' }, () => [
        h(
          NButton,
          { size: 'tiny', secondary: true, onClick: () => openTree(row) },
          () => t('snap.browse'),
        ),
        h(
          NButton,
          { size: 'tiny', type: 'primary', secondary: true, onClick: () => openRestore(row.id) },
          () => t('snap.restore'),
        ),
      ]),
  },
])

const errorText = computed(() => {
  const error = snapshotsQuery.error.value
  if (!error) {
    return ''
  }
  // AppError.message 是后端给的中文可操作原因（锁残留/凭据缺失…），
  // String(error) 会带前缀「Error: 」稀释文案
  return error instanceof Error ? error.message : String(error)
})

// ---------------------------------------------------------------- E21 forget 预览-确认流（R-016）

const forgetTag = ref<string | null>(null)
const forgetKeepDaily = ref<number | null>(null)
const forgetKeepWeekly = ref<number | null>(null)
const forgetKeepMonthly = ref<number | null>(null)
/** A2 时间线档（restic duration，如 30d）与标签档（--keep-tag，逗号分隔）；空 = 未启用。 */
const forgetKeepWithin = ref('')
const forgetKeepTagsText = ref('')
const forgetPrune = ref(true)
const forgetPreview = ref<ForgetPreviewResponse | null>(null)
const forgetShow = ref(false)
const forgetLoading = ref(false)
const forgetExecuting = ref(false)

/** 选中的 tag 匹配到任务时，回填该任务的保留档位（E34 卡片不带 keep 字段，取详情）；
 * 历史遗留 tag 无回填来源，维持手填值。A2 起时长档/标签档一并回填。 */
watch(forgetTag, async (tag) => {
  const card = (tasksQuery.data.value?.items ?? []).find((item) => item.tag === tag)
  if (!card) {
    return
  }
  try {
    const task = await getBackupTask(card.id)
    forgetKeepDaily.value = task.keep_daily
    forgetKeepWeekly.value = task.keep_weekly
    forgetKeepMonthly.value = task.keep_monthly
    forgetKeepWithin.value = task.keep_within
    forgetKeepTagsText.value = task.keep_tags.join(', ')
  } catch {
    // 回填失败不打断手填路径（AppError 已有全局提示）
  }
})

/** G3 三门并列（A2）：计数档任一非空，或时长档非空，或标签档非空 → 可预览。 */
const forgetReady = computed(
  () =>
    forgetTag.value !== null &&
    (forgetKeepDaily.value !== null ||
      forgetKeepWeekly.value !== null ||
      forgetKeepMonthly.value !== null ||
      forgetKeepWithin.value.trim() !== '' ||
      forgetKeepTagsText.value.trim() !== ''),
)

const removeColumns = computed<DataTableColumns<ForgetPreviewSnapshot>>(() => [
  { title: t('snap.id'), key: 'id', width: 110, render: (row) => row.id.slice(0, 8) },
  { title: t('snap.time'), key: 'time', width: 170, render: (row) => formatDateTimeOr(row.time, '—') },
  { title: t('snap.paths'), key: 'paths', render: (row) => row.paths.join(', ') || '—' },
])

async function runForgetPreview(): Promise<void> {
  if (!forgetTag.value) {
    return
  }
  forgetLoading.value = true
  try {
    forgetPreview.value = await previewForget({
      tag: forgetTag.value,
      keep_daily: forgetKeepDaily.value ?? undefined,
      keep_weekly: forgetKeepWeekly.value ?? undefined,
      keep_monthly: forgetKeepMonthly.value ?? undefined,
      // A2：时长档原样传（服务端校验 duration 形态）；标签档逗号分隔文本 → 数组
      keep_within: forgetKeepWithin.value.trim() || undefined,
      keep_tags: forgetKeepTagsText.value
        .split(',')
        .map((item) => item.trim())
        .filter((item) => item !== ''),
      prune: forgetPrune.value,
    })
    forgetShow.value = true
  } catch (error) {
    // AppError 已有全局错误提示；这里只兜底网络层之外的意外类型
    if (!isAppError(error)) {
      message.error(t('errors.UNKNOWN'))
    }
  } finally {
    forgetLoading.value = false
  }
}

async function confirmForget(): Promise<void> {
  const preview = forgetPreview.value
  if (!preview) {
    // 防御：正常路径不可能（门开着必有预览数据）；静默 return 会把故障表现成
    // 「点了确认没任何反应」——用户报告过的症状，必须显式报错而不是吞掉。
    message.error(t('snapshot.forget.previewLost'))
    return
  }
  forgetExecuting.value = true
  try {
    // params 为 E21 响应回显（服务端保证与预览同源），原样透传保证「预览与实际结果一致」
    const job = await createJob('forget', { params: preview.params })
    message.success(t('snapshot.forget.submitted'))
    forgetPreview.value = null
    // forget 是**异步作业**：留在快照页列表不会自己变（用户会以为没生效）。
    // 复用 A6 既有模式跳任务页 `?job=<id>`（jobs 页按 query 自动开日志抽屉），
    // 让「是否真的删了 / 失败原因」立刻可见。
    void router.push({ name: 'jobs', query: { job: job.id } })
  } catch (error) {
    if (isAppError(error)) {
      message.error(error.message)
    } else {
      message.error(t('errors.UNKNOWN'))
    }
  } finally {
    forgetExecuting.value = false
  }
}

// ---------------------------------------------------------------- R-016 独立回收（restic prune）
// 补漏 forget 未勾选 --prune 的遗留空间：预览（--dry-run 只读）→ 确认门 → 提交 prune 作业。

const prunePreview = ref<PrunePreviewResponse | null>(null)
const pruneShow = ref(false)
const pruneLoading = ref(false)
const pruneExecuting = ref(false)

async function runPrunePreview(): Promise<void> {
  pruneLoading.value = true
  try {
    prunePreview.value = await previewPrune()
    pruneShow.value = true
  } catch (error) {
    if (!isAppError(error)) {
      message.error(t('errors.UNKNOWN'))
    }
  } finally {
    pruneLoading.value = false
  }
}

async function confirmPrune(): Promise<void> {
  pruneExecuting.value = true
  try {
    // prune 无 tag/keep 概念，params 恒空；异步作业同样跳任务页跟踪（与 forget 同口径）
    const job = await createJob('prune', {})
    message.success(t('snapshot.prune.submitted'))
    prunePreview.value = null
    void router.push({ name: 'jobs', query: { job: job.id } })
  } catch (error) {
    if (isAppError(error)) {
      message.error(error.message)
    } else {
      message.error(t('errors.UNKNOWN'))
    }
  } finally {
    pruneExecuting.value = false
  }
}

// ---------------------------------------------------------------- A3 快照版本标签（restic tag --add/--remove）
const tagAddText = ref('')
const tagRemoveText = ref('')
const tagSnapshotIdsText = ref('')
const tagApplying = ref(false)
const tagResultText = ref<string | null>(null)
/** AC5：改标签会生成新快照对象，原→新 id 映射必须回显（否则用户不知旧 id 已失效）。 */
const tagChanged = ref<TagChanged[]>([])

/** 逗号/中文逗号分隔 → 去空白去空。 */
function splitTags(text: string): string[] {
  return text
    .split(/[,，]/)
    .map((item) => item.trim())
    .filter((item) => item !== '')
}

async function applyTag(): Promise<void> {
  const add = splitTags(tagAddText.value)
  const remove = splitTags(tagRemoveText.value)
  if (add.length === 0 && remove.length === 0) {
    message.warning(t('snapshot.tag.needTag'))
    return
  }
  const ids = splitTags(tagSnapshotIdsText.value)
  // 前置拦截（与后端 422 同口径，但前端先说人话）：ID 与筛选都空 = 会作用于仓库内
  // **全部**快照——这是 restic 的高危默认，服务端会拒，这里不发请求直接解释原因。
  if (ids.length === 0 && !tagFilter.value && !hostFilter.value) {
    message.warning(t('snapshot.tag.needTarget'))
    return
  }
  tagApplying.value = true
  tagResultText.value = null
  tagChanged.value = []
  try {
    const resp: TagSnapshotsResponse = await tagSnapshots({
      snapshot_ids: ids,
      add,
      remove,
      // 留空 ID 时按当前筛选条件（标签/主机）作用全部匹配快照；都空则服务端 422 拒
      tag_filter: tagFilter.value ? [tagFilter.value] : null,
      host: hostFilter.value || null,
    })
    tagResultText.value = t('snapshot.tag.success', { n: resp.changed_count })
    tagChanged.value = resp.changed
    message.success(tagResultText.value)
    void snapshotsQuery.refetch() // 刷新快照列表（标签已变）
  } catch (error) {
    if (!isAppError(error)) {
      message.error(t('errors.UNKNOWN'))
    }
  } finally {
    tagApplying.value = false
  }
}
</script>

<template>
  <n-space vertical :size="16">
    <n-alert v-if="errorText" type="error" :show-icon="false">{{ errorText }}</n-alert>

    <n-card :title="t('nav.snapshots')" size="small">
      <!-- 2026-10-28：原「恢复入口页」删除后，它的两张备选卡（从配置恢复 / 从其他仓库恢复）
           收敛为本页顶部的两个轻量入口——不再单独占一页。 -->
      <template #header-extra>
        <n-space :size="4" :wrap="false">
          <n-button size="tiny" quaternary @click="goSettingsTransfer">
            <template #icon>
              <n-icon :size="14"><IconUpload /></n-icon>
            </template>
            {{ t('snap.fromConfig') }}
          </n-button>
          <n-button size="tiny" quaternary @click="goRepo">
            <template #icon>
              <n-icon :size="14"><IconServer /></n-icon>
            </template>
            {{ t('snap.fromOtherRepo') }}
          </n-button>
        </n-space>
      </template>
      <!-- 归档仓库只读视图提示（删除保护方案 D13）：`?repo=<id>` 进来的场景。
           放在卡片内而非 n-space 直接子节点——n-space 展平子节点按索引发 key，
           多一个条件兄弟会让索引相撞（本项目既有纪律）。 -->
      <n-alert v-if="repoOverride" type="warning" :show-icon="false" style="margin-bottom: 12px">
        {{ t('snap.archivedView', { id: repoOverride }) }}
      </n-alert>
      <!-- 筛选器（共用形态层 FilterBar，与任务历史页同版式）：draft 值只在点「应用」后
           才落到查询参数，避免改一次下拉就打一次 restic。 -->
      <FilterBar :loading="snapshotsQuery.isFetching.value" @apply="applyFilters" @reset="resetFilters">
        <n-select
          v-model:value="draftTag"
          :options="tagOptions"
          size="small"
          class="snapshots__tag-select"
          :placeholder="t('snapshot.tagFilter')"
          clearable
        />
        <n-select
          v-model:value="draftHost"
          :options="hostOptions"
          size="small"
          class="snapshots__host-select"
          :placeholder="t('snapshot.hostFilter')"
          clearable
        />
      </FilterBar>

      <n-space vertical :size="12" class="snapshots__filter">
        <n-text v-if="groupHeaderText" depth="2" class="snapshots__group">
          {{ groupHeaderText }}
        </n-text>
      </n-space>


      <n-alert v-if="!snapshotsQuery.isLoading.value && snapshots.length === 0 && !errorText" type="default" :show-icon="false">
        <n-empty :description="t('snap.empty')" />
      </n-alert>
      <template v-else>
        <n-data-table
          size="small"
          :scroll-x="940"
          :columns="columns"
          :data="snapshots"
          :loading="snapshotsQuery.isFetching.value"
          :row-key="(row: SnapshotOut) => row.id"
        />
        <n-space justify="end" class="snapshots__pager">
          <n-pagination
            :page="page"
            :page-size="PAGE_SIZE"
            :item-count="total"
            @update:page="(value: number) => (page = value)"
          />
        </n-space>
      </template>
    </n-card>

    <!-- E21 forget 预览-确认流（R-016）：预览只读，实际裁剪必须过三重确认门 -->
    <n-card :title="t('snapshot.forget.title')" size="small">
      <template #header-extra>
        <n-text depth="3" class="snapshots__forget-note">{{ t('snapshot.forget.note') }}</n-text>
      </template>
      <n-space vertical :size="12">
        <n-alert type="warning" :show-icon="false">{{ t('snapshot.forget.desc') }}</n-alert>
        <n-space align="center" :size="8" class="snapshots__forget-form">
          <n-select
            v-model:value="forgetTag"
            :options="tagOptions.filter((option) => option.value !== '')"
            size="small"
            class="snapshots__forget-tag"
            :placeholder="t('snapshot.forget.tagPlaceholder')"
          />
          <n-input-number
            v-model:value="forgetKeepDaily"
            size="small"
            :min="0"
            class="snapshots__forget-num"
            :placeholder="t('snapshot.forget.keepDaily')"
          />
          <n-input-number
            v-model:value="forgetKeepWeekly"
            size="small"
            :min="0"
            class="snapshots__forget-num"
            :placeholder="t('snapshot.forget.keepWeekly')"
          />
          <n-input-number
            v-model:value="forgetKeepMonthly"
            size="small"
            :min="0"
            class="snapshots__forget-num"
            :placeholder="t('snapshot.forget.keepMonthly')"
          />
          <!-- A2：时长档（restic duration）与标签档（--keep-tag，逗号分隔） -->
          <n-input
            v-model:value="forgetKeepWithin"
            size="small"
            class="snapshots__forget-num"
            :placeholder="t('snapshot.forget.keepWithin')"
          />
          <n-input
            v-model:value="forgetKeepTagsText"
            size="small"
            class="snapshots__forget-num"
            :placeholder="t('snapshot.forget.keepTags')"
          />
          <n-space align="center" :size="8" :wrap="false">
            <n-switch v-model:value="forgetPrune" size="small" :aria-label="t('snapshot.forget.prune')" />
            <n-text depth="2" class="snapshots__forget-note">{{ t('snapshot.forget.prune') }}</n-text>
          </n-space>
          <n-text depth="3" class="snapshots__forget-note">{{ t('snapshot.forget.pruneHint') }}</n-text>
        </n-space>
        <!-- 预览按钮挪到独立行，与回收空间（prune）卡片同构（用户 2026-10-25 指令） -->
        <n-space align="center" :size="8">
          <n-button
            size="small"
            type="warning"
            secondary
            :loading="forgetLoading"
            :disabled="!forgetReady"
            @click="runForgetPreview"
          >
            <template #icon>
              <n-icon :size="16"><IconSearch /></n-icon>
            </template>
            {{ t('snapshot.forget.preview') }}
          </n-button>
          <n-text depth="3" class="snapshots__forget-note">{{ t('snapshot.forget.previewHint') }}</n-text>
        </n-space>
        <n-text v-if="!forgetReady" depth="3" class="snapshots__forget-note">
          {{ t('snapshot.forget.needKeep') }}
        </n-text>
        <!-- 分组维度只读公示：后端 group_by 恒取 restic 默认（任务表单亦无该控件），
             且拼装层只防注入、不校验维度合法性 → 不给自由输入（非法值会迟到 500）。 -->
        <n-text depth="3" class="snapshots__forget-note">
          {{ t('snapshot.forget.groupBy') }}
        </n-text>
      </n-space>
    </n-card>

    <!-- R-016 独立回收（restic prune）：补漏 forget 未勾选 --prune 的遗留空间。
         preview（--dry-run 只读）→ 确认门 → 提交 prune 异步作业（跳任务页跟踪）。 -->
    <n-card :title="t('snapshot.prune.title')" size="small">
      <template #header-extra>
        <n-text depth="3" class="snapshots__prune-note">{{ t('snapshot.prune.note') }}</n-text>
      </template>
      <n-space vertical :size="12">
        <n-alert type="warning" :show-icon="false">{{ t('snapshot.prune.desc') }}</n-alert>
        <n-space align="center" :size="8">
          <n-button
            size="small"
            type="warning"
            secondary
            :loading="pruneLoading"
            @click="runPrunePreview"
          >
            <template #icon>
              <n-icon :size="16"><IconSearch /></n-icon>
            </template>
            {{ t('snapshot.prune.preview') }}
          </n-button>
          <n-text depth="3" class="snapshots__prune-note">{{ t('snapshot.prune.previewHint') }}</n-text>
        </n-space>
      </n-space>
    </n-card>

    <!-- A3 快照版本标签：restic tag --add/--remove（打/去标签） -->
    <n-card :title="t('snapshot.tag.title')" size="small">
      <template #header-extra>
        <n-text depth="3" class="snapshots__tag-note">{{ t('snapshot.tag.note') }}</n-text>
      </template>
      <n-space vertical :size="12">
        <n-alert type="info" :show-icon="false">{{ t('snapshot.tag.desc') }}</n-alert>
        <n-space align="center" :size="8" :wrap="false" class="snapshots__tag-form">
          <n-input
            v-model:value="tagAddText"
            size="small"
            clearable
            class="snapshots__tag-input"
            :placeholder="t('snapshot.tag.addPlaceholder')"
          />
          <n-input
            v-model:value="tagRemoveText"
            size="small"
            clearable
            class="snapshots__tag-input"
            :placeholder="t('snapshot.tag.removePlaceholder')"
          />
          <n-input
            v-model:value="tagSnapshotIdsText"
            size="small"
            class="snapshots__tag-input"
            :placeholder="t('snapshot.tag.idsPlaceholder')"
          />
          <n-button
            size="small"
            type="primary"
            secondary
            :loading="tagApplying"
            :disabled="tagApplying"
            @click="applyTag"
          >
            <template #icon>
              <n-icon :size="16"><IconEdit /></n-icon>
            </template>
            {{ t('snapshot.tag.apply') }}
          </n-button>
        </n-space>
        <n-text depth="3" class="snapshots__tag-note">{{ t('snapshot.tag.targetHint') }}</n-text>
        <n-alert v-if="tagResultText" type="success" :show-icon="false">{{ tagResultText }}</n-alert>
        <!-- AC5：新旧 id 映射回显（改标签生成新快照对象，旧 id 立即失效） -->
        <n-space v-if="tagChanged.length > 0" vertical :size="4" class="snapshots__tag-changed">
          <n-text depth="2">{{ t('snapshot.tag.changedTitle') }}</n-text>
          <div v-for="pair in tagChanged" :key="pair.old_snapshot_id" class="snapshots__tag-pair">
            {{ pair.old_snapshot_id.slice(0, 8) }} → {{ pair.new_snapshot_id.slice(0, 8) }}
          </div>
        </n-space>
      </n-space>
    </n-card>

    <ConfirmGate
      v-model:show="forgetShow"
      :title="t('snapshot.forget.gateTitle')"
      keyword="FORGET"
      :description="t('snapshot.forget.gateDesc')"
      :loading="forgetExecuting"
      :confirm-label="t('snapshot.forget.confirmLabel')"
      @confirm="confirmForget"
    >
      <template #preview>
        <n-space vertical :size="8">
          <n-text>
            {{ t('snapshot.forget.summary', {
              n: forgetPreview?.remove_count ?? 0,
              m: forgetPreview?.keep_count ?? 0,
              g: forgetPreview?.group_count ?? 0,
            }) }}
          </n-text>
          <n-alert v-if="forgetPreview?.truncated" type="warning" :show-icon="false">
            {{ t('snapshot.forget.truncated') }}
          </n-alert>
          <template v-if="(forgetPreview?.remove_count ?? 0) === 0">
            <n-empty :description="t('snapshot.forget.nothing')" />
          </template>
          <template v-else>
            <n-data-table
              size="small"
              :scroll-x="560"
              :columns="removeColumns"
              :data="forgetPreview?.remove ?? []"
              :max-height="260"
              :row-key="(row: ForgetPreviewSnapshot) => row.id"
            />
          </template>
        </n-space>
      </template>
    </ConfirmGate>

    <!-- R-016 独立回收确认门（preview 只读 → keyword 确认 → 提交 prune 作业） -->
    <ConfirmGate
      v-model:show="pruneShow"
      :title="t('snapshot.prune.gateTitle')"
      keyword="PRUNE"
      :description="t('snapshot.prune.gateDesc')"
      :loading="pruneExecuting"
      :confirm-label="t('snapshot.prune.confirmLabel')"
      @confirm="confirmPrune"
    >
      <template #preview>
        <n-space vertical :size="8">
          <n-alert v-if="prunePreview?.clean" type="success" :show-icon="false">
            {{ t('snapshot.prune.clean') }}
          </n-alert>
          <n-alert v-else type="warning" :show-icon="false">
            {{ t('snapshot.prune.summary', {
              packs: prunePreview?.would_remove_packs ?? 0,
              freed: prunePreview?.freed_text ?? '—',
            }) }}
          </n-alert>
          <n-collapse v-if="(prunePreview?.lines ?? []).length > 0">
            <n-collapse-item :title="t('snapshot.prune.rawTitle')" name="raw">
              <pre class="snapshots__prune-raw">{{ (prunePreview?.lines ?? []).join('\n') }}</pre>
            </n-collapse-item>
          </n-collapse>
        </n-space>
      </template>
    </ConfirmGate>

    <!-- 文件树抽屉：懒加载 + 虚拟滚动 + 游标只透传 -->
    <n-drawer v-model:show="treeShow" :width="720" placement="right">
      <n-drawer-content :title="`${t('snap.filesTitle')} ${activeSnapshot?.id.slice(0, 8) ?? ''}`" closable>
        <n-space vertical :size="12">
          <n-alert v-if="treeTruncated" type="warning" :show-icon="false">
            {{ t('snap.filesTruncated') }}
          </n-alert>
          <n-space align="center" justify="space-between">
            <n-text depth="3">{{ activeSnapshot?.paths.join(', ') }}</n-text>
            <n-button size="small" secondary type="primary" @click="restoreActiveSnapshot">
              <template #icon>
                <n-icon :size="16"><IconRestore /></n-icon>
              </template>
              {{ t('snap.restoreThis') }}
            </n-button>
          </n-space>
          <VirtualTree v-if="treeShow" root-path="/" :load-children="treeLoader" :height="520" />
        </n-space>
      </n-drawer-content>
    </n-drawer>

    <!-- 恢复流程抽屉：三步流程组件（选文件 → 恢复选项 → 执行与结果）复用原独立页的全部实现。
         入口 = 列表行内「恢复」/ 文件树抽屉「恢复此快照」/ 深链 `?restore=<id>`（旧 `/restore*` 转址落点）。
         `v-if="restoreShow"` 让每次打开都重建面板：预选快照与步骤从 1 开始（关闭即弃状态）。 -->
    <n-drawer
      :show="restoreShow"
      :width="restoreDrawerWidth"
      placement="right"
      @update:show="onRestoreShowUpdate"
    >
      <n-drawer-content
        class="snap-restore-drawer"
        :title="t('snap.restoreDrawerTitle', { id: (restoreSeedId ?? '').slice(0, 8) })"
        closable
      >
        <RestorePanel
          v-if="restoreShow"
          ref="restorePanelRef"
          :initial-snapshot-id="restoreSeedId"
          @close="requestCloseRestore"
        />
      </n-drawer-content>
    </n-drawer>
  </n-space>
</template>

<style scoped>
.snapshots__pager {
  margin-top: 12px;
}

.snapshots__filter {
  margin-bottom: 4px;
}

/* 与任务历史页同一口径：用**固定宽度**而非 max-width——flex 容器里没有 min-width 时
   max-width 会被同行按钮挤成 0 宽（实证：加「应用/重置」后下拉整排消失）。 */
.snapshots__tag-select {
  width: 220px;
}

.snapshots__host-select {
  width: 180px;
}

.snapshots__group {
  font-size: 12px;
}


.snapshots__forget-form {
  flex-wrap: wrap;
}

.snapshots__forget-tag {
  min-width: 220px;
}

.snapshots__forget-num {
  width: 150px;
}

.snapshots__forget-note {
  font-size: 12px;
}

/* A3 新旧 id 映射：等宽对齐（tokens.ts fontFamilyMono 经全局变量），
   批量打标签时列表可能很长 → 夹高度内部滚动，不撑破卡片。 */
.snapshots__tag-changed {
  max-height: 160px;
  overflow-y: auto;
}

.snapshots__tag-pair {
  font-family: var(--font-family-mono);
  font-size: 12px;
}

/* R-016 prune 原始输出：等宽 + 限高滚动，避免长输出撑破确认门 */
.snapshots__prune-raw {
  font-family: var(--font-family-mono);
  font-size: 12px;
  margin: 0;
  max-height: 220px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
