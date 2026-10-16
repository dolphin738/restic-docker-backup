<script setup lang="ts">
import { useQuery } from '@tanstack/vue-query'
import {
  NAlert,
  NButton,
  NCard,
  NDataTable,
  NDrawer,
  NDrawerContent,
  NEmpty,
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
import { useRouter } from 'vue-router'
import { getBackupTask, listBackupTasks } from '@/api/backupTasks'
import { isAppError } from '@/api/http'
import { createJob } from '@/api/jobs'
import { listSnapshotFiles, listSnapshots, previewForget } from '@/api/snapshots'
import type { ForgetPreviewResponse, ForgetPreviewSnapshot, SnapshotOut } from '@/api/types'
import ConfirmGate from '@/components/ConfirmGate.vue'
import VirtualTree, { type TreePage } from '@/components/VirtualTree.vue'
import { formatDateTimeOr } from '@/utils/datetime'

const PAGE_SIZE = 20
const { t } = useI18n()
const router = useRouter()
const message = useMessage()

const page = ref(1)
/** R-026-10：按任务标签筛选（E17 ?tag=；空 = 全部）。 */
const tagFilter = ref<string | null>(null)
/** R-023 多主机视图：按快照主机标识筛选（E17 ?host=；空 = 全部）。 */
const hostFilter = ref<string | null>(null)

/** 标签选项：E34 任务列表 {id,name,tag} + 当前页快照中出现的历史遗留 tag。 */
const tasksQuery = useQuery({
  queryKey: ['backup-tasks', 'tag-options'],
  queryFn: () => listBackupTasks(50),
})

const snapshotsQuery = useQuery({
  queryKey: ['snapshots', page, tagFilter, hostFilter],
  queryFn: () =>
    listSnapshots(
      PAGE_SIZE,
      (page.value - 1) * PAGE_SIZE,
      tagFilter.value ?? undefined,
      hostFilter.value ?? undefined,
    ),
  placeholderData: (previous) => previous,
})

const snapshots = computed(() => snapshotsQuery.data.value?.items ?? [])
const total = computed(() => snapshotsQuery.data.value?.total ?? 0)

const tagOptions = computed(() => {
  const options = [{ label: t('snapshot.allTags'), value: '' }]
  for (const task of tasksQuery.data.value?.items ?? []) {
    options.push({ label: `${task.name}（${task.tag}）`, value: task.tag })
  }
  // 历史遗留 tag（无对应任务）：从当前（未筛选）页快照归并，去重
  const known = new Set(options.map((option) => option.value))
  for (const snapshot of snapshots.value) {
    for (const tag of snapshot.tags) {
      if (!known.has(tag)) {
        known.add(tag)
        options.push({ label: tag, value: tag })
      }
    }
  }
  return options
})

/** 主机选项（R-023 多主机视图）：从当前页快照归并 host 去重 + 全部主机兜底。 */
const hostOptions = computed(() => {
  const options = [{ label: t('snapshot.allHosts'), value: '' }]
  const known = new Set<string>([''])
  for (const snapshot of snapshots.value) {
    const host = snapshot.host
    if (host && !known.has(host)) {
      known.add(host)
      options.push({ label: host, value: host })
    }
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

function openTree(snapshot: SnapshotOut): void {
  activeSnapshot.value = snapshot
  treeShow.value = true
}

/** 「恢复此快照」直达恢复向导（PRD §5 页面 8）：快照 ID 走 query，由恢复页负责预选。 */
function restoreActiveSnapshot(): void {
  const snapshotId = activeSnapshot.value?.id
  if (!snapshotId) {
    return
  }
  void router.push({ name: 'restore', query: { snapshot: snapshotId } })
}

function treeLoader(path: string, cursor?: string): Promise<TreePage> {
  const snapshotId = activeSnapshot.value?.id ?? ''
  return listSnapshotFiles(snapshotId, path, cursor).then((data) => ({
    items: data.items.map((item) => ({
      key: item.path,
      name: item.name,
      type: item.type,
      path: item.display_path,
    })),
    nextCursor: data.next_cursor,
  }))
}

const columns = computed<DataTableColumns<SnapshotOut>>(() => [
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
    title: t('snap.size'),
    key: 'size',
    width: 90,
    render: (row) => (row.size === null || row.size === undefined ? '—' : String(row.size)),
  },
  {
    title: '',
    key: 'actions',
    width: 110,
    render: (row) =>
      h(
        NButton,
        { size: 'tiny', secondary: true, onClick: () => openTree(row) },
        () => t('snap.browse'),
      ),
  },
])

const errorText = computed(() => {
  const error = snapshotsQuery.error.value
  return error ? String(error) : ''
})

// ---------------------------------------------------------------- E21 forget 预览-确认流（R-016）

const forgetTag = ref<string | null>(null)
const forgetKeepDaily = ref<number | null>(null)
const forgetKeepWeekly = ref<number | null>(null)
const forgetKeepMonthly = ref<number | null>(null)
const forgetPrune = ref(true)
const forgetPreview = ref<ForgetPreviewResponse | null>(null)
const forgetShow = ref(false)
const forgetLoading = ref(false)
const forgetExecuting = ref(false)

/** 选中的 tag 匹配到任务时，回填该任务的保留档位（E34 卡片不带 keep 字段，取详情）；
 * 历史遗留 tag 无回填来源，维持手填值。 */
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
  } catch {
    // 回填失败不打断手填路径（AppError 已有全局提示）
  }
})

