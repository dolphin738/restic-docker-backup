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
import { computed, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { listSnapshots } from '@/api/snapshots'
import { RESTORE_TARGET_ROOT, restoreSnapshot } from '@/api/restore'
import ConfirmGate from '@/components/ConfirmGate.vue'
import JobProgress from '@/components/JobProgress.vue'
import { useUiStore } from '@/stores/ui'

const { t } = useI18n()
const router = useRouter()
const ui = useUiStore()

const message = useMessage()
ui.setMessageApi(message)
onUnmounted(() => ui.setMessageApi(null))

/** 1=选快照与目标 2=三步确认 3=执行与结果 */
const step = ref(1)
const snapshotId = ref<string | null>(null)
const gateShow = ref(false)
const restoring = ref(false)
const result = ref<{ jobId: string; restoreTarget: string } | null>(null)

// 与 QuadrantCard 同源：/api/auth/me 的 host_mount / user
const isReadOnlyMount = computed(() => ui.me?.host_mount === 'ro')
const inPlaceReason = computed(() =>
  isReadOnlyMount.value ? t('restore.inPlaceRoReason') : t('restore.inPlaceP2Reason'),
)

const snapshotsQuery = useQuery({ queryKey: ['snapshots', 'restore-picker'], queryFn: () => listSnapshots(50) })

const snapshotOptions = computed(() =>
  (snapshotsQuery.data.value?.items ?? []).map((snapshot) => ({
    label: `${snapshot.time ?? t('common.none')} · ${snapshot.paths.join(', ') || snapshot.id.slice(0, 8)}`,
    value: snapshot.id,
  })),
)

const selectedSnapshot = computed(() =>
  (snapshotsQuery.data.value?.items ?? []).find((snapshot) => snapshot.id === snapshotId.value) ?? null,
)

function goConfirm(): void {
  if (!snapshotId.value) {
    message.warning(t('restore.needSnapshot'))
    return
  }
  step.value = 2
  gateShow.value = true
}

/** 确认门通过后提交：请求体只有 snapshot_id 与 target；restore_target 由服务端返回。 */
async function onConfirmed(): Promise<void> {
  if (!snapshotId.value) {
    return
  }
  restoring.value = true
  try {
    const data = await restoreSnapshot(snapshotId.value, RESTORE_TARGET_ROOT)
    result.value = { jobId: data.job_id, restoreTarget: data.restore_target }
    step.value = 3
  } finally {
    restoring.value = false
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
        <!-- 步骤 1：选快照 + 目标模式 -->
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
            <n-text depth="3">{{ t('restore.targetNote') }}</n-text>
          </n-space>
          <JobProgress v-if="result" :job-id="result.jobId" :title="result.jobId" />
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
          <n-text>{{ t('restore.gateSnapshot') }}：{{ selectedSnapshot?.time ?? snapshotId }}</n-text>
          <n-text>{{ t('restore.gatePaths') }}：{{ selectedSnapshot?.paths.join(', ') }}</n-text>
          <n-text>{{ t('restore.gateTarget') }}：{{ RESTORE_TARGET_ROOT }}</n-text>
        </n-space>
      </template>
    </ConfirmGate>
  </n-space>
</template>

<style scoped>
.restore__body {
  margin-top: 16px;
}
</style>
