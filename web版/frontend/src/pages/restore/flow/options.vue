<script setup lang="ts">
/** 恢复向导步骤 2：恢复选项（P16 / UI全量改造方案 §4.9——按 Duplicati restore-flow
 * `options.component` 复刻：三张折叠卡 + 两端页脚）。
 *
 * 从 `flow/index.vue` 拆出（v1.23）；P16（owner 2026-10-25 五裁决）重写为：
 *  - 卡片①「恢复到哪」（禁折叠）：raised 单选卡「原位置 / 选择位置」——原位置内嵌
 *    原位目标下拉（限快照 paths，后端双重锁兜底）；选择位置展开 `RestoreTargetPicker`
 *    （D2 树双模式：runtime.restoreRoot 单一事实源 + 浏览树/手输二模）；
 *  - 卡片②「已存在文件」（禁折叠）：A7 覆盖布尔 + **E20 预览区并入**（D4=A）；
 *  - 卡片③「Metadata / 高级」：**D1/D3 裁决取消**（restic 无 metadata 参数 / 红线 S2），
 *    不渲染占位死控件，留档 §4.9.6；
 *  - 页脚两端对齐：返回（左）/ 提交（右 raised primary + loading，开确认门）。
 *
 * 可变态经显式 emits 上交父级（确认门描述与提交请求都要读）；预览 fetch 的**状态**
 * 留父级（确认门预览插槽与 canOpenGate 共用），本组件只经 props 读、经 emit 触发。
 */

import {
  NAlert,
  NButton,
  NCheckbox,
  NDataTable,
  NIcon,
  NSelect,
  NSpace,
  NText,
  type DataTableColumns,
} from 'naive-ui'
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { RestorePreviewItem } from '@/api/types'
import { IconArrowLeft, IconArrowRight } from '@/components/icons/icons'
import RestoreOptionCard from '@/components/restore/RestoreOptionCard.vue'
import RestoreTargetPicker from '@/components/restore/RestoreTargetPicker.vue'
import { DATETIME_FALLBACK, formatDateTimeOr } from '@/utils/datetime'
import { formatBytesOr } from '@/utils/format'

