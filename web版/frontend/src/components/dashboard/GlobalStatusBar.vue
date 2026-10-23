<script setup lang="ts">
/** 全局状态条（P6 L2 共用形态层；方案 §4.5.6 / §4.7 P6 批次）。
 *
 * 形态来源：Duplicati 全局 status-bar（ngclient status-bar.component.html：
 * 当前备份进度条 + Stop/Abort 二段 + 限速（speedometer）入口）。
 *
 * 消费位：P7 主页顶部（全局进度常驻条）。
 *
 * 与后端能力对齐（2026-10-23 owner 裁决 A5 后已补齐）：
 * - **Stop** → `POST /api/jobs/{id}/cancel`（SIGTERM → 30s 宽限 → SIGKILL）；
 * - **Abort 二段** → `POST /api/jobs/{id}/cancel?force=true`（跳过宽限直接 SIGKILL，
 *   服务端审计 `job.abort`）。交互对齐 Duplicati：点 Stop 后进入 isStopping 态（按钮禁用 +
 *   出现 Abort），供「已请求停止但迟迟不退出」时一键强杀；
 * - 限速（owner 2026-10-22 任务级裁决）：后端限速是**任务级配置**
 *   （backup_tasks.limit_up/down_kibps，创建时生效——备份进程启动时读入
 *   restic argv），restic CLI 无控制通道、运行中不可改 → 状态条**不设运行中
 *   限速入口**（同 pause/resume 列降级），仅保留 limitText 只读展示当前任务
 *   限速；改限速走任务编辑表单（TaskWizard step①）。
 */

import { NButton, NIcon, NProgress, NSpace, NText } from 'naive-ui'
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useJobStream } from '@/composables/useJobStream'
import type { JobProgressPayload } from '@/api/types'
import { IconStop } from '@/components/icons/icons'

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
  (e: 'abort'): void
}>()

const { t } = useI18n()

/** isStopping 态（Duplicati 对位）：点过 Stop 之后才允许 Abort —— 避免「还没请求停止就强杀」。 */
const stopping = ref(false)

function onStop(): void {
  stopping.value = true
  emit('stop')
}

function onAbort(): void {
  emit('abort')
}

// 换作业（或作业结束）后 isStopping 归零，避免下个作业一上来就带着 Abort
watch(
  () => props.jobId,
  () => {
    stopping.value = false
    // C4（owner 2026-10-23 裁决）：进度与速率一并归零——否则同实例换作业会先显示上一个
    // 作业的 100% 与旧速率，直到新作业首个 progress 帧到达（视觉上像「已完成又倒回去」）。
    percent.value = 0
    filesDone.value = 0
    totalFiles.value = 0
    rateKib.value = 0
    lastBytes = 0
    lastAt = 0
  },
)

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
      <!-- Stop/Abort 二段（Duplicati 对位）：Stop 后进入 stopping 态 → 出现 Abort 强杀 -->
      <n-button size="small" type="warning" secondary :disabled="stopping" @click="onStop">
        <template #icon>
          <n-icon :size="14"><IconStop /></n-icon>
        </template>
        {{ stopping ? t('dashboard.statusBar.stopping') : t('dashboard.statusBar.stop') }}
      </n-button>
      <n-button v-if="stopping" size="small" type="error" secondary @click="onAbort">
        {{ t('dashboard.statusBar.abort') }}
      </n-button>
    </n-space>
  </div>
</template>
