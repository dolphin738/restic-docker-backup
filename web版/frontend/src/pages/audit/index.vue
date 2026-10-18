<script setup lang="ts">
/** 审计日志页（页面 12，R-020，P2 批次 A）。
 *
 * E26 只读查询：筛选（动作前缀下拉/手输 + result 三值 + 时间范围）+ 分页表格（50/页，
 * 服务端上限 500）。S6：append-only——页面无任何编辑/删除入口，仅查询（查询本身不留痕）。
 * detail 为 JSON 文本原文，行展开展示格式化结果；时间统一走 utils/datetime 本地化。
 */

import { useQuery } from '@tanstack/vue-query'
import {
  NAlert,
  NButton,
  NCard,
  NDataTable,
  NDatePicker,
  NSelect,
  NSpace,
  NText,
  type DataTableColumns,
} from 'naive-ui'
import { computed, h, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { queryAudit } from '@/api/audit'
import type { AuditOut, AuditResult } from '@/api/types'
import StatusTag from '@/components/StatusTag.vue'
import { formatDateTime } from '@/utils/datetime'

const PAGE_SIZE = 50

const { t } = useI18n()

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
const actionOptions = computed(() => knownActions.value.map((action) => ({ label: action, value: action })))

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

/** detail 为 JSON 文本原文：能解析则缩进美化，不能则原样透传（契约不保证恒为 JSON）。 */
function prettyDetail(detail: string): string {
  try {
    return JSON.stringify(JSON.parse(detail) as unknown, null, 2)
  } catch {
    return detail
  }
}

const columns = computed<DataTableColumns<AuditOut>>(() => [
  {
    type: 'expand',
    renderExpand: (row) =>
      h(
        'pre',
        { class: 'audit__detail' },
        row.detail ? prettyDetail(row.detail) : t('common.none'),
      ),
  },
  { title: t('audit.colTs'), key: 'ts', width: 170, render: (row) => formatDateTime(row.ts) },
  {
    title: t('audit.colAction'),
    key: 'action',
    width: 170,
    // P3b 收口：动作名标签（原无 type）→ StatusTag default；原文案 row.action 不动
    render: (row) => h(StatusTag, { kind: 'default', label: row.action }),
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
</script>

<template>
  <n-space vertical :size="16">
    <n-card :title="t('nav.audit')" size="small">
      <n-space vertical :size="10">
        <!-- S6 UI 面：append-only 声明，无编辑/删除入口 -->
        <n-text depth="3">{{ t('audit.readonlyNote') }}</n-text>

        <!-- 筛选区 -->
        <n-space align="center" :size="10" class="audit__filters">
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
            {{ t('audit.apply') }}
          </n-button>
          <n-button size="small" quaternary @click="resetFilters">{{ t('audit.reset') }}</n-button>
        </n-space>

        <n-alert v-if="errorText" type="error" :show-icon="false">{{ errorText }}</n-alert>

        <!-- 数据表：行展开展示 detail（JSON 美化），主表无删除/编辑操作列 -->
        <n-data-table
          size="small"
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
    </n-card>
  </n-space>
</template>

<style scoped>
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
</style>
