<script setup lang="ts">
/** 归档页（删除保护方案 v1.2 D13/D16/D17）：只读数据面 + 保留策略配置。
 *
 * 三段分区（与审计页「内容 + 保留策略」同构，复用共用 SegmentedTabs）：
 *   ① 仓库归档：可恢复的归档仓库（恢复 / 释放 ID / 销毁数据 / 查看快照）+ 墓碑（删除记录）；
 *   ② rclone 配置归档：删除即自动归档的 conf 段（恢复写回 / 清理归档），**永不展示值**；
 *   ③ 保留策略：三个保留天数（`retention.repo_archive_days` 等），`<=0` = 不启用自动清理。
 *
 * 为什么配置入口在本页而不回设置页：审计保留策略 2026-10-26 已迁入审计页第二分区，
 * 归档保留策略与它同源同语义，放在一起才不会出现「两处各管一半」。
 *
 * 红线提醒（本页按钮文案与确认门都在守）：归档/墓碑只动「怎么找回」，**永不动数据**；
 * 「销毁数据」是独立按钮 + 独立确认门，绝不与「删除记录」合并。
 */

import { useQuery } from '@tanstack/vue-query'
import {
  NAlert,
  NButton,
  NCard,
  NDataTable,
  NInputNumber,
  NPopconfirm,
  NTag,
  NText,
  useMessage,
  type DataTableColumns,
} from 'naive-ui'
import { computed, h, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { getConfig, putConfig } from '@/api/config'
import {
  deleteRepo,
  deleteRcloneArchive,
  deleteRepoTombstone,
  getArchiveList,
  releaseArchivedRepo,
  restoreArchivedRepo,
  restoreRcloneArchive,
  listRcloneArchives,
} from '@/api/repo'
import type { ArchivedRepoOut, RepoTombstoneOut, RcloneArchiveItemOut } from '@/api/types'
import ConfirmGate from '@/components/ConfirmGate.vue'
import SegmentedTabs from '@/components/dashboard/SegmentedTabs.vue'
import { formatDateTime } from '@/utils/datetime'

const { t } = useI18n()
const message = useMessage()
const router = useRouter()

// ---------------------------------------------------------------- 分区

type TabId = 'repos' | 'rclone' | 'retention'

const tab = ref<TabId>('repos')

const tabItems = computed(() => [
  { id: 'repos', label: t('archive.tabs.repos') },
  { id: 'rclone', label: t('archive.tabs.rclone') },
  { id: 'retention', label: t('archive.tabs.retention') },
])

// ---------------------------------------------------------------- 数据

const archiveQuery = useQuery({ queryKey: ['archive', 'repos'], queryFn: getArchiveList })
const rcloneQuery = useQuery({ queryKey: ['archive', 'rclone'], queryFn: listRcloneArchives })

const tombRows = computed<RepoTombstoneOut[]>(() => archiveQuery.data.value?.tombstones ?? [])

/** 墓碑状态：已销毁（不可恢复）/ 已释放 ID / 仅记录。 */
function tombState(row: RepoTombstoneOut): { label: string; type: 'error' | 'warning' | 'default' } {
  if (row.data_destroyed) {
    return { label: t('archive.stateDestroyed'), type: 'error' }
  }
  if (row.released_at) {
    return { label: t('archive.stateReleased'), type: 'warning' }
  }
  return { label: t('archive.stateTombstone'), type: 'default' }
}

// ---------------------------------------------------------------- 动作：仓库

const releasing = ref(false)
const releaseTarget = ref<ArchivedRepoOut | null>(null)
const releaseShow = ref(false)

const destroying = ref(false)
const destroyTarget = ref<ArchivedRepoOut | null>(null)
const destroyShow = ref(false)

async function onRestore(row: ArchivedRepoOut): Promise<void> {
  try {
    await restoreArchivedRepo(row.repo_id)
    message.success(t('archive.restored', { name: row.name }))
    await archiveQuery.refetch()
  } catch {
    // 错误提示由 http 层统一处理
  }
}

function openRelease(row: ArchivedRepoOut): void {
  releaseTarget.value = row
  releaseShow.value = true
}

async function onRelease(): Promise<void> {
  const target = releaseTarget.value
  if (!target) {
    return
  }
  releasing.value = true
  try {
    await releaseArchivedRepo(target.repo_id, target.repo_id)
    message.success(t('archive.released', { name: target.name }))
    await archiveQuery.refetch()
  } catch {
    // 同上
  } finally {
    releasing.value = false
    releaseShow.value = false
    releaseTarget.value = null
  }
}

function openDestroy(row: ArchivedRepoOut): void {
  destroyTarget.value = row
  destroyShow.value = true
}

async function onDestroy(): Promise<void> {
  const target = destroyTarget.value
  if (!target) {
    return
  }
  destroying.value = true
  try {
    await deleteRepo(target.repo_id, { purgeDisk: true })
    message.success(t('archive.destroyed', { name: target.name }))
    await archiveQuery.refetch()
  } catch {
    // 同上
  } finally {
    destroying.value = false
    destroyShow.value = false
    destroyTarget.value = null
  }
}

async function onDeleteTombstone(row: RepoTombstoneOut): Promise<void> {
  try {
    await deleteRepoTombstone(row.repo_id)
    message.success(t('archive.tombDeleted'))
    await archiveQuery.refetch()
  } catch {
    // 同上
  }
}

/** 跳到快照页并限定该（归档）仓库：`?repo=<id>` 覆盖全局当前仓库。 */
function openSnapshots(row: ArchivedRepoOut): void {
  void router.push({ name: 'snapshots', query: { repo: row.repo_id } })
}

// ---------------------------------------------------------------- 动作：rclone 归档

async function onRestoreRclone(row: RcloneArchiveItemOut): Promise<void> {
  try {
    await restoreRcloneArchive(row.remote)
    message.success(t('archive.rcloneRestored', { name: row.remote }))
    await rcloneQuery.refetch()
  } catch {
    // 同上
  }
}

async function onDeleteRcloneArchive(row: RcloneArchiveItemOut): Promise<void> {
  try {
    await deleteRcloneArchive(row.remote)
    message.success(t('archive.rcloneDeleted'))
    await rcloneQuery.refetch()
  } catch {
    // 同上
  }
}

// ---------------------------------------------------------------- 列定义

const repoColumns = computed<DataTableColumns<ArchivedRepoOut>>(() => [
  {
    title: t('archive.colName'),
    key: 'name',
    minWidth: 180,
    render: (row) =>
      h('div', { class: 'archive__name' }, [
        h('span', { class: 'archive__strong' }, row.name),
        h('span', { class: 'archive__id' }, `#${row.repo_id}`),
      ]),
  },
  { title: t('archive.colType'), key: 'type', width: 100 },
  {
    title: t('archive.colUrl'),
    key: 'url',
    minWidth: 200,
    render: (row) => row.url || '—',
  },
  {
    title: t('archive.colArchivedAt'),
    key: 'archived_at',
    width: 170,
    render: (row) => formatDateTime(row.archived_at),
  },
  {
    title: t('archive.colActions'),
    key: 'actions',
    width: 320,
    render: (row) =>
      h('div', { class: 'archive__actions' }, [
        h(
          NButton,
          { size: 'tiny', secondary: true, onClick: () => openSnapshots(row) },
          { default: () => t('archive.viewSnapshots') },
        ),
        h(
          NButton,
          { size: 'tiny', secondary: true, type: 'primary', onClick: () => void onRestore(row) },
          { default: () => t('archive.restore') },
        ),
        h(
          NButton,
          { size: 'tiny', secondary: true, type: 'warning', onClick: () => openRelease(row) },
          { default: () => t('archive.releaseId') },
        ),
        h(
          NButton,
          { size: 'tiny', secondary: true, type: 'error', onClick: () => openDestroy(row) },
          { default: () => t('archive.destroyData') },
        ),
      ]),
  },
])

const tombColumns = computed<DataTableColumns<RepoTombstoneOut>>(() => [
  {
    title: t('archive.colName'),
    key: 'name',
    minWidth: 180,
    render: (row) =>
      h('div', { class: 'archive__name' }, [
        h('span', { class: 'archive__strong' }, row.name),
        h('span', { class: 'archive__id' }, `#${row.repo_id}`),
      ]),
  },
  { title: t('archive.colType'), key: 'type', width: 100 },
  {
    title: t('archive.colUrl'),
    key: 'url_display',
    minWidth: 180,
    render: (row) => row.url_display || '—',
  },
  {
    title: t('archive.colArchivedAt'),
    key: 'archived_at',
    width: 170,
    render: (row) => formatDateTime(row.archived_at),
  },
  {
    title: t('archive.colSnapshots'),
    key: 'snapshot_count',
    width: 110,
    render: (row) => (row.snapshot_count === null ? '—' : String(row.snapshot_count)),
  },
  {
    title: t('archive.colState'),
    key: 'state',
    width: 130,
    render: (row) => {
      const state = tombState(row)
      return h(NTag, { size: 'small', type: state.type, bordered: false }, { default: () => state.label })
    },
  },
  {
    title: t('archive.colActions'),
    key: 'actions',
    width: 130,
    render: (row) =>
      h(
        NPopconfirm,
        { onPositiveClick: () => void onDeleteTombstone(row) },
        {
          trigger: () =>
            h(
              NButton,
              { size: 'tiny', secondary: true, type: 'error' },
              { default: () => t('archive.deleteRecord') },
            ),
          default: () => t('archive.deleteRecordConfirm'),
        },
      ),
  },
])

const rcloneColumns = computed<DataTableColumns<RcloneArchiveItemOut>>(() => [
  { title: t('archive.colRemote'), key: 'remote', minWidth: 150 },
  { title: t('archive.colType'), key: 'type', width: 110 },
  {
    title: t('archive.colDeletedAt'),
    key: 'deleted_at',
    width: 170,
    render: (row) => formatDateTime(row.deleted_at),
  },
  {
    title: t('archive.colUsedBy'),
    key: 'used_by_repos',
    width: 150,
    render: (row) => t('archive.usedByRepos', { n: row.used_by_repos }),
  },
  {
    title: t('archive.colKeys'),
    key: 'keys',
    minWidth: 200,
    render: (row) => row.keys.join(', ') || '—',
  },
  {
    title: t('archive.colActions'),
    key: 'actions',
    width: 220,
    render: (row) =>
      h('div', { class: 'archive__actions' }, [
        h(
          NButton,
          { size: 'tiny', secondary: true, type: 'primary', onClick: () => void onRestoreRclone(row) },
          { default: () => t('archive.rcloneRestore') },
        ),
        h(
          NPopconfirm,
          { onPositiveClick: () => void onDeleteRcloneArchive(row) },
          {
            trigger: () =>
              h(
                NButton,
                { size: 'tiny', secondary: true, type: 'error' },
                { default: () => t('archive.rcloneDelete') },
              ),
            default: () => t('archive.rcloneDeleteConfirm'),
          },
        ),
      ]),
  },
])

// ---------------------------------------------------------------- 保留策略（与审计页同交互：本地草稿 → 脏检查 → 保存 → refetch）

const KEY_REPO_ARCHIVE = 'retention.repo_archive_days'
const KEY_REPO_TOMBSTONE = 'retention.repo_tombstone_days'
const KEY_RCLONE_ARCHIVE = 'retention.rclone_archive_days'

const repoArchiveDays = ref<number | null>(30)
const repoTombstoneDays = ref<number | null>(365)
const rcloneArchiveDays = ref<number | null>(365)
const savingRetention = ref(false)

const retentionActive = computed(() => tab.value === 'retention')

const configQuery = useQuery({
  queryKey: ['config', 'archive-retention'],
  queryFn: getConfig,
  enabled: retentionActive,
})

watch(
  configQuery.data,
  (data) => {
    if (!data) {
      return
    }
    const item = (key: string) => data.items.find((entry) => entry.key === key)?.value
    const pick = (key: string, fallback: number): number => {
      const parsed = Number(item(key))
      return Number.isInteger(parsed) && parsed >= 0 ? parsed : fallback
    }
    repoArchiveDays.value = pick(KEY_REPO_ARCHIVE, 30)
    repoTombstoneDays.value = pick(KEY_REPO_TOMBSTONE, 365)
    rcloneArchiveDays.value = pick(KEY_RCLONE_ARCHIVE, 365)
  },
  { immediate: true },
)

const retentionDirty = computed(() => {
  const item = (key: string) => configQuery.data.value?.items.find((entry) => entry.key === key)?.value
  const asText = (value: number | null): string =>
    value === null || value === undefined ? '' : String(value)
  return (
    asText(repoArchiveDays.value) !== (item(KEY_REPO_ARCHIVE) ?? '30') ||
    asText(repoTombstoneDays.value) !== (item(KEY_REPO_TOMBSTONE) ?? '365') ||
    asText(rcloneArchiveDays.value) !== (item(KEY_RCLONE_ARCHIVE) ?? '365')
  )
})

async function onSaveRetention(): Promise<void> {
  if (savingRetention.value || !retentionDirty.value) {
    return
  }
  savingRetention.value = true
  try {
    await putConfig({
      [KEY_REPO_ARCHIVE]: repoArchiveDays.value === null ? null : String(repoArchiveDays.value),
      [KEY_REPO_TOMBSTONE]: repoTombstoneDays.value === null ? null : String(repoTombstoneDays.value),
      [KEY_RCLONE_ARCHIVE]: rcloneArchiveDays.value === null ? null : String(rcloneArchiveDays.value),
    })
    message.success(t('archive.retentionSaved'))
    await configQuery.refetch()
  } catch {
    // 422 键级校验 / ENV_OVERRIDDEN 由 http 层统一提示
  } finally {
    savingRetention.value = false
  }
}
</script>

<template>
  <div class="archive">
    <SegmentedTabs
      v-model="tab"
      :items="tabItems"
      hash-prefix="archive"
      :aria-label="t('archive.tabsAria')"
    />

    <!-- ① 仓库归档 -->
    <n-card v-if="tab === 'repos'" :title="t('archive.reposTitle')" size="small">
      <div class="archive__stack">
        <n-text depth="3">{{ t('archive.reposHint') }}</n-text>

        <n-data-table
          v-if="(archiveQuery.data.value?.repos.length ?? 0) > 0"
          :columns="repoColumns"
          :data="archiveQuery.data.value?.repos ?? []"
          :bordered="false"
          :loading="archiveQuery.isFetching.value"
          size="small"
          :scroll-x="1000"
        />
        <n-alert v-else type="default" :show-icon="false">{{ t('archive.reposEmpty') }}</n-alert>

        <div class="archive__subhead">
          <n-text strong>{{ t('archive.tombTitle') }}</n-text>
          <n-text depth="3">{{ t('archive.tombHint') }}</n-text>
        </div>
        <n-data-table
          v-if="tombRows.length > 0"
          :columns="tombColumns"
          :data="tombRows"
          :bordered="false"
          size="small"
          :scroll-x="1000"
        />
        <n-text v-else depth="3">{{ t('archive.tombEmpty') }}</n-text>
      </div>
    </n-card>

    <!-- ② rclone 配置归档 -->
    <n-card v-else-if="tab === 'rclone'" :title="t('archive.rcloneTitle')" size="small">
      <div class="archive__stack">
        <n-alert type="info" :show-icon="false">{{ t('archive.rcloneHint') }}</n-alert>
        <n-data-table
          v-if="(rcloneQuery.data.value?.items.length ?? 0) > 0"
          :columns="rcloneColumns"
          :data="rcloneQuery.data.value?.items ?? []"
          :bordered="false"
          :loading="rcloneQuery.isFetching.value"
          size="small"
          :scroll-x="1000"
        />
        <n-alert v-else type="default" :show-icon="false">{{ t('archive.rcloneEmpty') }}</n-alert>
      </div>
    </n-card>

    <!-- ③ 保留策略 -->
    <n-card v-else :title="t('archive.retentionTitle')" size="small">
      <div class="archive__stack">
        <n-text depth="3">{{ t('archive.retentionHint') }}</n-text>
        <div class="archive__field">
          <div class="archive__field-text">
            <n-text strong>{{ t('archive.retentionRepoArchive') }}</n-text>
            <n-text depth="3">{{ t('archive.retentionRepoArchiveHint') }}</n-text>
          </div>
          <n-input-number v-model:value="repoArchiveDays" :min="0" :max="3650" style="width: 140px" />
        </div>
        <div class="archive__field">
          <div class="archive__field-text">
            <n-text strong>{{ t('archive.retentionRepoTombstone') }}</n-text>
            <n-text depth="3">{{ t('archive.retentionRepoTombstoneHint') }}</n-text>
          </div>
          <n-input-number v-model:value="repoTombstoneDays" :min="0" :max="3650" style="width: 140px" />
        </div>
        <div class="archive__field">
          <div class="archive__field-text">
            <n-text strong>{{ t('archive.retentionRcloneArchive') }}</n-text>
            <n-text depth="3">{{ t('archive.retentionRcloneArchiveHint') }}</n-text>
          </div>
          <n-input-number v-model:value="rcloneArchiveDays" :min="0" :max="3650" style="width: 140px" />
        </div>
        <n-alert type="warning" :show-icon="false">{{ t('archive.retentionZeroHint') }}</n-alert>
        <div class="archive__actions">
          <n-button
            type="primary"
            size="small"
            :disabled="!retentionDirty"
            :loading="savingRetention"
            @click="onSaveRetention"
          >
            {{ t('common.save') }}
          </n-button>
        </div>
      </div>
    </n-card>

    <!-- 释放 ID（重门：关键词 = 仓库 id） -->
    <ConfirmGate
      v-model:show="releaseShow"
      :title="t('archive.releaseTitle')"
      :keyword="releaseTarget?.repo_id ?? ''"
      :description="t('archive.releaseDesc')"
      :loading="releasing"
      :confirm-label="t('archive.releaseId')"
      @confirm="onRelease"
    >
      <template #preview>
        <n-text depth="3">
          {{ t('archive.releasePreview', { name: releaseTarget?.name ?? '' }) }}
        </n-text>
      </template>
    </ConfirmGate>

    <!-- 销毁数据（重门：关键词 = 仓库 id；与「删除记录」严格分离） -->
    <ConfirmGate
      v-model:show="destroyShow"
      :title="t('archive.destroyTitle')"
      :keyword="destroyTarget?.repo_id ?? ''"
      :description="t('archive.destroyDesc')"
      :loading="destroying"
      :confirm-label="t('archive.destroyData')"
      @confirm="onDestroy"
    >
      <template #preview>
        <n-text depth="3">
          {{ t('archive.destroyPreview', { name: destroyTarget?.name ?? '' }) }}
        </n-text>
      </template>
    </ConfirmGate>
  </div>
</template>

<style scoped>
.archive {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* 分区内结构行一律 flex（UI 全量改造方案 §192：不用 n-space 展开） */
.archive__stack {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.archive__subhead {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-top: 8px;
}

.archive__name {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.archive__strong {
  font-weight: 600;
}

.archive__id {
  font-size: 12px;
  opacity: 0.65;
}

.archive__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}

.archive__field {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 10px 0;
  border-top: 1px solid var(--n-divider-color, rgba(128, 128, 140, 0.16));
}

.archive__field:first-of-type {
  border-top: none;
}

.archive__field-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
</style>
