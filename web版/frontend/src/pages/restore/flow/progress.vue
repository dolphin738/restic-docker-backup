<script setup lang="ts">
/** 恢复向导步骤 3：progress（执行与结果，P11 三卡 + 完成态圆环）。
 *
 * 从 `flow/index.vue` 拆出（P10/P11 遗留项，2026-10-23 补做）。拆分口径：
 *  - result / jobFinished / jobFailed 由父级持有（跳步校验、离开确认都要读），
 *    本组件经 props 读；SSE 终态事件（done/failed）上抛父级落库；
 *  - 日志拉取（getJobLog）与 jobs.summary 计数解析**只服务本步骤**，随之内聚——
 *    进入步骤 3 拉一次起始输出，到达终态再拉一次最终日志。
 *
 * 三卡口径（对标 Duplicati restore-progress）：进度 / 通知 / 日志。
 * 本项目**无通知记录数据源**（`api/notify.ts` 只有配置读写，通知页亦只消费配置），
 * 故第二卡以**结果摘要**落地——复用既有 result / jobs.summary，
 * V-4「不新造数据源」的实质仍被遵守（不为此新增端点）。
 */

import { NAlert, NButton, NCard, NGrid, NGi, NLog, NSpace, NTag, NText } from 'naive-ui'
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getJobLog } from '@/api/jobs'
import type { JobDonePayload } from '@/api/types'
import JobProgress from '@/components/JobProgress.vue'
import RestoreProgressRing from '@/components/restore/RestoreProgressRing.vue'

const props = defineProps<{
  /** 提交后的作业信息；null = 尚未提交（本步骤不该出现，防呆）。 */
  result: {
    jobId: string
    restoreTarget: string
    filesDone: number | null
    summary: string | null
  } | null
  /** 任务是否已到达终态（用于区分「还没回报」与「回报了但没有计数」）。 */
  jobFinished: boolean
  /** 终态是否为失败（完成态圆环按此取语义色）。 */
  jobFailed: boolean
  /** 原位流才展示覆盖/失败计数回报（后端未回填则不显示，不编造数字）。 */
  isInPlace: boolean
}>()

const emit = defineEmits<{
  (e: 'done', payload: JobDonePayload): void
  (e: 'failed', payload: JobDonePayload): void
  (e: 'go-jobs'): void
}>()

const { t } = useI18n()

/** 完成态圆环中心文案（调用方注入 i18n，圆环组件本身不依赖 i18n）。 */
const ringLabel = computed(() =>
  props.jobFailed ? t('restore.progress.failed') : t('restore.progress.done'),
)

// ---------------------------------------------------------------- 日志卡（复用既有 getJobLog）

/** 日志卡内容：复用既有 `getJobLog`（V-4：不新造数据源），与 `JobLogDrawer` 同一口径。 */
const logContent = ref('')
const logTruncated = ref(false)

async function loadLog(jobId: string): Promise<void> {
  try {
    const data = await getJobLog(jobId)
    logContent.value = data.content
    logTruncated.value = data.truncated
  } catch {
    // 日志拉不到不阻断主流程，卡片回落空态文案
    logContent.value = ''
    logTruncated.value = false
  }
}

/** 进入步骤 3 拉一次（看起始输出），到达终态再拉一次（拿到最终日志）。 */
watch(
  () => props.result?.jobId,
  (jobId) => {
    logContent.value = ''
    logTruncated.value = false
    if (jobId) {
      void loadLog(jobId)
    }
  },
  { immediate: true },
)

watch(
  () => props.jobFinished,
  (finished) => {
    if (finished && props.result) {
      void loadLog(props.result.jobId)
    }
  },
)

// ---------------------------------------------------------------- 结果摘要卡（jobs.summary 计数回报）

/** jobs.summary 的实际覆盖/失败计数（Q4 口径：TOCTOU 窗口内以执行后实际值为准）。
 *
 * 后端 T08 尚未冻结 summary 的落盘形态，这里做「有则显示、无则不显示」的容错：
 * 只有 summary 是可解析的 JSON 对象且含数值型 overwritten/failed 时才取用，
 * 其余形态（纯文本、缺字段）一律返回 null —— 前端不展示任何数字，绝不编造。 */
