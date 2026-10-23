<script setup lang="ts">
/** 恢复向导步骤 2：options（恢复到哪 + 已存在文件 + E20 覆盖预览）。
 *
 * 从 `flow/index.vue` 拆出（P10/P11 遗留项，2026-10-23 补做）。拆分口径：
 *  - 可变态（targetMode / inplaceTarget / overwriteExisting）经 v-model 风格的
 *    显式 emits 上交父级——确认门描述与提交请求都要读；
 *  - 预览 fetch 的**状态**留父级（确认门预览插槽与 canOpenGate 共用），
 *    本组件只经 props 读、经 emit 触发 reload / load-more；
 *  - 预览表格列定义只服务本组件，随之内聚。
 *
 * 注：Duplicati 的 options 是**四**分区（恢复到哪 / 已存在文件 / Metadata / 高级）；
 * 本项目后端 RestoreRequest 仅 snapshot_id / target / paths / overwrite_mode 四个字段、
 * restic 层无 metadata 参数 → Metadata 与「高级」两分区**无后端支撑**，
 * 故不做占位死控件（待后端加契约后再补），此处只落地有后端支撑的两分区。
 */

import {
  NAlert,
  NButton,
  NCheckbox,
  NDataTable,
  NRadioButton,
  NRadioGroup,
  NSelect,
  NSpace,
  NText,
  type DataTableColumns,
} from 'naive-ui'
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { RestorePreviewItem } from '@/api/types'
import { DATETIME_FALLBACK, formatDateTimeOr } from '@/utils/datetime'
import { formatBytesOr } from '@/utils/format'

// 本组件脚本内不读 props（全部经模板消费），故不赋值——noUnusedLocals 会报未使用
defineProps<{
  /** inplace = 原位置（A7 默认）/ timestamp = 时间戳子目录零覆盖流。 */
  targetMode: 'timestamp' | 'inplace'
  isInPlace: boolean
  /** 原位入口置灰（快照无 paths 等）；置灰时模式已由父级回落时间戳流。 */
  inPlaceDisabled: boolean
  inPlaceBlockReason: string
  /** 已选原位目标（展示形态）；null = 未选。 */
  inplaceTarget: string | null
  inplaceTargetOptions: Array<{ label: string; value: string }>
  /** A7 覆盖布尔：默认不勾 = never 跳过已存在文件；勾选 = always 覆盖。 */
  overwriteExisting: boolean
  /** 原位流必须先有预览结果才允许开门（传统流无此约束）。 */
  canOpenGate: boolean
  previewItems: RestorePreviewItem[]
  previewStats: { overwriteCount: number; totalBytes: number; truncated: boolean } | null
  previewCursor: string | null
  previewLoading: boolean
  previewLoadingMore: boolean
  previewError: string
  /** 预览汇总字节数展示文案（父级组装，与确认门同一口径）。 */
  previewSizeText: string
  previewPolicy: string
}>()

const emit = defineEmits<{
  (e: 'update:targetMode', value: 'timestamp' | 'inplace'): void
  (e: 'update:inplaceTarget', value: string | null): void
  (e: 'update:overwriteExisting', value: boolean): void
  (e: 'reload-preview'): void
  (e: 'load-more-preview'): void
  (e: 'back'): void
  (e: 'next'): void
}>()

const { t } = useI18n()

const previewColumns = computed<DataTableColumns<RestorePreviewItem>>(() => [
  {
    title: t('restore.preview.colPath'),
    key: 'display_path',
    // 虚拟滚动下必须定宽：否则列宽随内容抖动、横向滚动条宽度算不准
    width: 300,
    ellipsis: { tooltip: true },
    // S1：只展示原路径语义，容器形态 path 不面向用户
    render: (row) => row.display_path,
  },
  {
    title: t('restore.preview.colSnapshotMtime'),
    key: 'snapshot_mtime',
    width: 170,
    render: (row) => formatDateTimeOr(row.snapshot_mtime, DATETIME_FALLBACK),
  },
  {
    title: t('restore.preview.colDiskMtime'),
    key: 'disk_mtime',
    width: 170,
    render: (row) => formatDateTimeOr(row.disk_mtime, DATETIME_FALLBACK),
  },
  {
    title: t('restore.preview.colSnapshotSize'),
    key: 'snapshot_size',
    width: 110,
    render: (row) => formatBytesOr(row.snapshot_size, t('common.none')),
  },
  {
    title: t('restore.preview.colDiskSize'),
    key: 'disk_size',
    width: 110,
    render: (row) => formatBytesOr(row.disk_size, t('common.none')),
  },
])
</script>