const forgetReady = computed(
  () => forgetTag.value !== null && (forgetKeepDaily.value !== null || forgetKeepWeekly.value !== null || forgetKeepMonthly.value !== null),
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
    return
  }
  forgetExecuting.value = true
  try {
    // params 为 E21 响应回显（服务端保证与预览同源），原样透传保证「预览与实际结果一致」
    await createJob('forget', { params: preview.params })
    message.success(t('snapshot.forget.submitted'))
    forgetPreview.value = null
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
</script>

<template>
  <n-space vertical :size="16">
    <n-alert v-if="errorText" type="error" :show-icon="false">{{ errorText }}</n-alert>

    <n-card :title="t('nav.snapshots')" size="small">
      <!-- R-026-10：tag 筛选 + 分组头（当前筛选维度摘要） -->
      <n-space vertical :size="10" class="snapshots__filter">
        <n-space :size="10" :wrap="false" class="snapshots__filters">
          <n-select
            v-model:value="tagFilter"
            :options="tagOptions"
            size="small"
            class="snapshots__tag-select"
            :placeholder="t('snapshot.tagFilter')"
            clearable
          />
          <n-select
            v-model:value="hostFilter"
            :options="hostOptions"
            size="small"
            class="snapshots__host-select"
            :placeholder="t('snapshot.hostFilter')"
            clearable
          />
        </n-space>
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
      <n-space vertical :size="10">
        <n-alert type="warning" :show-icon="false">{{ t('snapshot.forget.desc') }}</n-alert>
        <n-space align="center" :size="10" :wrap="false" class="snapshots__forget-form">
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
          <n-space align="center" :size="6" :wrap="false">
            <n-switch v-model:value="forgetPrune" size="small" />
            <n-text depth="2" class="snapshots__forget-note">{{ t('snapshot.forget.prune') }}</n-text>
          </n-space>
          <n-button
            size="small"
            type="warning"
            secondary
            :loading="forgetLoading"
            :disabled="!forgetReady"
            @click="runForgetPreview"
          >
            {{ t('snapshot.forget.preview') }}
          </n-button>
        </n-space>
        <n-text v-if="!forgetReady" depth="3" class="snapshots__forget-note">
          {{ t('snapshot.forget.needKeep') }}
        </n-text>
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
              :columns="removeColumns"
              :data="forgetPreview?.remove ?? []"
              :max-height="260"
              :row-key="(row: ForgetPreviewSnapshot) => row.id"
            />
          </template>
        </n-space>
      </template>
    </ConfirmGate>

    <!-- 文件树抽屉：懒加载 + 虚拟滚动 + 游标只透传 -->
    <n-drawer v-model:show="treeShow" :width="720" placement="right">
      <n-drawer-content :title="`${t('snap.filesTitle')} ${activeSnapshot?.id.slice(0, 8) ?? ''}`" closable>
        <n-space vertical :size="10">
          <n-space align="center" justify="space-between">
            <n-text depth="3">{{ activeSnapshot?.paths.join(', ') }}</n-text>
            <n-button size="small" secondary type="primary" @click="restoreActiveSnapshot">
              {{ t('snap.restoreThis') }}
            </n-button>
          </n-space>
          <VirtualTree v-if="treeShow" root-path="/" :load-children="treeLoader" :height="520" />
        </n-space>
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

.snapshots__tag-select {
  max-width: 320px;
}

.snapshots__host-select {
  max-width: 240px;
}

.snapshots__filters {
  flex-wrap: wrap;
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
</style>
