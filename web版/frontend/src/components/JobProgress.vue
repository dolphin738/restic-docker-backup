<script setup lang="ts">
import {
  NButton,
  NCollapse,
  NCollapseItem,
  NDrawer,
  NDrawerContent,
  NLog,
  NProgress,
  NSpace,
  NTag,
  NText,
} from 'naive-ui'
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import type { JobDonePayload, JobProgressPayload } from '@/api/types'
import { useJobStream, type JobStreamState } from '@/composables/useJobStream'

const props = defineProps<{
  jobId: string
  /** 卡片标题；缺省直接展示 jobId。 */
  title?: string
}>()

const emit = defineEmits<{
  (e: 'done', payload: JobDonePayload): void
  (e: 'failed', payload: JobDonePayload): void
}>()

const { t } = useI18n()

const percent = ref(0)
const bytesDone = ref(0)
const totalBytes = ref(0)
const filesDone = ref(0)
const totalFiles = ref(0)
const skippedCount = ref(0)
const rateKib = ref(0)
const drawerOpen = ref(false)

let lastBytes = 0
let lastAt = 0

const stream = useJobStream(
  () => props.jobId,
  {
    onProgress: (payload: JobProgressPayload) => applyProgress(payload),
    onDone: (payload: JobDonePayload) => finalize(payload, 'done'),
    onFailed: (payload: JobDonePayload) => finalize(payload, 'failed'),
  },
)

function applyProgress(payload: JobProgressPayload): void {
  totalBytes.value = payload.total_bytes ?? totalBytes.value
  totalFiles.value = payload.total_files ?? totalFiles.value
  const nextBytes = payload.bytes_done ?? bytesDone.value
  const now = Date.now()
  if (lastAt > 0 && now > lastAt && nextBytes > lastBytes) {
    const seconds = (now - lastAt) / 1000
    rateKib.value = Math.round((nextBytes - lastBytes) / 1024 / seconds)
  }
  lastBytes = nextBytes
  lastAt = now
  bytesDone.value = nextBytes
  filesDone.value = payload.files_done ?? filesDone.value
  if (payload.skipped_delta) {
    skippedCount.value += payload.skipped_delta
  }
  const byBytes = totalBytes.value > 0 ? (nextBytes / totalBytes.value) * 100 : null
  percent.value = clampPercent(payload.percent ?? byBytes ?? percent.value)
}

function finalize(payload: JobDonePayload, kind: 'done' | 'failed'): void {
  bytesDone.value = payload.bytes_done ?? bytesDone.value
  filesDone.value = payload.files_done ?? filesDone.value
  skippedCount.value = payload.skipped_count || skippedCount.value
  if (kind === 'done') {
    percent.value = 100
    emit('done', payload)
  } else {
    emit('failed', payload)
  }
}

function clampPercent(value: number): number {
  if (!Number.isFinite(value)) {
    return 0
  }
  return Math.min(100, Math.max(0, Math.round(value)))
}

const stateTagType = computed(() => {
  const map: Record<JobStreamState, 'default' | 'info' | 'success' | 'warning'> = {
    idle: 'default',
    connecting: 'info',
    open: 'success',
    reconnecting: 'warning',
    closed: 'default',
  }
  return map[stream.state.value]
})

const progressStatus = computed(() => (stream.state.value === 'closed' ? 'default' : 'success'))

const filesText = computed(() =>
  totalFiles.value > 0
    ? t('job.files', { done: filesDone.value, total: totalFiles.value })
    : t('job.filesOnly', { done: filesDone.value }),
)

const logText = computed(() => stream.logs.value.join('\n'))
</script>

<template>
  <div class="job-progress">
    <n-space align="center" justify="space-between">
      <n-text strong>{{ title || jobId }}</n-text>
      <n-space align="center" :size="6">
        <n-tag size="small" round :type="stateTagType">{{ t(`job.state.${stream.state.value}`) }}</n-tag>
        <n-button size="tiny" quaternary @click="drawerOpen = true">{{ t('job.viewLog') }}</n-button>
      </n-space>
    </n-space>

    <n-progress
      class="job-progress__bar"
      :percentage="percent"
      :status="progressStatus"
      :height="12"
      :border-radius="6"
      :show-indicator="false"
    />

    <n-space align="center" :size="12">
      <n-text depth="3">{{ percent }}%</n-text>
      <n-text depth="3">{{ filesText }}</n-text>
      <n-text depth="3">{{ t('job.rate', { n: rateKib }) }}</n-text>
      <n-text depth="3">{{ t('job.cursor', { n: stream.lastEventId.value }) }}</n-text>
    </n-space>

    <template v-if="skippedCount > 0">
      <n-collapse class="job-progress__skipped">
        <n-collapse-item :title="t('job.skippedTitle', { n: skippedCount })" name="skipped">
          <n-text depth="3">{{ t('job.skippedHint') }}</n-text>
        </n-collapse-item>
      </n-collapse>
    </template>

    <n-drawer v-model:show="drawerOpen" :width="680" placement="right">
      <n-drawer-content :title="t('job.logTitle')" closable>
        <n-log :log="logText || t('job.logEmpty')" :rows="24" trim />
      </n-drawer-content>
    </n-drawer>
  </div>
</template>

<style scoped>
.job-progress__bar {
  margin: 8px 0;
}

.job-progress__skipped {
  margin-top: 8px;
}
</style>
