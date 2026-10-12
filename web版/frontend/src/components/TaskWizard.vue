<script setup lang="ts">
/** 新建/编辑任务五步向导（增量设计 §5.3）。
 *
 * 步骤：①基础（名称/启停/运行模式/密码文件状态）→ ②备份源（SourcesPicker）
 * → ③存储目标（仓库只读 + 测试连接）→ ④调度（ScheduleRetentionForm）
 * → ⑤保留与首跑（dry-run 预览门 + 保存/首跑）。
 *
 * 编辑流：E34b 预填；tag 只读；sources/excludes 变更后服务端重置 dry_run_pending
 * （UI 拦截双保险，R-026-08）。
 */

import { useQuery, useQueryClient } from '@tanstack/vue-query'
import {
  NAlert,
  NButton,
  NCard,
  NInput,
  NSpace,
  NStep,
  NSteps,
  NSwitch,
  NTag,
  NText,
  useMessage,
} from 'naive-ui'
import { computed, onUnmounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import {
  REPO_ID,
  createBackupTask,
  dryRunBackupTask,
  dryRunSources,
  getBackupTask,
  updateBackupTask,
} from '@/api/backupTasks'
import { getConfig, testRepo } from '@/api/config'
import { hasCode, isAppError } from '@/api/http'
import { createJob } from '@/api/jobs'
import { getRepoStatus } from '@/api/repo'
import type { BackupTask, DryRunResponse, RepoTestResponse, TaskUpsertRequest } from '@/api/types'
import ScheduleRetentionForm, {
  type ScheduleRetentionValue,
} from '@/components/task/ScheduleRetentionForm.vue'
import SourcesPicker from '@/components/task/SourcesPicker.vue'
import TierCard from '@/components/TierCard.vue'
import { useUiStore } from '@/stores/ui'

const props = defineProps<{
  /** 编辑模式的任务 id；缺省 = 新建。 */
  taskId?: number | null
}>()

const { t } = useI18n()
const router = useRouter()
const ui = useUiStore()
const queryClient = useQueryClient()

const message = useMessage()
ui.setMessageApi(message)
onUnmounted(() => ui.setMessageApi(null))

const isEdit = computed(() => props.taskId != null)
const step = ref(1)

// ---------------------------------------------------------------- 表单状态
interface TaskFormState extends ScheduleRetentionValue {
  name: string
  enabled: boolean
  sources: string[]
  excludes: string[]
  /** 以下高级字段不在向导 UI 暴露：新建取缺省，编辑时原样往返（TaskUpsertRequest 全量语义）。 */
  group_by: string
  one_file_system: boolean
  compression: string
  check_sample_ratio: number
  monthly_full_day: number
  prune_interval_days: number
}

function defaultForm(): TaskFormState {
  return {
    name: '',
    enabled: true,
    sources: [],
    excludes: [],
    cron: '',
    keep_last: 0,
    keep_hourly: 0,
    keep_daily: 7,
    keep_weekly: 4,
    keep_monthly: 6,
    keep_yearly: 1,
    group_by: 'host,paths,tags',
    one_file_system: false,
    compression: 'auto',
    check_sample_ratio: 0.02,
    monthly_full_day: 1,
    prune_interval_days: 7,
  }
}

const form = reactive<TaskFormState>(defaultForm())

/** 编辑预填时的源集合快照：用于「变更后需重新预览」的本地提示。 */
const originalSources = ref<string[]>([])
const originalExcludes = ref<string[]>([])
const loadedTask = ref<BackupTask | null>(null)

// ---------------------------------------------------------------- 编辑预填（E34b）
const taskQuery = useQuery({
  queryKey: ['backup-task', props.taskId ?? 0],
  queryFn: () => (props.taskId == null ? Promise.resolve(null) : getBackupTask(props.taskId)),
})

watch(
  () => taskQuery.data.value,
  (task) => {
    if (!task) {
      return
    }
    loadedTask.value = task
    form.name = task.name
    form.enabled = task.enabled
    form.sources = [...task.display_sources]
    form.excludes = [...task.display_excludes]
    form.cron = task.cron ?? ''
    form.keep_last = task.keep_last
    form.keep_hourly = task.keep_hourly
    form.keep_daily = task.keep_daily
    form.keep_weekly = task.keep_weekly
    form.keep_monthly = task.keep_monthly
    form.keep_yearly = task.keep_yearly
    form.group_by = task.group_by
    form.one_file_system = task.one_file_system
    form.compression = task.compression
    form.check_sample_ratio = task.check_sample_ratio
    form.monthly_full_day = task.monthly_full_day
    form.prune_interval_days = task.prune_interval_days
    originalSources.value = [...task.display_sources]
    originalExcludes.value = [...task.display_excludes]
  },
  { immediate: true },
)

// ---------------------------------------------------------------- 仓库与配置（E30/E06）
const configQuery = useQuery({ queryKey: ['config'], queryFn: getConfig })
const repoStatusQuery = useQuery({
  queryKey: ['repo', 'status'],
  queryFn: getRepoStatus,
  refetchInterval: 60_000,
})

const repoUrl = computed(
  () => configQuery.data.value?.items.find((item) => item.key === 'repo.url')?.value ?? '',
)
const passfileConfigured = computed(() => configQuery.data.value?.passfile_status === 'configured')

/** 源集合是否较预填值发生变化（保存后服务端会重置 dry_run_pending）。 */
const sourcesChanged = computed(() => {
  if (!isEdit.value) {
    return false
  }
  return (
    form.sources.join('\n') !== originalSources.value.join('\n') ||
    form.excludes.join('\n') !== originalExcludes.value.join('\n')
  )
})

// ---------------------------------------------------------------- 步骤导航
function validateStep(current: number): boolean {
  if (current === 1 && form.name.trim() === '') {
    message.warning(t('wizard.needName'))
    return false
  }
  if (current === 2 && form.sources.length === 0) {
    message.warning(t('wizard.needSources'))
    return false
  }
  return true
}

function goNext(): void {
  if (!validateStep(step.value)) {
    return
  }
  step.value = Math.min(5, step.value + 1)
}

function goPrev(): void {
  step.value = Math.max(1, step.value - 1)
}

// ---------------------------------------------------------------- dry-run 预览门
const dryRunLoading = ref(false)
const dryRunResult = ref<DryRunResponse | null>(null)
const dryRunError = ref('')

/** 预览成功后才可「确认并立即首跑」；E36 成功即清服务端 dry_run_pending。 */
const dryRunOk = computed(() => dryRunResult.value !== null && dryRunResult.value.error === null)

async function onDryRun(): Promise<void> {
  if (form.sources.length === 0) {
    message.warning(t('wizard.needSources'))
    return
  }
  dryRunLoading.value = true
  dryRunError.value = ''
  dryRunResult.value = null
  try {
    dryRunResult.value = isEdit.value
      ? await dryRunBackupTask(props.taskId!, { sources: form.sources, excludes: form.excludes })
      : await dryRunSources(form.sources, form.excludes)
    if (isEdit.value && dryRunOk.value) {
      // E36 预览成功已清除 dry_run_pending：刷新本地任务态，让门禁提示即时消失
      await queryClient.invalidateQueries({ queryKey: ['backup-task', props.taskId ?? 0] })
    }
  } catch (error) {
    dryRunError.value = isAppError(error) ? error.message : t('errors.UNKNOWN')
  } finally {
    dryRunLoading.value = false
  }
}

// ---------------------------------------------------------------- 保存 / 首跑
const saving = ref(false)

function buildPayload(): TaskUpsertRequest {
  return {
    name: form.name.trim(),
    enabled: form.enabled,
    sources: form.sources,
    excludes: form.excludes,
    keep_last: form.keep_last,
    keep_hourly: form.keep_hourly,
    keep_daily: form.keep_daily,
    keep_weekly: form.keep_weekly,
    keep_monthly: form.keep_monthly,
    keep_yearly: form.keep_yearly,
    group_by: form.group_by,
    cron: form.cron.trim() || null,
    one_file_system: form.one_file_system,
    compression: form.compression,
    check_sample_ratio: form.check_sample_ratio,
    monthly_full_day: form.monthly_full_day,
    prune_interval_days: form.prune_interval_days,
    limit_up: 0,
    limit_down: 0,
  }
}

async function afterSaved(): Promise<void> {
  await queryClient.invalidateQueries({ queryKey: ['backup-tasks'] })
  void router.push({ name: 'dashboard' })
}

/** 仅保存：不触发 dry-run 门（首跑前的预览拦截由仪表盘 Start 按钮负责）。 */
async function onSaveOnly(): Promise<void> {
  if (!validateStep(1) || form.sources.length === 0) {
    message.warning(t('wizard.needSources'))
    return
  }
  saving.value = true
  try {
    if (isEdit.value) {
      await updateBackupTask(props.taskId!, buildPayload())
      message.success(t('wizard.updated'))
    } else {
      await createBackupTask(buildPayload())
      message.success(t('wizard.created'))
    }
    await afterSaved()
  } finally {
    saving.value = false
  }
}

/** 确认并立即首跑：先过 dry-run 预览门，再保存并提交 backup job（R-026-08）。 */
async function onSaveAndRun(): Promise<void> {
  if (!validateStep(1) || form.sources.length === 0) {
    message.warning(t('wizard.needSources'))
    return
  }
  if (!dryRunOk.value) {
    message.warning(t('wizard.dry_run_hint'))
    return
  }
  saving.value = true
  try {
    const taskId = isEdit.value ? props.taskId! : (await createBackupTask(buildPayload())).id
    if (isEdit.value) {
      await updateBackupTask(taskId, buildPayload())
    }
    await createJob('backup', { taskId })
    message.success(t('wizard.firstRunSubmitted'))
    await afterSaved()
  } catch (error) {
    if (hasCode(error, 'JOB_CONFLICT')) {
      const raw = isAppError(error) ? error.data?.['running_task_name'] : null
      const name = typeof raw === 'string' && raw ? raw : t('common.none')
      message.error(t('task.conflict', { name }))
      return
    }
    if (!isAppError(error)) {
      throw error
    }
  } finally {
    saving.value = false
  }
}

// ---------------------------------------------------------------- 测试连接（E29）
const testing = ref(false)
const testResult = ref<RepoTestResponse | null>(null)

async function onTestConnection(): Promise<void> {
  testing.value = true
  testResult.value = null
  try {
    testResult.value = await testRepo()
  } finally {
    testing.value = false
  }
}

const taskLoadFailed = computed(() => {
  const error = taskQuery.error.value
  return error ? (isAppError(error) ? error.message : t('errors.UNKNOWN')) : ''
})

// ---------------------------------------------------------------- 调度/保留子表单（显式收发，类型安全）
const scheduleValue = computed<ScheduleRetentionValue>(() => ({
  cron: form.cron,
  keep_last: form.keep_last,
  keep_hourly: form.keep_hourly,
  keep_daily: form.keep_daily,
  keep_weekly: form.keep_weekly,
  keep_monthly: form.keep_monthly,
  keep_yearly: form.keep_yearly,
}))

function onScheduleUpdate(value: ScheduleRetentionValue): void {
  form.cron = value.cron
  form.keep_last = value.keep_last
  form.keep_hourly = value.keep_hourly
  form.keep_daily = value.keep_daily
  form.keep_weekly = value.keep_weekly
  form.keep_monthly = value.keep_monthly
  form.keep_yearly = value.keep_yearly
}
</script>

<template>
  <n-space vertical :size="16">
    <n-card :title="isEdit ? t('task.edit') : t('wizard.submitCreate')" size="small">
      <n-steps :current="step" size="small" class="wizard__steps">
        <n-step :title="t('wizard.step1')" />
        <n-step :title="t('wizard.step2')" />
        <n-step :title="t('wizard.step3')" />
        <n-step :title="t('wizard.step4')" />
        <n-step :title="t('wizard.step5')" />
      </n-steps>

      <n-alert v-if="isEdit && taskLoadFailed" type="error" :show-icon="false">
        {{ t('dashboard.loadFailed', { message: taskLoadFailed }) }}
      </n-alert>

      <n-space vertical :size="14" class="wizard__body">
        <!-- ① 基础信息 -->
        <template v-if="step === 1">
          <n-space vertical :size="4">
            <n-text strong>{{ t('wizard.name') }}</n-text>
            <n-input
              v-model:value="form.name"
              :placeholder="t('wizard.namePlaceholder')"
              :maxlength="64"
            />
          </n-space>

          <n-space align="center" :size="8">
            <n-switch v-model:value="form.enabled" size="small" />
            <n-text depth="3">{{ t('wizard.enabled') }}</n-text>
          </n-space>

          <n-text strong>{{ t('wizard.modeCard') }}</n-text>
          <TierCard :user="ui.me?.user ?? ''" :host-mount="ui.me?.host_mount ?? 'ro'" />

          <n-space align="center" :size="8">
            <n-text depth="3">{{ t('wizard.passfile') }}：</n-text>
            <n-tag size="small" round :type="passfileConfigured ? 'success' : 'warning'">
              {{ passfileConfigured ? t('wizard.passfileConfigured') : t('wizard.passfileMissing') }}
            </n-tag>
          </n-space>
        </template>

        <!-- ② 备份源 -->
        <template v-else-if="step === 2">
          <SourcesPicker v-model:sources="form.sources" v-model:excludes="form.excludes" />
        </template>

        <!-- ③ 存储目标 -->
        <template v-else-if="step === 3">
          <n-space vertical :size="4">
            <n-text strong>{{ t('wizard.repoReadonly') }}</n-text>
            <n-input :value="repoUrl" readonly :placeholder="t('wizard.repoReadonly')" />
            <n-space align="center" :size="8">
              <n-tag size="small" round :type="repoStatusQuery.data.value?.connected ? 'success' : 'error'">
                {{
                  repoStatusQuery.data.value?.connected
                    ? t('dashboard.repoConnected')
                    : t('dashboard.repoDisconnected')
                }}
              </n-tag>
              <n-text depth="3">{{ REPO_ID }}</n-text>
            </n-space>
            <n-text depth="3">{{ t('dashboard.repoCacheNote') }}</n-text>
          </n-space>

          <n-space align="center" :size="8">
            <n-button size="small" secondary :loading="testing" @click="onTestConnection">
              {{ t('wizard.testConnection') }}
            </n-button>
            <n-text v-if="testResult?.ok" type="success" depth="3">
              {{ t('wizard.testOk', { n: testResult.latency_ms }) }}
            </n-text>
          </n-space>
          <n-alert v-if="testResult && !testResult.ok" type="error" :show-icon="false">
            {{ testResult.reason || t('wizard.testFail') }}
          </n-alert>
        </template>

        <!-- ④ 调度 -->
        <template v-else-if="step === 4">
          <ScheduleRetentionForm :model-value="scheduleValue" @update:model-value="onScheduleUpdate" />
        </template>

        <!-- ⑤ 保留与首跑 -->
        <template v-else>
          <n-space v-if="isEdit" align="center" :size="8">
            <n-text depth="3">{{ t('wizard.tagReadonly') }}：</n-text>
            <n-tag size="small" round>{{ loadedTask?.tag ?? '' }}</n-tag>
          </n-space>

          <n-space vertical :size="6">
            <n-text strong>{{ t('wizard.retention') }}</n-text>
            <n-text depth="3">
              keep-last {{ form.keep_last }} · keep-hourly {{ form.keep_hourly }} · keep-daily
              {{ form.keep_daily }} · keep-weekly {{ form.keep_weekly }} · keep-monthly
              {{ form.keep_monthly }} · keep-yearly {{ form.keep_yearly }}
            </n-text>
          </n-space>

          <n-alert v-if="isEdit && loadedTask?.dry_run_pending" type="warning" :show-icon="false">
            {{ t('wizard.rescan_required') }}
          </n-alert>
          <n-alert v-else-if="sourcesChanged" type="warning" :show-icon="false">
            {{ t('wizard.rescan_required') }}
          </n-alert>

          <n-space vertical :size="8">
            <n-button size="small" secondary :loading="dryRunLoading" @click="onDryRun">
              {{ dryRunLoading ? t('wizard.dry_run_running') : t('wizard.dry_run') }}
            </n-button>
            <n-text depth="3" class="wizard__hint">{{ t('wizard.dry_run_hint') }}</n-text>
          </n-space>

          <n-alert v-if="dryRunError" type="error" :show-icon="false">{{ dryRunError }}</n-alert>
          <template v-if="dryRunResult">
            <n-alert v-if="dryRunResult.error" type="error" :show-icon="false">
              {{ dryRunResult.error }}
            </n-alert>
            <n-alert v-if="dryRunResult.truncated" type="warning" :show-icon="false">
              {{ t('wizard.dry_run_truncated') }}
            </n-alert>
            <n-alert
              v-if="dryRunResult.warnings.length"
              type="warning"
              :title="t('wizard.dry_run_pseudo')"
              :show-icon="false"
            >
              <n-space vertical :size="2">
                <n-text v-for="warning in dryRunResult.warnings" :key="warning.path" depth="3">
                  {{ warning.path }}
                </n-text>
              </n-space>
            </n-alert>
            <n-text depth="3">{{ t('wizard.dry_run_count', { n: dryRunResult.items.length }) }}</n-text>
            <n-space :size="6" class="wizard__preview">
              <n-tag
                v-for="item in dryRunResult.items.slice(0, 200)"
                :key="item.display_path"
                size="small"
              >
                {{ item.display_path }}
              </n-tag>
            </n-space>
          </template>
        </template>

        <n-space justify="end">
          <n-button v-if="step > 1" quaternary @click="goPrev">{{ t('confirm.back') }}</n-button>
          <n-button v-if="step < 5" type="primary" @click="goNext">{{ t('confirm.next') }}</n-button>
          <template v-else>
            <n-button quaternary :loading="saving" @click="onSaveOnly">
              {{ isEdit ? t('wizard.submitUpdate') : t('wizard.save_only') }}
            </n-button>
            <n-tooltip v-if="!dryRunOk" trigger="hover">
              <template #trigger>
                <n-button type="primary" disabled>
                  {{ t('wizard.dry_run_confirm') }}
                </n-button>
              </template>
              {{ t('wizard.dry_run_hint') }}
            </n-tooltip>
            <n-button v-else type="primary" :loading="saving" @click="onSaveAndRun">
              {{ t('wizard.dry_run_confirm') }}
            </n-button>
          </template>
        </n-space>
      </n-space>
    </n-card>
  </n-space>
</template>

<style scoped>
.wizard__steps {
  margin-bottom: 8px;
}

.wizard__body {
  margin-top: 16px;
}

.wizard__hint {
  display: block;
  font-size: 12px;
}

.wizard__preview {
  max-height: 200px;
  overflow-y: auto;
}
</style>
