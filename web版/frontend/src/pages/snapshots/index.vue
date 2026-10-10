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
  NSpace,
  NTag,
  NText,
  type DataTableColumns,
} from 'naive-ui'
import { computed, h, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { listSnapshotFiles, listSnapshots } from '@/api/snapshots'
import type { SnapshotOut } from '@/api/types'
import VirtualTree, { type TreePage } from '@/components/VirtualTree.vue'

const PAGE_SIZE = 20
const { t } = useI18n()

const page = ref(1)
const snapshotsQuery = useQuery({
  queryKey: ['snapshots', page],
  queryFn: () => listSnapshots(PAGE_SIZE, (page.value - 1) * PAGE_SIZE),
  placeholderData: (previous) => previous,
})

const snapshots = computed(() => snapshotsQuery.data.value?.items ?? [])
const total = computed(() => snapshotsQuery.data.value?.total ?? 0)

// ---------------------------------------------------------------- 文件树抽屉
const treeShow = ref(false)
const activeSnapshot = ref<SnapshotOut | null>(null)

function openTree(snapshot: SnapshotOut): void {
  activeSnapshot.value = snapshot
  treeShow.value = true
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
    render: (row) => row.time ?? '—',
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
          <n-text depth="3">{{ activeSnapshot?.paths.join(', ') }}</n-text>
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
</style>
