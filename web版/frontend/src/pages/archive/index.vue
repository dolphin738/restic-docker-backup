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
  NForm,
  NFormItem,
  NIcon,
  NInputNumber,
  NPopconfirm,
  NSpace,
  NTag,
  NText,
  NTooltip,
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
import { IconCheckCircle } from '@/components/icons/icons'
import SegmentedTabs from '@/components/dashboard/SegmentedTabs.vue'
import OpLogDrawer from '@/pages/archive/op-log-drawer.vue'
import { formatDateTime } from '@/utils/datetime'

const { t } = useI18n()
const message = useMessage()
const router = useRouter()

/** 本页三个动作在审计日志里的动作名（后端 constants.py AUDIT_* 字面值）。
 *  它们不共享单一前缀，故服务端按逗号分隔取并集（ops._split_action_prefixes）。 */
const REPO_OP_ACTIONS = ['repo.archive', 'repo.restore', 'repo.purge', 'repo.id_release']
const RCLONE_OP_ACTIONS = ['rclone.config_restore']

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

/** 执行阶段（三个动作互斥，共用一个状态：同一时刻只会有一个在跑）。 */
type OpPhase = 'idle' | 'running' | 'done' | 'failed'

const opPhase = ref<OpPhase>('idle')
const opProgress = ref('')
const opResult = ref('')

/** 有动作在跑 → 行内按钮一律禁用（防双击，也防「点了没反应再来一次」）。 */
const busy = computed(() => opPhase.value === 'running')

const releaseTarget = ref<ArchivedRepoOut | null>(null)
const releaseShow = ref(false)

const destroyTarget = ref<ArchivedRepoOut | null>(null)
const destroyShow = ref(false)

/** 恢复写回：非破坏性动作，**不进危险关键词门**，但反馈与释放/销毁**同款**
 *  （同一个 ConfirmGate + awaitResult：预览 → 确认 → 转圈 → 结果）。 */
const rcloneTarget = ref<RcloneArchiveItemOut | null>(null)
const rcloneShow = ref(false)

/** 操作日志抽屉（数据源 = 审计日志，不另造流水）。 */
const repoLogShow = ref(false)
const rcloneLogShow = ref(false)

/** 错误文案取自 AppError（http 层已把它转成 Error）；非 Error 兜底转字符串。 */
function errText(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

/** 统一的阶段推进（三个动作共用）：跑 → 成功/失败。
 *  成功正文由调用方给（含处置摘要）；失败正文走同一把 `archive.opFailed` 文案，
 *  把 AppError 的中文原因带出来——三个动作的错误呈现因此完全一致。 */
async function runOp(progressText: string, execute: () => Promise<string>): Promise<void> {
  opProgress.value = progressText
  opPhase.value = 'running'
  let ok = false
  try {
    opResult.value = await execute()
    ok = true
  } catch (error) {
    opResult.value = t('archive.opFailed', { reason: errText(error) })
  }
  opPhase.value = ok ? 'done' : 'failed'
}

/** 关掉结果弹窗时清空阶段态（下次打开是干净的第 3 步，不是上次的结果）。 */
function resetOp(): void {
  opPhase.value = 'idle'
  opProgress.value = ''
  opResult.value = ''
}

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
  await runOp(t('archive.releaseRunning', { name: target.name }), async () => {
    await releaseArchivedRepo(target.repo_id, target.repo_id)
    await archiveQuery.refetch()
    // 释放后该行从「归档仓库」移到墓碑区，目标随之失效
    releaseTarget.value = null
    return t('archive.releaseDone', { name: target.name })
  })
}

/** 能否销毁数据：**仅本地 FS**（与仓库页删除门的勾选框同一判据）。
 *  远端后端（rclone/s3/b2…）没有本地目录可清，`purge_disk=1` 会被后端 422 拒绝
 *  （「远程后端无法自动清空」）——此前本页无条件发 purgeDisk，远端仓库点了必然报错。 */
function canDestroy(row: ArchivedRepoOut): boolean {
  return row.type === 'local'
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
  await runOp(t('archive.destroyRunning', { name: target.name }), async () => {
    await deleteRepo(target.repo_id, { purgeDisk: true })
    await archiveQuery.refetch()
    destroyTarget.value = null
    return t('archive.destroyDone', { name: target.name })
  })
}

watch(releaseShow, (visible) => {
  if (!visible) {
    releaseTarget.value = null
    resetOp()
  }
})

watch(destroyShow, (visible) => {
  if (!visible) {
    destroyTarget.value = null
    resetOp()
  }
})

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