const summaryCounts = computed<{ overwritten: number | null; failed: number | null } | null>(() => {
  const raw = props.result?.summary
  if (!raw) {
    return null
  }
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    return null
  }
  if (typeof parsed !== 'object' || parsed === null) {
    return null
  }
  const record = parsed as Record<string, unknown>
  const overwritten = readCount(record['overwritten'])
  const failed = readCount(record['failed'])
  return overwritten === null && failed === null ? null : { overwritten, failed }
})

function readCount(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

const overwrittenCount = computed(() => summaryCounts.value?.overwritten ?? null)
const failedCount = computed(() => summaryCounts.value?.failed ?? null)
/** 终态已到但后端没回计数：明确说没回报，不猜数字。 */
const summaryPending = computed(
  () => props.isInPlace && props.jobFinished && summaryCounts.value === null,
)

// 「查看作业」按钮的跳转由父级处理（@go-jobs）——导航意图与外壳的路由职责在一起。
</script>

<template>
  <n-alert type="success" :show-icon="false">{{ t('restore.submitted') }}</n-alert>
  <n-grid :cols="'1 m:2'" responsive="screen" :x-gap="12" :y-gap="12">
    <!-- 卡一：进度 + 完成态圆环 -->
    <n-gi>
      <n-card size="small" :title="t('restore.progress.title')">
        <n-space align="center" :size="12">
          <RestoreProgressRing
            v-if="jobFinished"
            :percent="100"
            :status="jobFailed ? 'error' : 'success'"
            :label="ringLabel"
          />
          <n-space vertical :size="4">
            <n-text>
              {{ t('restore.restoreTarget') }}：<n-tag size="small">{{ result?.restoreTarget }}</n-tag>
            </n-text>
            <n-text v-if="result?.filesDone !== null && result?.filesDone !== undefined" depth="3">
              {{ t('job.filesOnly', { done: result.filesDone }) }}
            </n-text>
            <n-text depth="3">
              {{ isInPlace ? t('restore.inplace.runNote') : t('restore.targetNote') }}
            </n-text>
          </n-space>
        </n-space>
        <JobProgress
          v-if="result"
          :job-id="result.jobId"
          :title="result.jobId"
          @done="emit('done', $event)"
          @failed="emit('failed', $event)"
        />
      </n-card>
    </n-gi>

    <!-- 卡二：结果摘要（替代无数据源的通知卡） -->
    <n-gi>
      <n-card size="small" :title="t('restore.progress.summaryTitle')">
        <n-space vertical :size="4">
          <n-text v-if="jobFailed" type="error">{{ t('restore.progress.failedHint') }}</n-text>
          <n-text v-else-if="jobFinished">{{ t('restore.progress.finished') }}</n-text>
          <n-text v-else depth="3">{{ t('restore.progress.running') }}</n-text>
          <!-- 原位流：任务回报的实际覆盖/失败计数（后端未回填则不显示，不编造数字） -->
          <template v-if="isInPlace">
            <n-text v-if="overwrittenCount !== null" depth="2">
              {{ t('restore.summary.overwritten', { n: overwrittenCount }) }}
            </n-text>
            <n-text v-if="failedCount !== null" depth="2">
              {{ t('restore.summary.failed', { n: failedCount }) }}
            </n-text>
            <n-text v-if="summaryPending" depth="3">{{ t('restore.summary.pending') }}</n-text>
          </template>
        </n-space>
      </n-card>
    </n-gi>

    <!-- 卡三：任务日志（复用既有 getJobLog，与 JobLogDrawer 同一口径） -->
    <n-gi span="1 m:2">
      <n-card size="small" :title="t('restore.progress.logTitle')">
        <n-space vertical :size="6">
          <n-log v-if="logContent" :log="logContent" :rows="12" trim />
          <n-text v-else depth="3">{{ t('restore.progress.logEmpty') }}</n-text>
          <n-text v-if="logTruncated" depth="3">{{ t('restore.progress.logTruncated') }}</n-text>
        </n-space>
      </n-card>
    </n-gi>
  </n-grid>
  <n-space justify="end">
    <n-button secondary @click="emit('go-jobs')">{{ t('restore.goJobs') }}</n-button>
  </n-space>
</template>
