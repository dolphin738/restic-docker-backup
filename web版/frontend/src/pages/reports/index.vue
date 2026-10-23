<script setup lang="ts">
/** 备份成功率报表页（R-024 / P3-B）。
 *
 * E38 数据源：按配置时区分桶的日报表（零运行日补齐）。展示层零图表依赖——
 * 比率用内联宽度条呈现（项目无图表库选型，不为单页引入）；Prometheus 抓取
 * 入口（E37 /api/metrics + Bearer 令牌）在系统设置「Prometheus 指标」卡管理。
 */

import { useQuery } from '@tanstack/vue-query'
import {
  NAlert,
  NCard,
  NDataTable,
  NRadioButton,
  NRadioGroup,
  NSpace,
  NText,
  type DataTableColumns,
} from 'naive-ui'
import { computed, h, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { getBackupSuccessReport } from '@/api/reports'
import type { ReportBucket } from '@/api/types'

const { t } = useI18n()

const days = ref(30)
const reportQuery = useQuery({
  queryKey: ['reports', 'backup-success', days],
  queryFn: () => getBackupSuccessReport(days.value),
})

const report = computed(() => reportQuery.data.value ?? null)

const windowOptions = computed(() => [
  { label: t('reports.window7'), value: 7 },
  { label: t('reports.window30'), value: 30 },
  { label: t('reports.window90'), value: 90 },
])

function formatRatio(ratio: number | null): string {
  return ratio === null ? '—' : `${(ratio * 100).toFixed(1)}%`
}

/** 比率内联条：宽度=比率，无运行日渲染 —（<50% 走警示色）。 */
function renderRatio(bucket: ReportBucket) {
  const ratio = bucket.ratio
  if (ratio === null) {
    return h(NText, { depth: 3 }, () => '—')
  }
  const percent = Math.round(ratio * 100)
  return h(NSpace, { align: 'center', size: 8 }, () => [
    h('div', { class: 'reports__ratio-track' }, [
      h('div', {
        class: 'reports__ratio-bar',
        style: { width: `${percent}%` },
        'data-low': ratio < 0.5 ? 'true' : undefined,
      }),
    ]),
    h(NText, null, () => formatRatio(ratio)),
  ])
}

const columns = computed<DataTableColumns<ReportBucket>>(() => [
  {
    title: t('reports.colDate'),
    key: 'date',
    render: (row: ReportBucket) =>
      h(NText, { depth: row.total === 0 ? 3 : 1 }, () => row.date),
  },
  {
    title: t('reports.colTotal'),
    key: 'total',
    sorter: (a: ReportBucket, b: ReportBucket) => a.total - b.total,
  },
  {
    title: t('reports.colSuccess'),
    key: 'success',
    sorter: (a: ReportBucket, b: ReportBucket) => a.success - b.success,
  },
  {
    title: t('reports.colFailed'),
    key: 'failed',
    sorter: (a: ReportBucket, b: ReportBucket) => a.failed - b.failed,
  },
  { title: t('reports.colRatio'), key: 'ratio', render: renderRatio },
])
</script>

<template>
  <n-space vertical :size="16">
    <n-card :title="t('reports.title')" size="small">
      <n-space vertical :size="12">
        <n-space align="center" :size="12">
          <n-text depth="3">{{ t('reports.window') }}</n-text>
          <n-radio-group v-model:value="days" size="small">
            <n-radio-button v-for="opt in windowOptions" :key="opt.value" :value="opt.value">
              {{ opt.label }}
            </n-radio-button>
          </n-radio-group>
          <n-text v-if="report" depth="3">
            {{ t('reports.range') }}：{{ report.from_date }} ~ {{ report.to_date }}（{{ report.timezone }}）
          </n-text>
        </n-space>

        <n-alert v-if="reportQuery.error.value" type="error" :show-icon="false">
          {{ String(reportQuery.error.value) }}
        </n-alert>

        <n-space :size="24" v-if="report">
          <n-text depth="3"
            >{{ t('reports.summaryTotal') }}：<n-text strong>{{ report.total }}</n-text></n-text
          >
          <n-text depth="3"
            >{{ t('reports.summarySuccess') }}：<n-text strong type="success">{{ report.success }}</n-text></n-text
          >
          <n-text depth="3"
            >{{ t('reports.summaryFailed') }}：<n-text strong type="error">{{ report.failed }}</n-text></n-text
          >
          <n-text depth="3"
            >{{ t('reports.summaryRatio') }}：<n-text strong>{{ formatRatio(report.ratio) }}</n-text></n-text
          >
        </n-space>

        <!-- P12 加载态：去掉 v-if="report"——首载时表格以 loading 态存在（空数据 + naive
             加载遮罩），避免「窗口选择器之下大片空白」；摘要区仍 v-if（数字不应猜） -->
        <n-data-table
          :columns="columns"
          :data="report?.buckets ?? []"
          :bordered="false"
          size="small"
          :loading="reportQuery.isFetching.value"
          :scroll-x="520"
        />
      </n-space>
    </n-card>

    <n-text depth="3" class="reports__note">{{ t('reports.metricsNote') }}</n-text>
  </n-space>
</template>

<style scoped>
.reports__ratio-track {
  width: 120px;
  height: 8px;
  border-radius: 4px;
  background: var(--n-color-embedded, rgba(0, 0, 0, 0.06));
  overflow: hidden;
}

.reports__ratio-bar {
  height: 100%;
  border-radius: 4px;
  background: var(--n-success-color, #18a058);
  transition: width 0.2s ease;
}

.reports__ratio-bar[data-low='true'] {
  background: var(--n-warning-color, #f0a020);
}

.reports__note {
  font-size: 12px;
}
</style>
