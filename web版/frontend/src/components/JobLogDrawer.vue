<script setup lang="ts">
/** 作业日志抽屉（E27）：jobs 页与主页任务行**共用**同一份实现（owner 2026-10-23 裁决 A4 / Q2(a)）。
 *
 * 存在理由：改造前该抽屉是 `pages/jobs/index.vue` 的私有实现（`logShow` / `loadMoreLog` /
 * `skippedEntries` 全套），主页任务行的「日志」入口只能借用 `JobProgress` 内部那个**跟随 SSE**
 * 的抽屉——只看得到运行中的实时流，看不到已结束作业的历史日志。抽成共用组件后：
 * - 两处消费方零重复（S-4：禁止逐页各写一份）；
 * - 数据源统一走 E27 `getJobLog`（尾部 64 KiB 起步，按需逐级放大到 1 MiB）；
 * - 打开即按 jobId 拉一次，不依赖是否有活跃 SSE 流 → 已结束作业同样可看。
 *
 * 用法：`<JobLogDrawer v-model:show="show" :job-id="id" :skipped-count="n" />`
 */

import { NButton, NCollapse, NCollapseItem, NDrawer, NDrawerContent, NLog, NSpace, NText } from 'naive-ui'
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getJobLog } from '@/api/jobs'

const props = withDefaults(
  defineProps<{
    /** 抽屉可见性（v-model:show）。 */
    show: boolean
    /** 目标作业 id；空串表示未指定（抽屉仍可显示「无日志」）。 */
    jobId: string
    /** 该作业的 skipped 计数（E14 口径，用于折叠区标题与是否渲染）。 */
    skippedCount?: number
  }>(),
  { skippedCount: 0 },
)

const emit = defineEmits<{
  (e: 'update:show', value: boolean): void
}>()

const { t } = useI18n()

/** 服务端默认尾部窗口（未指定 tail_bytes 时 E27 的默认值）。 */
const DEFAULT_TAIL = 65_536
/** 逐级放大上限（与后端 `LOG_TAIL_MAX_BYTES` 同量级，超出无意义）。 */
const MAX_TAIL = 1_048_576

const content = ref('')
const tailBytes = ref<number | null>(null)
const truncated = ref(false)
const loadingMore = ref(false)

const canLoadMore = computed(
  () => truncated.value && (tailBytes.value === null || tailBytes.value < MAX_TAIL),
)

function nextTailSize(current: number | null): number {
  if (current === null) {
    return DEFAULT_TAIL * 4 // 64 KiB → 256 KiB
  }
  return Math.min(current * 2, MAX_TAIL)
}

/** 拉一次日志；`target` 为空表示用服务端默认窗口。 */
async function load(target?: number): Promise<void> {
  if (!props.jobId) {
    content.value = ''
    tailBytes.value = null
    truncated.value = false
    return
  }
  try {
    const data = await getJobLog(props.jobId, target)
    content.value = data.content
    tailBytes.value = data.tail_bytes
    truncated.value = data.truncated
  } catch {
    // 拉取失败保持上一次内容（不冒泡：抽屉是辅助视图，不该打断主流程）
  }
}

/** 打开抽屉即加载（jobId 变化同样重载，避免串台显示上一个作业的日志）。 */
watch(
  () => [props.show, props.jobId] as const,
  ([show]) => {
    if (show) {
      void load()
    }
  },
  { immediate: true },
)

/** 逐级放大尾部窗口（64 KiB → 256 KiB → … → 1 MiB 上限）。 */
async function loadMore(): Promise<void> {
  if (loadingMore.value || !canLoadMore.value) {
    return
  }
  loadingMore.value = true
  try {
    await load(nextTailSize(tailBytes.value))
  } finally {
    loadingMore.value = false
  }
}

/** skipped 明细（数量取 E14，明细从 E27 文本挑）：archival 阶段的 error 行。 */
const skippedEntries = computed<{ item: string; error: string }[]>(() => {
  const entries: { item: string; error: string }[] = []
  for (const line of content.value.split('\n')) {
    const trimmed = line.trim()
    if (!trimmed.startsWith('{')) {
      continue
    }
    try {
      const payload = JSON.parse(trimmed) as Record<string, unknown>
      if (payload['message_type'] !== 'error' || payload['during'] !== 'archival') {
        continue
      }
      entries.push({
        item: typeof payload['item'] === 'string' ? payload['item'] : trimmed,
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
  <n-drawer :show="show" :width="760" placement="right" @update:show="emit('update:show', $event)">
    <n-drawer-content :title="t('jobs.logTitle')" closable>
      <n-space vertical :size="10">
        <template v-if="skippedCount > 0">
          <n-collapse size="small">
            <n-collapse-item :title="t('job.skippedTitle', { n: skippedCount })" name="skipped">
              <n-space vertical :size="6">
                <n-text depth="3">{{ t('jobs.skippedTailNote') }}</n-text>
                <template v-if="skippedEntries.length">
                  <div v-for="(entry, index) in skippedEntries" :key="`${entry.item}-${index}`" class="job-log__skipped">
                    <n-text depth="2">{{ entry.item }}</n-text>
                    <n-text v-if="entry.error" depth="3">{{ entry.error }}</n-text>
                  </div>
                </template>
                <n-text v-else depth="3">{{ t('jobs.skippedNotInTail') }}</n-text>
              </n-space>
            </n-collapse-item>
          </n-collapse>
        </template>

        <n-log :log="content || t('job.logEmpty')" :rows="26" trim />
        <n-text depth="3">{{ t('jobs.logTailNote', { n: tailBytes ?? 0 }) }}</n-text>
        <n-space>
          <n-button v-if="canLoadMore" size="small" secondary :loading="loadingMore" @click="loadMore">
            {{ t('jobs.logLoadMore') }}
          </n-button>
          <n-text v-else-if="truncated" depth="3">{{ t('jobs.logMaxTail') }}</n-text>
        </n-space>
      </n-space>
    </n-drawer-content>
  </n-drawer>
</template>

<style scoped>
.job-log__skipped {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 4px 8px;
  border-left: 2px solid rgba(250, 208, 44, 0.6);
  font-size: 12px;
}
</style>
