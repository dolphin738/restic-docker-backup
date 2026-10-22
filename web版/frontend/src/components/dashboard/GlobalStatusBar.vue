<script setup lang="ts">
/** 全局状态条（P6 L2 共用形态层；方案 §4.5.6 / §4.7 P6 批次）。
 *
 * 形态来源：Duplicati 全局 status-bar（ngclient status-bar.component.html：
 * 当前备份进度条 + Stop/Abort 二段 + 限速（speedometer）入口）。
 *
 * 消费位：P7 主页顶部（全局进度常驻条）。
 *
 * 与后端能力的差异标注（如实标注，勿造假 UI）：
 * - Stop → 消费方接 POST /api/jobs/{id}/cancel（SIGTERM → 30s 宽限 → SIGKILL）；
 * - **Abort 二段暂缺**：Duplicati 的 isStopping→abort 是「优雅停止后仍可强杀」，
 *   本后端 cancel 无 grace=0 强杀路径（routers/jobs.py:202 无参数），故不渲染
 *   不可用的 Abort 按钮——待后端补强杀端点或 owner 裁决丢弃该段后再加；
 * - 限速：后端限速是**任务级配置**（backup_tasks.limit_up/down_kibps，创建时生效），
 *   无运行中改限速 API → 限速入口 = 只读展示当前任务限速 + 点击外抛 throttle
 *   事件（消费方跳任务编辑页）。
 */

import { NButton, NIcon, NProgress, NSpace, NText, NTooltip } from 'naive-ui'
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useJobStream } from '@/composables/useJobStream'
import type { JobProgressPayload } from '@/api/types'
import { IconGauge, IconStop } from '@/components/icons/HomeIcons'

const props = defineProps<{
  /** 当前作业 id；null/空 = 无进行中作业（整条不渲染）。 */
  jobId?: string | null
  /** 条首作业名（缺省回落 jobId）。 */
  title?: string
  /** 限速只读文案（任务级配置派生，如「上传 0 · 下载 0 KiB/s（不限速）」）。 */
  limitText?: string
}>()

const emit = defineEmits<{
  (e: 'stop'): void
  (e: 'throttle'): void
}>()

const { t } = useI18n()

const percent = ref(0)
const filesDone = ref(0)
const totalFiles = ref(0)
const rateKib = ref(0)
let lastBytes = 0
let lastAt = 0

// jobId 为空时 useJobStream 不开流（内部 watch immediate + 空 id 早退）；
// 整条可见性由模板 v-if 控制，避免占位空条。
useJobStream(
  () => props.jobId ?? '',
  {
    onProgress: (payload: JobProgressPayload) => {
      totalFiles.value = payload.total_files ?? totalFiles.value
      const nextBytes = payload.bytes_done ?? 0
      const now = Date.now()
      if (lastAt > 0 && now > lastAt && nextBytes > lastBytes) {
        rateKib.value = Math.round((nextBytes - lastBytes) / 1024 / ((now - lastAt) / 1000))
      }
      lastBytes = nextBytes
      lastAt = now
      filesDone.value = payload.files_done ?? filesDone.value
      const byBytes = payload.total_bytes ? (nextBytes / payload.total_bytes) * 100 : null
      const next = payload.percent ?? byBytes ?? percent.value
      percent.value = Number.isFinite(next) ? Math.min(100, Math.max(0, Math.round(next))) : percent.value
    },
  },
)

const filesText = computed(() =>
  totalFiles.value > 0
    ? t('job.files', { done: filesDone.value, total: totalFiles.value })
    : t('job.filesOnly', { done: filesDone.value }),
)

const barTitle = computed(() => props.title || props.jobId || '')
</script>

<template>
  <div v-if="jobId" class="rw-status-bar">
    <n-text strong class="rw-status-bar__title">{{ barTitle }}</n-text>
    <n-progress
      class="rw-status-bar__bar"
      :percentage="percent"
      :height="8"
      :border-radius="4"
      :show-indicator="false"
    />
    <n-space :size="12" :wrap="false" class="rw-status-bar__metrics">
      <n-text depth="3" class="tabular-nums">{{ percent }}%</n-text>
      <n-text depth="3" class="tabular-nums">{{ filesText }}</n-text>
      <n-text depth="3" class="tabular-nums">{{ t('job.rate', { n: rateKib }) }}</n-text>
    </n-space>
    <n-space :size="6" :wrap="false" align="center" class="rw-status-bar__actions">
      <n-text v-if="limitText" depth="3" class="rw-status-bar__limit">{{ limitText }}</n-text>
      <n-tooltip trigger="hover">
        <template #trigger>
          <n-button size="small" secondary circle :aria-label="t('dashboard.statusBar.throttle')" @click="emit('throttle')">
            <template #icon>
              <n-icon :size="16"><IconGauge /></n-icon>
            </template>
          </n-button>
        </template>
        {{ limitText || t('dashboard.statusBar.throttle') }}
      </n-tooltip>
      <n-button size="small" type="warning" secondary @click="emit('stop')">
        <template #icon>
          <n-icon :size="14"><IconStop /></n-icon>
        </template>
        {{ t('dashboard.statusBar.stop') }}
      </n-button>
    </n-space>
  </div>
</template>
