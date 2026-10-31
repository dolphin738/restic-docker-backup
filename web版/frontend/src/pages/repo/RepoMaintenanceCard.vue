
<script setup lang="ts">
/**
 * 仓库页 · 维护卡（候选 4 批 2）。
 *
 * 七个危险/维护动作的列表：解锁 / 修复 / 找回 / 密钥轮换 / 手动 copy / 清空缓存，
 * 外加三个作业进度卡。
 *
 * **只管展示，不管确认门**：确认门仍在父页（useRepoGates 持有开关与动作），
 * 本组件通过 emit 请求父页开门——这样门的渲染位置与生命周期不变，零行为变化。
 */

import { computed } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { NButton, NCard, NSpace, NText } from 'naive-ui'
import { useI18n } from 'vue-i18n'

import { getCacheUsage } from '@/api/settings'
import JobProgress from '@/components/JobProgress.vue'
import { formatDateTime } from '@/utils/datetime'

const { t } = useI18n()

const props = defineProps<{
  /** 当前仓库状态（null = 尚未取到）。 */
  repoStatus: { locked?: boolean; connected?: boolean } | null
  /** 密码文件是否缺失（缺失时禁用密钥轮换）。 */
  passfileMissing: boolean
  /** 三个作业 id（空串 = 无进行中作业）。 */
  unlockJobId: string
  repairJobId: string
  recoverJobId: string
  /** 各动作 loading。 */
  unlocking: boolean
  repairing: boolean
  recovering: boolean
  cacheClearing: boolean
}>()

/** 动作请求：父页据此打开对应确认门（门不在本组件内）。 */
const emit = defineEmits<{
  (e: 'open', action: 'unlock' | 'repair' | 'recover' | 'rotate' | 'copy' | 'cache'): void
  (e: 'job-done', job: 'unlock' | 'repair' | 'recover'): void
}>()

/** 缓存占用（服务端 30s 缓存，前端 5 分钟不重取）。 */
const cacheQuery = useQuery({
  queryKey: ['ops', 'cache'],
  queryFn: getCacheUsage,
  staleTime: 300_000,
  refetchOnWindowFocus: false,
})

/** 缓存占用一行文案（truncated 时值仍可用，标注为「部分统计」即可，后端不 5xx）。 */
const cacheUsageText = computed(() => {
  const usage = cacheQuery.data.value
  if (!usage) {
    return t('repo.cacheUnknown')
  }
  return t('repo.cacheUsage', { size: formatBytes(usage.size_bytes), n: usage.items_count })
})

/** 缓存目录一行文案（原设置页专区字段并入，沿用纯文本注释口径）；无数据时不渲染。 */
const cachePathLine = computed(() => {
  const usage = cacheQuery.data.value
  return usage ? t('repo.cachePath', { path: usage.path }) : ''
})

/** 测量时间一行文案；无数据时不渲染。 */
const cacheMeasuredLine = computed(() => {
  const usage = cacheQuery.data.value
  return usage ? t('repo.cacheMeasured', { time: formatDateTime(usage.measured_at) }) : ''
})

/** du 超时截断标注（后端回部分值，仍可用，仅提示不精确）。 */
const cacheTruncated = computed(() => cacheQuery.data.value?.truncated ?? false)

