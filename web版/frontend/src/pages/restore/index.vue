<script setup lang="ts">
import { useQuery } from '@tanstack/vue-query'
import {
  NAlert,
  NButton,
  NCard,
  NRadioButton,
  NRadioGroup,
  NSelect,
  NSpace,
  NStep,
  NSteps,
  NTag,
  NText,
  useMessage,
} from 'naive-ui'
import { computed, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { listSnapshotFiles, listSnapshots } from '@/api/snapshots'
import { RESTORE_TARGET_ROOT, restoreSnapshot } from '@/api/restore'
import type { JobDonePayload } from '@/api/types'
import ConfirmGate from '@/components/ConfirmGate.vue'
import JobProgress from '@/components/JobProgress.vue'
import VirtualTree, { type TreeItem, type TreePage } from '@/components/VirtualTree.vue'
import { DATETIME_FALLBACK, formatDateTimeOr } from '@/utils/datetime'
import { useUiStore } from '@/stores/ui'

const { t } = useI18n()
const router = useRouter()
const route = useRoute()
const ui = useUiStore()

const message = useMessage()
ui.setMessageApi(message)
onUnmounted(() => ui.setMessageApi(null))

/** 1=选快照与目标 2=三步确认 3=执行与结果 */
const step = ref(1)
/** 当前选中快照；列表加载后默认取最新版本，之后用户可自由改选。 */
const snapshotId = ref<string | null>(null)
/** URL ?snapshot= 预选（快照页「恢复此快照」直达入口，PRD §5 页面 8）；缺省 null。 */
const requestedSnapshotId = ref<string | null>(
  typeof route.query.snapshot === 'string' && route.query.snapshot ? route.query.snapshot : null,
)
const gateShow = ref(false)
const restoring = ref(false)
const result = ref<{ jobId: string; restoreTarget: string; filesDone: number | null } | null>(null)

/** B1 文件级恢复：已选文件/目录（展示形态，如 /etc、/home/u/x）；
 *  空集合 = 整快照恢复（E19 缺省行为，回归零影响）。 */
const selectedFiles = ref<Set<string>>(new Set())

// 与 TierCard（运行模式卡）同源：/api/auth/me 的 host_mount / user
const isReadOnlyMount = computed(() => ui.me?.host_mount === 'ro')
const inPlaceReason = computed(() =>
  isReadOnlyMount.value ? t('restore.inPlaceRoReason') : t('restore.inPlaceP2Reason'),
)

const snapshotsQuery = useQuery({ queryKey: ['snapshots', 'restore-picker'], queryFn: () => listSnapshots(50) })

/** 列表按 time 降序：后端 E17 已保证最新在前，前端再兜底排序一次，首项即最新版本。 */
const snapshotItems = computed(() =>
  [...(snapshotsQuery.data.value?.items ?? [])].sort((a, b) => (b.time ?? '').localeCompare(a.time ?? '')),
)

const snapshotOptions = computed(() =>
  snapshotItems.value.map((snapshot) => ({
    // 缺陷 C：选择项时间收口为本地可读形式；无 time 时回落「无」
    label: `${formatDateTimeOr(snapshot.time, t('common.none'))} · ${snapshot.paths.join(', ') || snapshot.id.slice(0, 8)}`,
    value: snapshot.id,
  })),
)

const selectedSnapshot = computed(() => snapshotItems.value.find((snapshot) => snapshot.id === snapshotId.value) ?? null)

/** 默认选中：列表就绪后，?snapshot= 预选命中则用之，否则取最新版本；
 *  当前选择仍有效（含用户改选）时不打扰；预选 id 不存在时忽略并回落默认最新。 */
watch(
  snapshotItems,
  (items) => {
    if (snapshotId.value && items.some((item) => item.id === snapshotId.value)) {
      return
    }
    const requested = requestedSnapshotId.value
    snapshotId.value = (requested ? items.find((item) => item.id === requested)?.id : undefined) ?? items[0]?.id ?? null
  },
  { immediate: true },
)

/** 换快照即清空已选文件：文件路径是快照内相对语义，跨快照不通用。 */
watch(snapshotId, () => {
  selectedFiles.value = new Set()
})

// ---------------------------------------------------------------- 文件树多选（B1，E18）
function treeLoader(path: string, cursor?: string): Promise<TreePage> {
  const id = snapshotId.value ?? ''
  return listSnapshotFiles(id, path, cursor).then((data) => ({
    items: data.items.map((item) => ({
      key: item.path,
      name: item.name,
      type: item.type,
      path: item.display_path,
    })),
    nextCursor: data.next_cursor,
  }))
}

/** 点目录名（selectDir）/文件名 = 勾选/取消勾选；展开由行首三角形负责。 */
function onFileSelect(item: TreeItem): void {
  const next = new Set(selectedFiles.value)
  if (next.has(item.path)) {
    next.delete(item.path)
  } else {
    next.add(item.path)
  }
  selectedFiles.value = next
}

const selectedFileList = computed(() => [...selectedFiles.value])
const selectedFileCount = computed(() => selectedFiles.value.size)

function goConfirm(): void {
  if (!snapshotId.value) {
    message.warning(t('restore.needSnapshot'))
    return
  }
  step.value = 2
  gateShow.value = true
}

/** 确认门通过后提交：paths 为展示形态；空集合 = 整快照；restore_target 由服务端返回。 */
async function onConfirmed(): Promise<void> {
  if (!snapshotId.value) {
    return
  }
  restoring.value = true
  try {
    const data = await restoreSnapshot(snapshotId.value, RESTORE_TARGET_ROOT, selectedFileList.value)
    result.value = { jobId: data.job_id, restoreTarget: data.restore_target, filesDone: null }
    step.value = 3
  } finally {
    restoring.value = false
  }
}

/** SSE 终态帧带回 files_done：结果区展示本次恢复的文件数。 */
function onJobDone(payload: JobDonePayload): void {
  if (result.value) {
    result.value.filesDone = payload.files_done
  }
}

function goJobs(): void {
  void router.push({ name: 'jobs' })
}

const stepStatus = computed(() => (step.value >= 3 ? 'finish' : 'process'))
</script>

<template>
  <n-space vertical :size="16">
    <n-card :title="t('nav.restore')" size="small">
      <n-steps :current="step" :status="stepStatus" size="small">
        <n-step :title="t('restore.stepPick')" />
        <n-step :title="t('restore.stepConfirm')" />
        <n-step :title="t('restore.stepRun')" />
      </n-steps>

      <n-space vertical :size="14" class="restore__body">
        <!-- 步骤 1：选快照 + 目标模式 + 文件多选 -->
        <template v-if="step === 1">
          <n-select
            v-model:value="snapshotId"
            :options="snapshotOptions"
            :loading="snapshotsQuery.isFetching.value"
            :placeholder="t('restore.pickPlaceholder')"
            filterable
          />
          <n-alert v-if="snapshotsQuery.error.value" type="error" :show-icon="false">
            {{ String(snapshotsQuery.error.value) }}
          </n-alert>

          <n-space vertical :size="6">
            <n-text strong>{{ t('restore.targetTitle') }}</n-text>
            <n-radio-group :value="'timestamp'" size="small">
              <n-radio-button value="timestamp">{{ t('restore.targetTimestamp') }}</n-radio-button>
              <n-radio-button value="inplace" disabled>{{ t('restore.targetInPlace') }}</n-radio-button>
            </n-radio-group>
            <n-text depth="3">{{ t('restore.targetTimestampNote') }}</n-text>
            <!-- 原位恢复：P1 不开放。ro 档给挂载原因，rw 档给 P2 原因；来源与四象限卡同源 -->
            <n-alert type="warning" :show-icon="false">
              <n-space :size="6" align="center">
                <n-tag size="tiny" round :type="isReadOnlyMount ? 'warning' : 'default'">
                  {{ ui.me?.host_mount ?? 'ro' }}
                </n-tag>
                <n-text depth="3">{{ inPlaceReason }}</n-text>
              </n-space>
            </n-alert>
          </n-space>

          <!-- B1：文件级多选（可选）；不选 = 整快照 -->
          <n-space vertical :size="6">
            <n-text strong>{{ t('restore.filesTitle') }}</n-text>
            <n-text depth="3">{{ t('restore.filesHint') }}</n-text>
            <VirtualTree
              v-if="snapshotId"
              :key="`files-${snapshotId}`"
              root-path="/"
              :load-children="treeLoader"
              :height="300"
              select-dir
              :selected-paths="selectedFiles"
              @select="onFileSelect"
            />
            <n-text v-if="selectedFileCount > 0" depth="2">
              {{ t('restore.filesSelected', { n: selectedFileCount }) }}
            </n-text>
            <n-text v-else depth="3">{{ t('restore.filesWhole') }}</n-text>
          </n-space>

          <n-space justify="end">
            <n-button type="primary" :disabled="!snapshotId" @click="goConfirm">
              {{ t('confirm.next') }}
            </n-button>
          </n-space>
        </template>

        <!-- 步骤 2 的入口是 ConfirmGate 弹窗；弹窗被关闭后可重新打开 -->
        <template v-else-if="step === 2">
          <n-text depth="3">{{ t('restore.stepConfirm') }}</n-text>
          <n-space justify="end">
            <n-button quaternary @click="step = 1">{{ t('confirm.back') }}</n-button>
            <n-button type="primary" @click="gateShow = true">{{ t('confirm.next') }}</n-button>
          </n-space>
        </template>

        <!-- 步骤 3：执行结果 -->
        <template v-else>
          <n-alert type="success" :show-icon="false">{{ t('restore.submitted') }}</n-alert>
          <n-space vertical :size="4">
            <n-text>
              {{ t('restore.restoreTarget') }}：<n-tag size="small">{{ result?.restoreTarget }}</n-tag>
            </n-text>
            <n-text v-if="result?.filesDone !== null && result?.filesDone !== undefined" depth="3">
              {{ t('job.filesOnly', { done: result.filesDone }) }}
            </n-text>
            <n-text depth="3">{{ t('restore.targetNote') }}</n-text>
          </n-space>
          <JobProgress v-if="result" :job-id="result.jobId" :title="result.jobId" @done="onJobDone" />
          <n-space justify="end">
            <n-button secondary @click="goJobs">{{ t('restore.goJobs') }}</n-button>
          </n-space>
        </template>
      </n-space>
    </n-card>

    <!-- 三步确认门：预览 → 输入 RESTORE → 二次确认 -->
    <ConfirmGate
      v-model:show="gateShow"
      :title="t('restore.gateTitle')"
      :keyword="'RESTORE'"
      :description="t('restore.gateDesc')"
      :loading="restoring"
      @confirm="onConfirmed"
    >
      <template #preview>
        <n-space vertical :size="4">
          <!-- 缺陷 C：确认门快照时间同样收口；无 time 时回落快照 id（保持原兜底语义） -->
          <n-text>{{ t('restore.gateSnapshot') }}：{{ formatDateTimeOr(selectedSnapshot?.time, selectedSnapshot ? DATETIME_FALLBACK : (snapshotId ?? DATETIME_FALLBACK)) }}</n-text>
          <n-text>{{ t('restore.gatePaths') }}：{{ selectedSnapshot?.paths.join(', ') }}</n-text>
          <n-text>{{ t('restore.gateTarget') }}：{{ RESTORE_TARGET_ROOT }}</n-text>
          <!-- B1：已选文件清单（前 N 条 + 共 M 条）；空 = 整快照 -->
          <template v-if="selectedFileCount > 0">
            <n-text strong>{{ t('restore.gateFiles', { n: 20, m: selectedFileCount }) }}</n-text>
            <n-text
              v-for="path in selectedFileList.slice(0, 20)"
              :key="path"
              depth="3"
              class="restore__gate-file"
            >
              {{ path }}
            </n-text>
          </template>
          <n-text v-else depth="3">{{ t('restore.filesWhole') }}</n-text>
        </n-space>
      </template>
    </ConfirmGate>
  </n-space>
</template>

<style scoped>
.restore__body {
  margin-top: 16px;
}

.restore__gate-file {
  display: block;
  font-size: 12px;
}
</style>
