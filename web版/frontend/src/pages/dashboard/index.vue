<script setup lang="ts">
import { useQuery } from '@tanstack/vue-query'
import {
  NAlert,
  NButton,
  NCard,
  NEmpty,
  NGi,
  NGrid,
  NSpace,
  NStatistic,
  NTag,
  NText,
} from 'naive-ui'
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { isAppError } from '@/api/http'
import { listActiveJobs } from '@/api/jobs'
import { getRepoStatus } from '@/api/repo'
import JobProgress from '@/components/JobProgress.vue'
import TierCard from '@/components/TierCard.vue'
import { useUiStore } from '@/stores/ui'

const { t } = useI18n()
const ui = useUiStore()

// E30 服务端 30s 缓存：这里按 60s 轮询 + 手动刷新，禁止秒级轮询
const repoQuery = useQuery({
  queryKey: ['repo', 'status'],
  queryFn: getRepoStatus,
  refetchInterval: 60_000,
})

// E13 只开放 running/pending 过滤
const jobsQuery = useQuery({
  queryKey: ['jobs', 'active'],
  queryFn: () => listActiveJobs(20),
  refetchInterval: 15_000,
})

const repoStatus = computed(() => repoQuery.data.value ?? null)
const repoLoading = computed(() => repoQuery.isFetching.value)
const repoError = computed(() => toText(repoQuery.error.value))
const jobs = computed(() => jobsQuery.data.value?.items ?? [])
const jobsError = computed(() => toText(jobsQuery.error.value))

const capacityText = computed(() =>
  repoStatus.value?.capacity.size ? formatBytes(repoStatus.value.capacity.size) : t('common.none'),
)
const snapsText = computed(() =>
  repoStatus.value?.capacity.snaps === null || repoStatus.value?.capacity.snaps === undefined
    ? t('common.none')
    : String(repoStatus.value.capacity.snaps),
)

function toText(error: unknown): string {
  if (!error) {
    return ''
  }
  return isAppError(error) ? error.message : t('errors.UNKNOWN')
}

function formatBytes(value: number): string {
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  let size = value
  let index = 0
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024
    index += 1
  }
  return `${size.toFixed(index === 0 ? 0 : 1)} ${units[index]}`
}
</script>

<template>
  <n-space vertical :size="16">
    <n-grid cols="1 900:3" :x-gap="16" :y-gap="16" responsive="screen">
      <!-- 仓库状态卡 -->
      <n-gi>
        <n-card :title="t('dashboard.repoCard')" size="small">
          <template #header-extra>
            <n-button size="tiny" secondary :loading="repoLoading" @click="repoQuery.refetch()">
              {{ t('common.refresh') }}
            </n-button>
          </template>

          <n-space vertical :size="10">
            <n-space align="center" :size="8">
              <n-tag size="small" round :type="repoStatus?.connected ? 'success' : 'error'">
                {{ repoStatus?.connected ? t('dashboard.repoConnected') : t('dashboard.repoDisconnected') }}
              </n-tag>
              <n-tag size="small" round :type="repoStatus?.locked ? 'warning' : 'default'">
                {{ repoStatus?.locked ? t('dashboard.repoLocked') : t('dashboard.repoUnlocked') }}
              </n-tag>
            </n-space>

            <n-space :size="24">
              <n-statistic :label="t('dashboard.repoCapacity')" :value="capacityText" />
              <n-statistic :label="t('dashboard.repoSnapshots')" :value="snapsText" />
            </n-space>

            <n-alert v-if="repoStatus?.last_error" type="error" :show-icon="false">
              {{ t('dashboard.repoLastError') }}：{{ repoStatus.last_error }}
            </n-alert>

            <n-alert v-if="repoError" type="error" :show-icon="false">
              {{ t('dashboard.loadFailed', { message: repoError }) }}
            </n-alert>

            <n-text depth="3" class="dashboard__note">{{ t('dashboard.repoCacheNote') }}</n-text>
          </n-space>
        </n-card>
      </n-gi>

      <!-- 进行中任务卡 -->
      <n-gi>
        <n-card :title="t('dashboard.jobsCard')" size="small">
          <template #header-extra>
            <n-button size="tiny" secondary @click="jobsQuery.refetch()">
              {{ t('common.refresh') }}
            </n-button>
          </template>

          <n-alert v-if="jobsError" type="error" :show-icon="false">
            {{ t('dashboard.loadFailed', { message: jobsError }) }}
          </n-alert>
          <template v-else-if="jobs.length">
            <div v-for="job in jobs" :key="job.id" class="dashboard__job">
              <JobProgress :job-id="job.id" :title="`${job.type} · ${job.status}`" />
            </div>
          </template>
          <n-empty v-else :description="t('dashboard.jobsEmpty')" />

          <n-text depth="3" class="dashboard__note">{{ t('dashboard.jobsAutoNote') }}</n-text>
        </n-card>
      </n-gi>

      <!-- 运行模式卡：运行用户 × /host 挂载模式，只展示当前模式 -->
      <n-gi>
        <TierCard :user="ui.me?.user ?? ''" :host-mount="ui.me?.host_mount ?? 'ro'" />
      </n-gi>
    </n-grid>
  </n-space>
</template>

<style scoped>
.dashboard__note {
  display: block;
  margin-top: 10px;
  font-size: 12px;
}

.dashboard__job + .dashboard__job {
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px solid rgba(128, 128, 128, 0.18);
}
</style>