/** 字节数人读化（缓存占用展示用）。 */
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
    <n-card :title="t('repo.maintenanceTitle')" size="small">
      <n-space vertical :size="12">
        <n-text depth="3" class="repo__note">{{ t('repo.maintenanceIntro') }}</n-text>

        <!-- 动作行 1：解锁（仅锁残留时有意义） -->
        <div class="repo__maint-row">
          <div class="repo__maint-text">
            <n-text strong>{{ t('repo.unlockBtn') }}</n-text>
            <n-text depth="3" class="repo__note">{{ t('repo.unlockHint') }}</n-text>
          </div>
          <n-button
            size="small"
            type="warning"
            :disabled="!props.repoStatus?.locked"
            :loading="props.unlocking"
            @click="emit('open', 'unlock')"
          >
            {{ t('repo.unlockBtn') }}
          </n-button>
        </div>

        <!-- 动作行 2：修复索引/卷（B1，仅已连接可操作） -->
        <div class="repo__maint-row">
          <div class="repo__maint-text">
            <n-text strong>{{ t('repo.repairBtn') }}</n-text>
            <n-text depth="3" class="repo__note">{{ t('repo.repairHint') }}</n-text>
          </div>
          <n-button
            size="small"
            type="error"
            :disabled="!props.repoStatus?.connected"
            :loading="props.repairing"
            @click="emit('open', 'repair')"
          >
            {{ t('repo.repairBtn') }}
          </n-button>
        </div>

        <!-- 动作行 3：找回丢失快照（B3，G1 新增入口） -->
        <div class="repo__maint-row">
          <div class="repo__maint-text">
            <n-text strong>{{ t('repo.recoverBtn') }}</n-text>
            <n-text depth="3" class="repo__note">{{ t('repo.recoverHint') }}</n-text>
          </div>
          <n-button
            size="small"
            type="error"
            :disabled="!props.repoStatus?.connected"
            :loading="props.recovering"
            @click="emit('open', 'recover')"
          >
            {{ t('repo.recoverBtn') }}
          </n-button>
        </div>

        <!-- 初始化不在此处：已按用户要求移到每个仓库行内（未初始化才显示），见仓库列表行卡。 -->

        <!-- 动作行 5/6：C4 密钥轮换 + 手动 copy（搬迁自状态卡，逻辑不变） -->
        <div class="repo__maint-row">
          <div class="repo__maint-text">
            <n-text strong>{{ t('repo.rotateBtn') }}</n-text>
            <n-text depth="3" class="repo__note">{{ t('repo.rotateHint') }}</n-text>
          </div>
          <n-button
            size="small"
            secondary
            :disabled="props.passfileMissing"
            @click="emit('open', 'rotate')"
          >
            {{ t('repo.rotateBtn') }}
          </n-button>
        </div>

        <div class="repo__maint-row">
          <div class="repo__maint-text">
            <n-text strong>{{ t('repo.copyBtn') }}</n-text>
            <n-text depth="3" class="repo__note">{{ t('repo.copyHint') }}</n-text>
          </div>
          <n-button size="small" secondary @click="emit('open', 'copy')">
            {{ t('repo.copyBtn') }}
          </n-button>
        </div>

        <!-- 动作行 7：E25 清空本地缓存（后端 S6 危险操作，有活跃任务时 409）。
             原设置页「缓存维护」专区已并入本行：路径/大小/条目数/测量时间/截断
             全部沿用本行纯文本注释口径展示（不再用设置页的 descriptions 表格）。 -->
        <div class="repo__maint-row">
          <div class="repo__maint-text">
            <n-text strong>{{ t('repo.cacheBtn') }}</n-text>
            <n-text depth="3" class="repo__note">{{ t('repo.cacheHint') }}</n-text>
            <n-text depth="3" class="repo__note">{{ cacheUsageText }}</n-text>
            <n-text v-if="cachePathLine" depth="3" class="repo__note">{{ cachePathLine }}</n-text>
            <n-text v-if="cacheMeasuredLine" depth="3" class="repo__note">{{ cacheMeasuredLine }}</n-text>
            <n-text v-if="cacheTruncated" depth="3" class="repo__note">{{ t('settings.cache.truncated') }}</n-text>
          </div>
          <n-button size="small" secondary :loading="props.cacheClearing" @click="emit('open', 'cache')">
            {{ t('repo.cacheBtn') }}
          </n-button>
        </div>

        <template v-if="props.unlockJobId">
          <JobProgress :job-id="props.unlockJobId" :title="t('repo.unlockJobTitle')" @done="emit('job-done', 'unlock')" @failed="emit('job-done', 'unlock')" />
        </template>
        <template v-if="props.repairJobId">
          <JobProgress :job-id="props.repairJobId" :title="t('repo.repairJobTitle')" @done="emit('job-done', 'repair')" @failed="emit('job-done', 'repair')" />
        </template>
        <template v-if="props.recoverJobId">
          <JobProgress :job-id="props.recoverJobId" :title="t('repo.recoverJobTitle')" @done="emit('job-done', 'recover')" @failed="emit('job-done', 'recover')" />
        </template>
      </n-space>
    </n-card>
</template>
