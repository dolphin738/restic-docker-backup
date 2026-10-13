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
  NPagination,
  NSelect,
  NSpace,
  NTag,
  NText,
  type DataTableColumns,
} from 'naive-ui'
import { computed, h, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { listBackupTasks } from '@/api/backupTasks'
import { listSnapshotFiles, listSnapshots } from '@/api/snapshots'
import type { SnapshotOut } from '@/api/types'
import VirtualTree, { type TreePage } from '@/components/VirtualTree.vue'
import { formatDateTimeOr } from '@/utils/datetime'

const PAGE_SIZE = 20
const { t } = useI18n()
const router = useRouter()

const page = ref(1)
/** R-026-10：按任务标签筛选（E17 ?tag=；空 = 全部）。 */
const tagFilter = ref<string | null>(null)

/** 标签选项：E34 任务列表 {id,name,tag} + 当前页快照中出现的历史遗留 tag。 */
const tasksQuery = useQuery({
  queryKey: ['backup-tasks', 'tag-options'],
  queryFn: () => listBackupTasks(50),
})

const snapshotsQuery = useQuery({
  queryKey: ['snapshots', page, tagFilter],
  queryFn: () => listSnapshots(PAGE_SIZE, (page.value - 1) * PAGE_SIZE, tagFilter.value ?? undefined),
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
</script>

<template>
  <n-space vertical :size="16">
    <n-alert v-if="errorText" type="error" :show-icon="false">{{ errorText }}</n-alert>

    <n-card :title="t('nav.snapshots')" size="small">
      <!-- R-026-10：tag 筛选 + 分组头（当前筛选维度摘要） -->
      <n-space vertical :size="10" class="snapshots__filter">
        <n-select
          v-model:value="tagFilter"
          :options="tagOptions"
          size="small"
          class="snapshots__tag-select"
          :placeholder="t('snapshot.tagFilter')"
          clearable
        />
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

.snapshots__group {
  font-size: 12px;
}
</style>