/** 点「恢复写回」只打开确认门（预览/确认两步），**不立即执行**——
 *  与释放 ID/销毁数据完全一致的入口，执行交给 @confirm 后的 onRestoreRclone。 */
function openRcloneRestore(row: RcloneArchiveItemOut): void {
  rcloneTarget.value = row
  rcloneShow.value = true
}

/** 确认门点「确认」后触发：跑 → 转圈 → 成功/失败，结果留在同一个弹窗内（awaitResult）。 */
async function onRestoreRclone(): Promise<void> {
  const target = rcloneTarget.value
  if (!target) {
    return
  }
  await runOp(t('archive.rcloneRestoreRunning', { name: target.remote }), async () => {
    await restoreRcloneArchive(target.remote)
    await rcloneQuery.refetch()
    return t('archive.rcloneRestoreDone', { name: target.remote })
  })
}

watch(rcloneShow, (visible) => {
  if (!visible) {
    rcloneTarget.value = null
    resetOp()
  }
})

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

/** 操作列「销毁数据」：远端后端禁用 + 原因气泡——`purge_disk=1` 对 rclone/s3/b2…
 *  一律 422（后端「远程后端无法自动清空」），无条件发请求就是一枚必然报错的死键。 */
function renderDestroyButton(row: ArchivedRepoOut) {
  const destroyable = canDestroy(row)
  const button = () =>
    h(
      NButton,
      {
        size: 'tiny',
        secondary: true,
        type: 'error',
        disabled: busy.value || !destroyable,
        onClick: () => openDestroy(row),
      },
      { default: () => t('archive.destroyData') },
    )
  if (destroyable) {
    return button()
  }
  return h(
    NTooltip,
    { trigger: 'hover' },
    { trigger: button, default: () => t('archive.destroyRemoteHint') },
  )
}

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
          { size: 'tiny', secondary: true, disabled: busy.value, onClick: () => openSnapshots(row) },
          { default: () => t('archive.viewSnapshots') },
        ),
        h(
          NButton,
          {
            size: 'tiny',
            secondary: true,
            type: 'primary',
            disabled: busy.value,
            onClick: () => void onRestore(row),
          },
          { default: () => t('archive.restore') },
        ),
        h(
          NButton,
          {
            size: 'tiny',
            secondary: true,
            type: 'warning',
            disabled: busy.value,
            onClick: () => openRelease(row),
          },
          { default: () => t('archive.releaseId') },
        ),
        renderDestroyButton(row),
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
          {
            size: 'tiny',
            secondary: true,
            type: 'primary',
            disabled: busy.value,
            onClick: () => openRcloneRestore(row),
          },
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
const KEY_REPO_PASSFILE = 'retention.repo_passfile_days'

