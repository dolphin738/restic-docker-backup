<script setup lang="ts">
/** 审计日志页（页面 12，R-020，P2 批次 A）。
 *
 * 两个分区（2026-10-26 迁移：保留策略从设置页并入本页）：
 *  ① 审计日志——E26 只读查询：筛选（动作前缀下拉/手输 + result 三值 + 时间范围）
 *    + 分页表格（50/页，服务端上限 500）。S6：append-only——无任何编辑/删除入口；
 *  ② 保留策略——R-020 裁决1/3：审计保留期 / 归档保留期 / 归档目录 / 冷存占位
 *    + 归档文件只读列表。**不放审计「立即清理」按钮**（裁决4：避免误触发审计删除；
 *    任务历史的「立即清理」在任务历史页，仅清 jobs，见 裁决5）。
 * detail 为 JSON 文本原文，行展开展示格式化结果；时间统一走 utils/datetime 本地化。
 */

import { useQuery } from '@tanstack/vue-query'
import {
  NAlert,
  NButton,
  NCard,
  NDataTable,
  NDatePicker,
  NDivider,
  NEmpty,
  NForm,
  NFormItem,
  NIcon,
  NInput,
  NInputNumber,
  NSelect,
  NSpace,
  NTable,
  NText,
  useMessage,
  type DataTableColumns,
} from 'naive-ui'
import { computed, h, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { exportAuditNdjson, listAuditArchives, queryAudit } from '@/api/audit'
import { getConfig } from '@/api/config'
import { saveDumpBehaviorConfig } from '@/api/settings'
import type { AuditOut, AuditResult } from '@/api/types'
import { IconCheckCircle, IconDownload, IconFilter, IconX } from '@/components/icons/icons'
import SegmentedTabs from '@/components/dashboard/SegmentedTabs.vue'
import StatusTag from '@/components/StatusTag.vue'
import {
  auditActionLabel,
  auditDetailKeyLabel,
  auditDetailValue,
  parseAuditDetail,
} from '@/utils/audit'
import { formatDateTime } from '@/utils/datetime'
import { formatBytes } from '@/utils/format'

const PAGE_SIZE = 50

const { t, te } = useI18n()
const message = useMessage()

// ---------------------------------------------------------------- 分区（2026-10-26：保留策略从设置页并入）

type AuditTabId = 'log' | 'retention'

const activeTab = ref<AuditTabId>('log')

/** 分段条项：文案经 computed 派生，切语言即时刷新；深链 #audit-tab-<id> 内置于组件。 */
const tabLinks = computed(() => [
  { id: 'log', label: t('audit.tabs.log') },
  { id: 'retention', label: t('audit.tabs.retention') },
])

function onTabChange(id: string): void {
  activeTab.value = id as AuditTabId
}

// ---------------------------------------------------------------- 筛选（草稿态 + 应用态分离，点「查询」生效）

const draftAction = ref<string | null>(null)
const draftResult = ref<AuditResult | null>(null)
const draftRange = ref<[number, number] | null>(null)

const appliedAction = ref<string | null>(null)
const appliedResult = ref<AuditResult | null>(null)
const appliedSince = ref<string | undefined>(undefined)
const appliedUntil = ref<string | undefined>(undefined)
const page = ref(1)

function applyFilters(): void {
  const action = draftAction.value?.trim()
  appliedAction.value = action ? action : null
  appliedResult.value = draftResult.value
  appliedSince.value = draftRange.value ? new Date(draftRange.value[0]).toISOString() : undefined
  appliedUntil.value = draftRange.value ? new Date(draftRange.value[1]).toISOString() : undefined
  page.value = 1
}

function resetFilters(): void {
  draftAction.value = null
  draftResult.value = null
  draftRange.value = null
  applyFilters()
}

// ---------------------------------------------------------------- 导出当前筛选（E26：NDJSON 下载，S6 只读不留痕）

const exporting = ref(false)

async function onExport(): Promise<void> {
  exporting.value = true
  try {
    const text = await exportAuditNdjson({
      action: appliedAction.value ?? undefined,
      result: appliedResult.value ?? undefined,
      since: appliedSince.value,
      until: appliedUntil.value,
    })
    const blob = new Blob([text], { type: 'application/x-ndjson' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `audit-export-${new Date().toISOString().slice(0, 10)}.ndjson`
    anchor.click()
    URL.revokeObjectURL(url)
    message.success(t('audit.export.done'))
  } catch {
    // 错误（AppError）由 http 层全局提示
  } finally {
    exporting.value = false
  }
}

const resultOptions = computed(() => [
  { label: t('audit.result.ok'), value: 'ok' as AuditResult },
  { label: t('audit.result.denied'), value: 'denied' as AuditResult },
  { label: t('audit.result.failed'), value: 'failed' as AuditResult },
])

// ---------------------------------------------------------------- 查询（vue-query：筛选/页码变化自动重新拉取）

const auditQuery = useQuery({
  queryKey: ['audit', appliedAction, appliedResult, appliedSince, appliedUntil, page],
  queryFn: () =>
    queryAudit({
      action: appliedAction.value ?? undefined,
      result: appliedResult.value ?? undefined,
      since: appliedSince.value,
      until: appliedUntil.value,
      limit: PAGE_SIZE,
      offset: (page.value - 1) * PAGE_SIZE,
    }),
  placeholderData: (previous) => previous,
})

const rows = computed(() => auditQuery.data.value?.items ?? [])
const total = computed(() => auditQuery.data.value?.total ?? 0)
const errorText = computed(() => {
  const error = auditQuery.error.value
  return error ? String(error) : ''
})

/** 动作名枚举（响应附带）：筛选下拉数据源；filterable+tag 允许手输任意前缀（如 restore.）。 */
const knownActions = ref<string[]>([])
// 筛选项「中文（原文）」：既能一眼看懂，又保留原文以便按前缀手输（如 restore.）；
// 未收录（译文 = 原文）时不重复拼括号，直接显示原文。
const actionOptions = computed(() =>
  knownActions.value.map((action) => {
    const label = actionLabel(action)
    return { label: label === action ? action : `${label}（${action}）`, value: action }
  }),
)

/** 累积去重：换筛选/翻页后不丢此前见过的动作名。 */
watch(
  () => auditQuery.data.value?.actions ?? [],
  (actions) => {
    if (actions.length === 0) {
      return
    }
    const known = new Set(knownActions.value)
    for (const action of actions) {
      known.add(action)
    }
    knownActions.value = [...known].sort()
  },
  { immediate: true },
)

const pagination = computed(() => ({
  page: page.value,
  pageSize: PAGE_SIZE,
  itemCount: total.value,
  onChange: (value: number) => {
    page.value = value
  },
  prefix: () => t('audit.total', { n: total.value }),
}))

// ---------------------------------------------------------------- 表格列（含 detail 行展开；无任何操作列）

function resultTagType(result: AuditResult): 'success' | 'warning' | 'error' {
  switch (result) {
    case 'ok':
      return 'success'
    case 'denied':
      return 'warning'
    case 'failed':
      return 'error'
  }
}

/** 三大展示格式化收口到 utils/audit（与归档页「操作日志」共用一份）；
 *  未收录的动作名/键名一律回落原文：宁可显示英文，也不显示空标签。 */
function actionLabel(action: string): string {
  return auditActionLabel(action, t, te)
}

/** 展开区：中文键名的键值列表（不再是裸 JSON——审计是给人看的，键名全是英文时读不动）。 */
function renderDetail(detail: string) {
  const parsed = parseAuditDetail(detail)
  if (!parsed) {
    return h('pre', { class: 'audit__detail' }, detail || t('common.none'))
  }
  const entries = Object.entries(parsed)
  if (entries.length === 0) {
    return h('pre', { class: 'audit__detail' }, t('common.none'))
  }
  return h(
    'div',
    { class: 'audit__detail-list' },
    entries.map(([key, value]) =>
      h('div', { class: 'audit__detail-row' }, [
        h('span', { class: 'audit__detail-key' }, auditDetailKeyLabel(key, t, te)),
        h('span', { class: 'audit__detail-val' }, auditDetailValue(key, value, t)),
      ]),
    ),
  )
}

const columns = computed<DataTableColumns<AuditOut>>(() => [
  {
    type: 'expand',
    renderExpand: (row) => renderDetail(row.detail ?? ''),
  },
  { title: t('audit.colTs'), key: 'ts', width: 170, render: (row) => formatDateTime(row.ts) },
  {
    title: t('audit.colAction'),
    key: 'action',
    width: 170,
    // P3b 收口：动作名标签（原无 type）→ StatusTag default；文案走 i18n 中文（未收录回落原文）
    render: (row) => h(StatusTag, { kind: 'default', label: actionLabel(row.action) }),
  },
  {
    title: t('audit.colResult'),
    key: 'result',
    width: 100,
    // P3b 收口：ok→success / denied→warning / failed→error 映射不变，只换渲染
    render: (row) =>
      h(StatusTag, {
        kind: resultTagType(row.result),
        label: t(`audit.result.${row.result}`),
      }),
  },
  {
    title: t('audit.colTarget'),
    key: 'target',
    ellipsis: { tooltip: true },
    render: (row) => row.target ?? '—',
  },
])

// ---------------------------------------------------------------- 保留策略（R-020 裁决1/裁决3：审计保留期 + 归档 + 冷存占位）

const auditDays = ref<number | null>(365)
const archiveDays = ref<number | null>(365)
const archiveDir = ref('')
const coldTarget = ref('')
const savingRetention = ref(false)

/** 仅在「保留策略」分区激活时拉配置/归档列表（默认停在日志分区 = 零额外请求）。 */
const retentionActive = computed(() => activeTab.value === 'retention')

const configQuery = useQuery({
  queryKey: ['config', 'audit-retention'],
  queryFn: getConfig,
  enabled: retentionActive,
})

watch(
  configQuery.data,
  (data) => {
    if (!data) {
      return
    }
    const item = (key: string) => data.items.find((entry) => entry.key === key) ?? null
    const ad = Number(item('retention.audit_days')?.value)
    auditDays.value = Number.isInteger(ad) && ad >= 0 ? ad : 365
    const ard = Number(item('retention.audit_archive_days')?.value)
    archiveDays.value = Number.isInteger(ard) && ard >= 0 ? ard : 365
    archiveDir.value = item('retention.audit_archive_dir')?.value ?? ''
    coldTarget.value = item('retention.audit_archive_cold_target')?.value ?? ''
  },
  { immediate: true },
)

const retentionDirty = computed(() => {
  const item = (key: string) =>
    configQuery.data.value?.items.find((entry) => entry.key === key) ?? null
  return (
    String(auditDays.value ?? '') !== (item('retention.audit_days')?.value ?? '365') ||
    String(archiveDays.value ?? '') !== (item('retention.audit_archive_days')?.value ?? '365') ||
    archiveDir.value.trim() !== (item('retention.audit_archive_dir')?.value ?? '') ||
    coldTarget.value.trim() !== (item('retention.audit_archive_cold_target')?.value ?? '')
  )
})

const archiveQuery = useQuery({
  queryKey: ['audit', 'archives'],
  queryFn: listAuditArchives,
  enabled: retentionActive,
})

async function onSaveRetention(): Promise<void> {
  if (savingRetention.value || !retentionDirty.value) {
    return
  }
  savingRetention.value = true
  try {
    await saveDumpBehaviorConfig({
      'retention.audit_days': auditDays.value !== null ? String(auditDays.value) : null,
      'retention.audit_archive_days': archiveDays.value !== null ? String(archiveDays.value) : null,
      'retention.audit_archive_dir': archiveDir.value.trim() || null,
      'retention.audit_archive_cold_target': coldTarget.value.trim() || null,
    })
    message.success(t('audit.retention.saved'))
    await configQuery.refetch()
  } catch {
    // 错误（422 键级校验 / ENV_OVERRIDDEN 等）由 http 层全局提示
  } finally {
    savingRetention.value = false
  }
}
</script>

<template>
  <n-space vertical :size="16">
    <n-card :title="t('nav.audit')" size="small">
      <!-- 分区：① 审计日志 ② 保留策略（2026-10-26 从设置页并入；裁决4：页内无审计「立即清理」按钮） -->
      <div class="audit__segments">
        <SegmentedTabs
          :items="tabLinks"
          :model-value="activeTab"
          hash-prefix="audit-tab"
          :aria-label="t('audit.tabsAria')"
          @update:model-value="onTabChange"
        />
      </div>

      <!-- ① 审计日志 -->
      <div v-if="activeTab === 'log'" class="audit__panel">
        <n-space vertical :size="12">
          <!-- S6 UI 面：append-only 声明，无编辑/删除入口 -->
          <n-text depth="3">{{ t('audit.readonlyNote') }}</n-text>

        <!-- 筛选区 -->
        <n-space align="center" :size="8" class="audit__filters">
          <n-select
            v-model:value="draftAction"
            :options="actionOptions"
            filterable
            tag
            clearable
            :placeholder="t('audit.actionPlaceholder')"
            size="small"
            class="audit__action-select"
          />
          <n-select
            v-model:value="draftResult"
            :options="resultOptions"
            clearable
            :placeholder="t('audit.filterResult')"
            size="small"
            class="audit__result-select"
          />
          <n-date-picker
            v-model:value="draftRange"
            type="datetimerange"
            clearable
            size="small"
            class="audit__range"
          />
          <n-button size="small" type="primary" :loading="auditQuery.isFetching.value" @click="applyFilters">
            <template #icon>
              <n-icon :size="16"><IconFilter /></n-icon>
            </template>
            {{ t('audit.apply') }}
          </n-button>
          <n-button size="small" quaternary @click="resetFilters">
            <template #icon>
              <n-icon :size="16"><IconX /></n-icon>
            </template>
            {{ t('audit.reset') }}
          </n-button>
          <n-button size="small" secondary :loading="exporting" @click="onExport">
            <template #icon>
              <n-icon :size="16"><IconDownload /></n-icon>
            </template>
            {{ t('audit.export.btn') }}
          </n-button>
        </n-space>

        <n-alert v-if="errorText" type="error" :show-icon="false">{{ errorText }}</n-alert>

        <!-- 数据表：行展开展示 detail（JSON 美化），主表无删除/编辑操作列 -->
        <n-data-table
          size="small"
          :scroll-x="720"
          :columns="columns"
          :data="rows"
          :loading="auditQuery.isFetching.value"
          :pagination="pagination"
          :row-key="(row: AuditOut) => row.id"
        />
          <n-text v-if="!auditQuery.isFetching.value && rows.length === 0 && !errorText" depth="3">
            {{ t('audit.empty') }}
          </n-text>
        </n-space>
      </div>

      <!-- ② 保留策略（R-020 裁决1/裁决3：审计保留期 + 归档压缩 + 冷存占位；只读归档列表） -->
      <div v-else class="audit__panel">
        <n-space vertical :size="12">
          <n-text depth="3">{{ t('audit.retention.desc') }}</n-text>
          <n-form label-placement="top" class="audit__form">
            <n-form-item :label="t('audit.retention.auditDays')">
              <n-space vertical :size="4">
                <n-input-number
                  v-model:value="auditDays"
                  :min="0"
                  :show-button="false"
                  class="audit__number"
                />
                <n-text depth="3" class="audit__hint">{{ t('audit.retention.auditDaysHint') }}</n-text>
              </n-space>
            </n-form-item>
            <n-form-item :label="t('audit.retention.archiveDays')">
              <n-space vertical :size="4">
                <n-input-number
                  v-model:value="archiveDays"
                  :min="0"
                  :show-button="false"
                  class="audit__number"
                />
                <n-text depth="3" class="audit__hint">{{ t('audit.retention.archiveDaysHint') }}</n-text>
              </n-space>
            </n-form-item>
            <n-form-item :label="t('audit.retention.archiveDir')">
              <n-space vertical :size="4">
                <n-input
                  v-model:value="archiveDir"
                  :placeholder="t('audit.retention.archiveDirPlaceholder')"
                />
                <n-text depth="3" class="audit__hint">{{ t('audit.retention.archiveDirHint') }}</n-text>
              </n-space>
            </n-form-item>
            <n-form-item :label="t('audit.retention.coldTarget')">
              <n-space vertical :size="4">
                <n-input
                  v-model:value="coldTarget"
                  :placeholder="t('audit.retention.coldTargetPlaceholder')"
                />
                <n-text depth="3" class="audit__hint">{{ t('audit.retention.coldTargetHint') }}</n-text>
              </n-space>
            </n-form-item>
          </n-form>
          <n-space justify="start">
            <n-button
              type="primary"
              :loading="savingRetention"
              :disabled="!retentionDirty"
              @click="onSaveRetention"
            >
              <template #icon>
                <n-icon :size="16"><IconCheckCircle /></n-icon>
              </template>
              {{ t('common.save') }}
            </n-button>
          </n-space>

          <!-- 归档文件只读列表（外送冷存由部署侧既有 restic/rclone/挂卷完成，见部署指南 §7.0） -->
          <n-divider />
          <n-space align="center" :size="8" wrap>
            <n-text depth="2">{{ t('audit.retention.archivesTitle') }}</n-text>
            <n-button
              size="tiny"
              quaternary
              :loading="archiveQuery.isFetching.value"
              @click="archiveQuery.refetch()"
            >
              {{ t('audit.retention.archivesRefresh') }}
            </n-button>
          </n-space>
          <n-empty v-if="archiveQuery.isLoading.value" :description="t('common.loading')" size="small" />
          <n-table
            v-else-if="archiveQuery.data.value && archiveQuery.data.value.length > 0"
            :single-line="false"
            size="small"
            class="audit__archive-table"
          >
            <thead>
              <tr>
                <th>{{ t('audit.retention.archiveName') }}</th>
                <th>{{ t('audit.retention.archiveSize') }}</th>
                <th>{{ t('audit.retention.archiveMtime') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="file in archiveQuery.data.value" :key="file.name">
                <td>{{ file.name }}</td>
                <td>{{ formatBytes(file.size_bytes) }}</td>
                <td>{{ formatDateTime(file.mtime_iso) }}</td>
              </tr>
            </tbody>
          </n-table>
          <n-empty v-else :description="t('audit.retention.archivesEmpty')" size="small" />
        </n-space>
      </div>
    </n-card>
  </n-space>
</template>

<style scoped>
/* 分区切换（复用全站 SegmentedTabs 形态层，勿逐页另写一套） */
.audit__segments {
  margin-bottom: 16px;
}

/* 保留策略面板：表单按内容宽度收窄，长提示不铺满整屏 */
.audit__form {
  max-width: 640px;
}

.audit__number {
  width: 200px;
}

.audit__hint {
  font-size: 12px;
}

/* 归档文件表：窄屏可横向滚动，不撑破卡片 */
.audit__archive-table {
  max-width: 100%;
  overflow-x: auto;
}

.audit__filters {
  flex-wrap: wrap;
}

.audit__action-select {
  width: 240px;
}

.audit__result-select {
  width: 160px;
}

.audit__range {
  width: 360px;
  /* 移动端走查：390px 视口内容宽 ~342px，固定宽会溢出裁切；max-width 只在窄屏生效 */
  max-width: 100%;
}

/* detail 展开区：等宽字体 + 横向滚动，长 JSON 不撑破表格 */
.audit__detail {
  margin: 0;
  padding: 8px 12px;
  max-width: 640px;
  overflow: auto;
  /* 等宽常量收口：theme/tokens.ts fontFamilyMono（经 --font-family-mono 全局变量） */
  font-family: var(--font-family-mono);
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
}

/* 中文化键值列表（键名中文 + 值原样；读审计不用再对英文键名猜含义） */
.audit__detail-list {
  display: grid;
  gap: 4px;
  padding: 8px 12px;
  max-width: 640px;
  font-size: 12px;
  line-height: 1.6;
}

.audit__detail-row {
  display: grid;
  grid-template-columns: 140px 1fr;
  gap: 8px;
}

.audit__detail-key {
  color: var(--n-text-color-depth-3, inherit);
}

.audit__detail-val {
  word-break: break-all;
}
</style>