<template>
  <!-- 分区一：恢复到哪 -->
  <n-space vertical :size="6">
    <n-text strong>{{ t('restore.options.whereTitle') }}</n-text>
    <n-radio-group
      :value="targetMode"
      size="small"
      @update:value="emit('update:targetMode', $event)"
    >
      <!-- A7：原位置为默认选项（对齐 Duplicati）；自定义目录 = 时间戳子目录零覆盖流 -->
      <n-radio-button value="inplace" :disabled="inPlaceDisabled">
        {{ t('restore.targetInPlace') }}
      </n-radio-button>
      <n-radio-button value="timestamp">{{ t('restore.targetTimestamp') }}</n-radio-button>
    </n-radio-group>
    <n-text depth="3">
      {{ isInPlace ? t('restore.targetInPlaceNote') : t('restore.targetTimestampNote') }}
    </n-text>
    <!-- 原位恢复不可用时置灰 + 原因（后端双重锁兜底，前端只做提示性限制） -->
    <n-alert v-if="inPlaceDisabled" type="warning" :show-icon="false">
      <n-text depth="3">{{ inPlaceBlockReason }}</n-text>
    </n-alert>
    <!-- 原位目标：限该快照 paths 命中的目录（后端双重锁兜底，前端只做提示性限制） -->
    <template v-if="isInPlace">
      <n-text strong>{{ t('restore.inplace.targetTitle') }}</n-text>
      <n-select
        :value="inplaceTarget"
        :options="inplaceTargetOptions"
        :placeholder="t('restore.inplace.targetPlaceholder')"
        @update:value="emit('update:inplaceTarget', $event)"
      />
      <n-text depth="3">{{ t('restore.inplace.targetHint') }}</n-text>
    </template>
  </n-space>

  <!-- 分区二：已存在文件（覆盖策略 + E20 预览清单） -->
  <n-space vertical :size="6">
    <n-text strong>{{ t('restore.options.existingTitle') }}</n-text>
    <template v-if="isInPlace">
      <!-- A7 覆盖布尔（Duplicati --overwrite）：默认不勾 = never 跳过已存在文件 -->
      <n-checkbox
        :checked="overwriteExisting"
        @update:checked="emit('update:overwriteExisting', $event)"
      >
        {{ t('restore.overwrite.label') }}
      </n-checkbox>
      <n-text depth="3">{{ t('restore.overwrite.hint') }}</n-text>
    </template>
    <n-text v-else depth="3">{{ t('restore.options.timestampNoOverwrite') }}</n-text>
  </n-space>

  <template v-if="isInPlace">
    <n-space vertical :size="8">
      <n-space align="center" :size="10">
        <n-text strong>{{ t('restore.preview.title') }}</n-text>
        <n-button size="tiny" quaternary :loading="previewLoading" @click="emit('reload-preview')">
          {{ t('restore.preview.reload') }}
        </n-button>
      </n-space>
      <n-text v-if="previewStats" depth="2">
        {{ t('restore.preview.overwriteCount', { n: previewStats.overwriteCount }) }}
        · {{ t('restore.preview.totalBytes', { size: previewSizeText }) }}
      </n-text>
      <n-text v-if="previewStats" depth="3">{{ previewPolicy }}</n-text>
      <n-alert v-if="previewStats?.truncated" type="warning" :show-icon="false">
        {{ t('restore.preview.truncated') }}
      </n-alert>
      <n-alert v-if="previewError" type="error" :show-icon="false">{{ previewError }}</n-alert>
      <!-- 大列表纪律：虚拟滚动 + 固定行高；游标分页靠下方「加载更多」续拉 -->
      <n-data-table
        v-if="previewItems.length > 0"
        size="small"
        :columns="previewColumns"
        :data="previewItems"
        :max-height="320"
        :loading="previewLoading"
        virtual-scroll
        :row-key="(row: RestorePreviewItem) => row.path"
      />
      <n-text v-else-if="!previewLoading && !previewError" depth="3">
        {{ t('restore.preview.empty') }}
      </n-text>
      <n-button
        v-if="previewCursor"
        size="tiny"
        quaternary
        :loading="previewLoadingMore"
        @click="emit('load-more-preview')"
      >
        {{ t('restore.preview.loadMore') }}
      </n-button>
    </n-space>
  </template>
  <n-text v-else depth="3">{{ t('restore.stepConfirm') }}</n-text>
  <!-- P10 页脚两端对齐：左上一步 / 右开门 -->
  <n-space justify="space-between" align="center">
    <n-button quaternary @click="emit('back')">{{ t('confirm.back') }}</n-button>
    <n-button type="primary" :disabled="!canOpenGate" @click="emit('next')">
      {{ t('confirm.next') }}
    </n-button>
  </n-space>
</template>