const props = defineProps<{
  /** original = 原位置（A7 默认）/ pickLocation = 选择位置（/restore 子树）。 */
  restoreFrom: 'original' | 'pickLocation'
  isInPlace: boolean
  /** 原位入口置灰（快照无 paths 等）；置灰时模式已由父级回落选择位置。 */
  inPlaceDisabled: boolean
  inPlaceBlockReason: string
  /** 已选原位目标（展示形态）；null = 未选。 */
  inplaceTarget: string | null
  inplaceTargetOptions: Array<{ label: string; value: string }>
  /** 已选恢复目标（容器形态，/restore 子树内；选择位置流提交值）。 */
  customTarget: string
  /** 恢复根（生效值，部署形态派生：docker=/restore、host=/）——文案与选择器同源。 */
  restoreRoot: string
  /** A7 覆盖布尔：默认不勾 = never 跳过已存在文件；勾选 = always 覆盖。 */
  overwriteExisting: boolean
  /** 提交中（确认门 loading 同源；页脚提交按钮共用）。 */
  submitting: boolean
  /** 原位流必须先有预览结果才允许开门（选择位置流无此约束）。 */
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
  (e: 'update:restoreFrom', value: 'original' | 'pickLocation'): void
  (e: 'update:inplaceTarget', value: string | null): void
  (e: 'update:customTarget', value: string): void
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

/** raised 单选卡两项（Duplicati sh-radio raised primary 同款形态：整行可点 +
 *  选中主色描边 + 单选圆点；键盘可达沿用 repo A4 类型卡 button 范式）。
 *  文案里的恢复根随部署形态（docker /restore、host /），不写死目录名。 */
const whereOptions = computed(() => [
  {
    value: 'original' as const,
    label: t('restore.options.original'),
    note: t('restore.options.originalNote'),
  },
  {
    value: 'pickLocation' as const,
    label: t('restore.options.pickLocation'),
    note: t('restore.options.pickLocationNote', { root: props.restoreRoot || '/' }),
  },
])

function onWhereKeydown(event: KeyboardEvent, value: 'original' | 'pickLocation'): void {
  if (event.key !== 'Enter' && event.key !== ' ') {
    return
  }
  event.preventDefault()
  emit('update:restoreFrom', value)
}
</script>

<template>
  <n-space vertical :size="12" class="restore-options">
    <n-text strong class="restore-options__title">{{ t('restore.options.title') }}</n-text>

    <!-- 卡片①：恢复到哪（禁折叠；Duplicati 卡片① 同款） -->
    <RestoreOptionCard :title="t('restore.options.whereTitle')" disable-toggle>
      <div class="restore-opt-list" role="radiogroup" :aria-label="t('restore.options.whereTitle')">
        <button
          v-for="opt in whereOptions"
          :key="opt.value"
          type="button"
          role="radio"
          class="restore-opt-card"
          :class="{
            'is-selected': restoreFrom === opt.value,
            'is-disabled': opt.value === 'original' && inPlaceDisabled,
          }"
          :aria-checked="restoreFrom === opt.value ? 'true' : 'false'"
          :disabled="opt.value === 'original' && inPlaceDisabled"
          @click="emit('update:restoreFrom', opt.value)"
          @keydown="onWhereKeydown($event, opt.value)"
        >
          <span class="restore-opt-card__dot" :class="{ 'is-on': restoreFrom === opt.value }" aria-hidden="true" />
          <span class="restore-opt-card__body">
            <n-text strong>{{ opt.label }}</n-text>
            <n-text depth="3" class="restore-opt-card__note">{{ opt.note }}</n-text>
          </span>
        </button>
      </div>

      <!-- 原位不可用时置灰 + 原因（后端双重锁兜底，前端只做提示性限制） -->
      <n-alert v-if="inPlaceDisabled" type="warning" :show-icon="false">
        <n-text depth="3">{{ inPlaceBlockReason }}</n-text>
      </n-alert>

      <!-- 原位置：目标目录限该快照 paths 命中的目录 -->
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

      <!-- 选择位置：路径选择器（D2 树双模式） -->
      <RestoreTargetPicker
        v-else
        :model-value="customTarget"
        @update:model-value="emit('update:customTarget', $event)"
      />
    </RestoreOptionCard>

    <!-- 卡片②：已存在文件（禁折叠；E20 预览区 D4=A 并入本卡） -->
    <RestoreOptionCard :title="t('restore.options.existingTitle')" disable-toggle>
      <template v-if="isInPlace">
        <!-- A7 覆盖布尔（Duplicati --overwrite）：默认不勾 = never 跳过已存在文件 -->
        <n-checkbox
          :checked="overwriteExisting"
          @update:checked="emit('update:overwriteExisting', $event)"
        >
          {{ t('restore.overwrite.label') }}
        </n-checkbox>
        <n-text depth="3">{{ t('restore.overwrite.hint') }}</n-text>

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
      <n-text v-else depth="3">{{ t('restore.options.timestampNoOverwrite') }}</n-text>
    </RestoreOptionCard>

    <!-- 页脚两端对齐（Duplicati .form-actions：左 Go back / 右 Submit raised primary） -->
    <n-space justify="space-between" align="center" class="restore-options__footer">
      <n-button quaternary @click="emit('back')">
        <template #icon>
          <n-icon :size="16"><IconArrowLeft /></n-icon>
        </template>
        {{ t('restore.options.back') }}
      </n-button>
      <n-button type="primary" :disabled="!canOpenGate" :loading="submitting" @click="emit('next')">
        <template #icon>
          <n-icon :size="16"><IconArrowRight /></n-icon>
        </template>
        {{ t('restore.options.submit') }}
      </n-button>
    </n-space>
  </n-space>
</template>

<style scoped>
.restore-options__title {
  font-size: 15px;
}

/* raised 单选卡（Duplicati sh-radio raised primary；repo A4 类型卡 button 范式） */
.restore-opt-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.restore-opt-card {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  width: 100%;
  padding: 8px 12px;
  border: 1px solid var(--n-border-color, rgba(128, 128, 128, 0.35));
  border-radius: 8px;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition: border-color 0.2s var(--n-bezier, ease);
}

.restore-opt-card.is-selected {
  border-color: var(--n-color-primary, #3d5bd9);
}

/* 置灰 = Duplicati .inactive 语义（pointer-events 交给 disabled 属性，此处仅视觉降档） */
.restore-opt-card.is-disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.restore-opt-card:focus-visible {
  outline: 2px solid var(--n-color-primary, #3d5bd9);
  outline-offset: 2px;
}

.restore-opt-card__dot {
  flex: none;
  width: 14px;
  height: 14px;
  margin-top: 3px;
  border: 1.5px solid var(--n-border-color, rgba(128, 128, 128, 0.35));
  border-radius: 999px;
}

.restore-opt-card__dot.is-on {
  border-color: var(--n-color-primary, #3d5bd9);
  /* 内环用卡片面色（--n-color 在 n-card 内解析为 cardColor，亮/暗自适应） */
  background: var(--n-color-primary, #3d5bd9);
  box-shadow: inset 0 0 0 2px var(--n-color, #ffffff);
}

.restore-opt-card__body {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.restore-opt-card__note {
  display: block;
  font-size: 12px;
}

.restore-options__footer {
  margin-top: 4px;
}
</style>
