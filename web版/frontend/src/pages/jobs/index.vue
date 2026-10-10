<script setup lang="ts">
import { useQuery } from '@tanstack/vue-query'
import {
  NAlert,
  NButton,
  NCard,
  NDrawer,
  NDrawerContent,
  NEmpty,
  NCollapse,
  NCollapseItem,
  NLog,
  NPopconfirm,
  NSpace,
  NTag,
  NText,
  useMessage,
} from 'naive-ui'
import { computed, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { cancelJob, getJobLog, listActiveJobs } from '@/api/jobs'
import { useUiStore } from '@/stores/ui'
import JobProgress from '@/components/JobProgress.vue'

const { t } = useI18n()
const ui = useUiStore()

const message = useMessage()
ui.setMessageApi(message)
onUnmounted(() => ui.setMessageApi(null))

// E13 只开放 running/pending
const jobsQuery = useQuery({
  queryKey: ['jobs', 'active'],
  queryFn: () => listActiveJobs(50),
  refetchInterval: 15_000,
})

const jobs = computed(() => jobsQuery.data.value?.items ?? [])

// ---------------------------------------------------------------- 取消
const cancellingId = ref('')

async function onCancel(jobId: string): Promise<void> {
  cancellingId.value = jobId
  try {
    await cancelJob(jobId)
    message.success(t('jobs.cancelSubmitted'))
    await jobsQuery.refetch()
  } finally {
    cancellingId.value = ''
  }
}

// ---------------------------------------------------------------- 日志抽屉（E27，默认 64 KiB 逐级放大）
interface LogState {
  jobId: string
  content: string
  /** 实际已拉取字节数（响应 tail_bytes）；undefined 表示还没拉过（用服务端默认 64 KiB）。 */
  tailBytes: number | null
  truncated: boolean
  loading: boolean
  loadingMore: boolean
}

const DEFAULT_TAIL = 65_536
const MAX_TAIL = 1_048_576

const logShow = ref(false)
const log = ref<LogState>({ jobId: '', content: '', tailBytes: null, truncated: false, loading: false, loadingMore: false })

function nextTailSize(current: number | null): number {
  if (current === null) {
    return DEFAULT_TAIL * 4 // 64 KiB → 256 KiB
  }
  return Math.min(current * 2, MAX_TAIL)
}

async function openLog(jobId: string): Promise<void> {
  logShow.value = true
  log.value = { jobId, content: '', tailBytes: null, truncated: false, loading: true, loadingMore: false }
  try {
    const data = await getJobLog(jobId)
    log.value = {
      jobId,
      content: data.content,
      tailBytes: data.tail_bytes,
      truncated: data.truncated,
      loading: false,
      loadingMore: false,
    }
  } catch {
    log.value.loading = false
  }
}

/** 逐级放大尾部窗口（64 KiB → 256 KiB → … → 1 MiB 上限）。 */
async function loadMoreLog(): Promise<void> {
  if (log.value.loadingMore || !log.value.truncated) {
    return
  }
  const target = nextTailSize(log.value.tailBytes)
  if (log.value.tailBytes !== null && log.value.tailBytes >= MAX_TAIL) {
    return
  }
  log.value.loadingMore = true
  try {
    const data = await getJobLog(log.value.jobId, target)
    log.value.content = data.content
    log.value.tailBytes = data.tail_bytes
    log.value.truncated = data.truncated
  } finally {
    log.value.loadingMore = false
  }
}

const canLoadMore = computed(
  () => log.value.truncated && (log.value.tailBytes === null || log.value.tailBytes < MAX_TAIL),
)

// ---------------------------------------------------------------- skipped 明细（数量取 E14，明细从 E27 文本挑）
interface SkippedEntry {
  item: string
  error: string
}

const activeSkippedCount = computed(
  () => jobs.value.find((job) => job.id === log.value.jobId)?.skipped_count ?? 0,
)

const skippedEntries = computed<SkippedEntry[]>(() => {
  const entries: SkippedEntry[] = []
  for (const line of log.value.content.split('\n')) {
    const trimmed = line.trim()
    if (!trimmed.startsWith('{')) {
      continue
    }
    try {
      const payload = JSON.parse(trimmed) as Record<string, unknown>
      if (payload['message_type'] !== 'error' || payload['during'] !== 'archival') {
        continue
      }
      const item = typeof payload['item'] === 'string' ? payload['item'] : trimmed
      entries.push({
        item,
        error: typeof payload['error'] === 'string' ? payload['error'] : '',
      })
    } catch {
      // 非完整 JSON 行（半行截断）跳过
    }
  }
  return entries
})
</script>

<template>
  <n-space vertical :size="16">
    <n-alert v-if="jobsQuery.error.value" type="error" :show-icon="false">
      {{ String(jobsQuery.error.value) }}
    </n-alert>

    <template v-if="jobs.length">
      <n-card v-for="job in jobs" :key="job.id" size="small">
        <n-space vertical :size="10">
          <n-space align="center" justify="space-between">
            <n-space align="center" :size="8">
              <n-tag size="small" round>{{ job.type }}</n-tag>
              <n-tag size="small" round :type="job.status === 'running' ? 'success' : 'info'">
                {{ job.status }}
              </n-tag>
              <n-text depth="3">{{ job.created_at }}</n-text>
            </n-space>
            <n-space :size="6">
              <n-button size="tiny" secondary @click="openLog(job.id)">{{ t('job.viewLog') }}</n-button>
              <n-popconfirm @positive-click="onCancel(job.id)">
                <template #trigger>
                  <n-button size="tiny" type="warning" :loading="cancellingId === job.id">
                    {{ t('jobs.cancel') }}
                  </n-button>
                </template>
                {{ t('jobs.cancelConfirm') }}
              </n-popconfirm>
            </n-space>
          </n-space>
          <JobProgress :job-id="job.id" :title="`${job.type} · ${job.status}`" />
        </n-space>
      </n-card>
    </template>
    <n-card v-else size="small">
      <n-empty :description="t('dashboard.jobsEmpty')" />
    </n-card>

    <!-- 日志抽屉：首次默认 64 KiB，按需逐级放大到 1 MiB -->
    <n-drawer v-model:show="logShow" :width="760" placement="right">
      <n-drawer-content :title="t('jobs.logTitle')" closable>
        <n-space vertical :size="10">
          <template v-if="activeSkippedCount > 0">
            <n-collapse size="small">
              <n-collapse-item :title="t('job.skippedTitle', { n: activeSkippedCount })" name="skipped">
                <n-space vertical :size="6">
                  <n-text depth="3">{{ t('jobs.skippedTailNote') }}</n-text>
                  <template v-if="skippedEntries.length">
                    <div v-for="(entry, index) in skippedEntries" :key="`${entry.item}-${index}`" class="jobs__skipped">
                      <n-text depth="2">{{ entry.item }}</n-text>
                      <n-text v-if="entry.error" depth="3">{{ entry.error }}</n-text>
                    </div>
                  </template>
                  <n-text v-else depth="3">{{ t('jobs.skippedNotInTail') }}</n-text>
                </n-space>
              </n-collapse-item>
            </n-collapse>
          </template>

          <n-log :log="log.content || t('job.logEmpty')" :rows="26" trim />
          <n-text depth="3">
            {{ t('jobs.logTailNote', { n: log.tailBytes ?? 0 }) }}
          </n-text>
          <n-space>
            <n-button
              v-if="canLoadMore"
              size="small"
              secondary
              :loading="log.loadingMore"
              @click="loadMoreLog"
            >
              {{ t('jobs.logLoadMore') }}
            </n-button>
            <n-text v-else-if="log.truncated" depth="3">{{ t('jobs.logMaxTail') }}</n-text>
          </n-space>
        </n-space>
      </n-drawer-content>
    </n-drawer>
  </n-space>
</template>

<style scoped>
.jobs__skipped {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 4px 8px;
  border-left: 2px solid rgba(250, 208, 44, 0.6);
  font-size: 12px;
}
</style>