const repoArchiveDays = ref<number | null>(30)
const repoTombstoneDays = ref<number | null>(365)
const rcloneArchiveDays = ref<number | null>(365)
const repoPassfileDays = ref<number | null>(30)
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
    repoPassfileDays.value = pick(KEY_REPO_PASSFILE, 30)
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
    asText(rcloneArchiveDays.value) !== (item(KEY_RCLONE_ARCHIVE) ?? '365') ||
    asText(repoPassfileDays.value) !== (item(KEY_REPO_PASSFILE) ?? '30')
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
      [KEY_REPO_PASSFILE]: repoPassfileDays.value === null ? null : String(repoPassfileDays.value),
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
        <div class="archive__toolbar">
          <n-text depth="3">{{ t('archive.reposHint') }}</n-text>
          <n-button size="tiny" secondary @click="repoLogShow = true">
            {{ t('archive.opLog.open') }}
          </n-button>
        </div>

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
        <div class="archive__toolbar">
          <n-alert type="info" :show-icon="false" class="archive__toolbar-alert">
            {{ t('archive.rcloneHint') }}
          </n-alert>
          <n-button size="tiny" secondary @click="rcloneLogShow = true">
            {{ t('archive.opLog.open') }}
          </n-button>
        </div>
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

    <!-- ③ 保留策略（与审计页第二分区**同款布局**：标签置顶 + 数字框 + 框下提示） -->
    <n-card v-else :title="t('archive.retentionTitle')" size="small">
      <div class="archive__stack">
        <n-text depth="3">{{ t('archive.retentionHint') }}</n-text>
        <n-form label-placement="top" class="archive__form">
          <n-form-item :label="t('archive.retentionRepoArchive')">
            <n-space vertical :size="4">
              <n-input-number
                v-model:value="repoArchiveDays"
                :min="0"
                :show-button="false"
                class="archive__number"
              />
              <n-text depth="3" class="archive__hint">
                {{ t('archive.retentionRepoArchiveHint') }}
              </n-text>
            </n-space>
          </n-form-item>
          <n-form-item :label="t('archive.retentionRepoTombstone')">
            <n-space vertical :size="4">
              <n-input-number
                v-model:value="repoTombstoneDays"
                :min="0"
                :show-button="false"
                class="archive__number"
              />
              <n-text depth="3" class="archive__hint">
                {{ t('archive.retentionRepoTombstoneHint') }}
              </n-text>
            </n-space>
          </n-form-item>
          <n-form-item :label="t('archive.retentionRcloneArchive')">
            <n-space vertical :size="4">
              <n-input-number
                v-model:value="rcloneArchiveDays"
                :min="0"
                :show-button="false"
                class="archive__number"
              />
              <n-text depth="3" class="archive__hint">
                {{ t('archive.retentionRcloneArchiveHint') }}
              </n-text>
            </n-space>
          </n-form-item>
          <n-form-item :label="t('archive.retentionRepoPassfile')">
            <n-space vertical :size="4">
              <n-input-number
                v-model:value="repoPassfileDays"
                :min="0"
                :show-button="false"
                class="archive__number"
              />
              <n-text depth="3" class="archive__hint">
                {{ t('archive.retentionRepoPassfileHint') }}
              </n-text>
            </n-space>
          </n-form-item>
        </n-form>
        <n-alert type="warning" :show-icon="false" class="archive__warn">{{ t('archive.retentionZeroHint') }}</n-alert>
        <div class="archive__actions">
          <n-button
            type="primary"
            :disabled="!retentionDirty"
            :loading="savingRetention"
            @click="onSaveRetention"
          >
            <template #icon>
              <n-icon :size="16"><IconCheckCircle /></n-icon>
            </template>
            {{ t('common.save') }}
          </n-button>
        </div>
      </div>
    </n-card>

    <!-- 释放 ID（重门：关键词 = 仓库 id；awaitResult = 执行中/结果留在弹窗内） -->
    <ConfirmGate
      v-model:show="releaseShow"
      :title="t('archive.releaseTitle')"
      :keyword="releaseTarget?.repo_id ?? ''"
      :description="t('archive.releaseDesc')"
      :confirm-label="t('archive.releaseId')"
      await-result
      :phase="opPhase === 'idle' ? null : opPhase"
      :progress-label="opProgress"
      :result-text="opResult"
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
      :confirm-label="t('archive.destroyData')"
      await-result
      :phase="opPhase === 'idle' ? null : opPhase"
      :progress-label="opProgress"
      :result-text="opResult"
      @confirm="onDestroy"
    >
      <template #preview>
        <n-text depth="3">
          {{ t('archive.destroyPreview', { name: destroyTarget?.name ?? '' }) }}
        </n-text>
      </template>
    </ConfirmGate>

    <!-- 恢复写回：非破坏性 → 不进危险关键词门，但反馈与释放/销毁**同款**（同一个 ConfirmGate + awaitResult）。 -->
    <ConfirmGate
      v-model:show="rcloneShow"
      :title="t('archive.rcloneRestoreTitle')"
      :description="t('archive.rcloneRestoreDesc')"
      :confirm-label="t('archive.rcloneRestore')"
      confirm-type="primary"
      await-result
      :phase="opPhase === 'idle' ? null : opPhase"
      :progress-label="opProgress"
      :result-text="opResult"
      @confirm="onRestoreRclone"
    />

    <!-- 操作日志（数据源 = 审计日志，append-only，只增不改） -->
    <OpLogDrawer
      v-model:show="repoLogShow"
      :title="t('archive.opLog.reposTitle')"
      :actions="REPO_OP_ACTIONS"
    />
    <OpLogDrawer
      v-model:show="rcloneLogShow"
      :title="t('archive.opLog.rcloneTitle')"
      :actions="RCLONE_OP_ACTIONS"
    />
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

/* 分区顶部工具行：提示文字居左撑开，操作入口贴右（窄屏可换行，不挤压提示） */
.archive__toolbar {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  min-width: 0;
}

.archive__toolbar > :first-child {
  flex: 1 1 auto;
  min-width: 0;
}

.archive__toolbar > .n-button {
  flex: none;
}

.archive__toolbar-alert {
  width: 100%;
}

/* 保留策略：与审计页第二分区同款几何（表单收窄到 640 / 数字框 200 / 提示 12px） */
.archive__form {
  max-width: 640px;
}

/* 底部告警条与上方设置表单同宽（不再铺满整张卡），避免「黄色长条」观感 */
.archive__warn {
  max-width: 640px;
}

.archive__number {
  width: 200px;
}

.archive__hint {
  font-size: 12px;
}
</style>
