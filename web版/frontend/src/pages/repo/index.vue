<script setup lang="ts">
import { useQuery } from '@tanstack/vue-query'
import {
  NAlert,
  NButton,
  NCard,
  NForm,
  NFormItem,
  NGrid,
  NGi,
  NInput,
  NSpace,
  NStatistic,
  NTag,
  NText,
  NTooltip,
  useMessage,
} from 'naive-ui'
import { computed, onUnmounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getConfig, putConfig, testRepo } from '@/api/config'
import { createJob } from '@/api/jobs'
import { getRepoStatus } from '@/api/repo'
import { isAppError } from '@/api/http'
import type { ConfigItem, RepoTestResponse } from '@/api/types'
import ConfirmGate from '@/components/ConfirmGate.vue'
import JobProgress from '@/components/JobProgress.vue'
import { useUiStore } from '@/stores/ui'

const { t } = useI18n()
const ui = useUiStore()

const message = useMessage()
ui.setMessageApi(message)
onUnmounted(() => ui.setMessageApi(null))

type RepoType = 'local' | 's3' | 'b2'

interface CredField {
  key: string
  labelKey: string
}

/** 凭据键表（§4.3）：全部为 cred.* 敏感项，API 只回 has_value + source。 */
const CRED_FIELDS: Record<RepoType, CredField[]> = {
  local: [],
  s3: [
    { key: 'cred.aws_access_key_id', labelKey: 'repo.credAwsAccessKeyId' },
    { key: 'cred.aws_secret_access_key', labelKey: 'repo.credAwsSecretAccessKey' },
    { key: 'cred.aws_default_region', labelKey: 'repo.credAwsDefaultRegion' },
    { key: 'cred.aws_endpoint_url', labelKey: 'repo.credAwsEndpointUrl' },
  ],
  b2: [
    { key: 'cred.b2_account_id', labelKey: 'repo.credB2AccountId' },
    { key: 'cred.b2_account_key', labelKey: 'repo.credB2AccountKey' },
  ],
}

const configQuery = useQuery({ queryKey: ['config'], queryFn: getConfig })

// E30：服务端 30s 缓存，60s 轮询 + 手动刷新
const statusQuery = useQuery({
  queryKey: ['repo', 'status'],
  queryFn: getRepoStatus,
  refetchInterval: 60_000,
})

const form = reactive({ url: '', hostTag: '' })
const credInput = reactive<Record<string, string>>({})
const initial = reactive({ url: '', hostTag: '' })
const saving = ref(false)
const testing = ref(false)
const testResult = ref<RepoTestResponse | null>(null)
const unlockShow = ref(false)
const unlocking = ref(false)
const unlockJobId = ref('')

const itemMap = computed<Map<string, ConfigItem>>(() => {
  const map = new Map<string, ConfigItem>()
  for (const item of configQuery.data.value?.items ?? []) {
    map.set(item.key, item)
  }
  return map
})

function itemOf(key: string): ConfigItem | undefined {
  return itemMap.value.get(key)
}

/** 仓库类型是服务端只读派生键（E06 回 value，E07 直写会 409 READONLY_KEY），
 * 由 repo.url 协议推导；此处仅作未保存 URL 时的本地预览。 */
const repoType = computed<RepoType>(() => {
  const stored = itemOf('repo.type')?.value
  if (stored === 's3' || stored === 'b2' || stored === 'local') {
    return stored
  }
  const url = form.url.trim()
  if (url.startsWith('s3://')) return 's3'
  if (url.startsWith('b2:')) return 'b2'
  return 'local'
})

const credFields = computed<CredField[]>(() => CRED_FIELDS[repoType.value])

// 配置加载后回填表单（敏感项无明文，只回填非敏感键）
watch(
  () => configQuery.data.value,
  (data) => {
    if (!data) {
      return
    }
    const url = data.items.find((item) => item.key === 'repo.url')
    const hostTag = data.items.find((item) => item.key === 'repo.host_tag')
    form.url = url?.value ?? ''
    form.hostTag = hostTag?.value ?? ''
    initial.url = form.url
    initial.hostTag = form.hostTag
  },
  { immediate: true },
)

const repoStatus = computed(() => statusQuery.data.value ?? null)

const capacityText = computed(() => {
  const size = repoStatus.value?.capacity.size
  return size ? formatBytes(size) : t('common.none')
})

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

async function onSave(): Promise<void> {
  const patch: Record<string, string | null> = {}
  if (form.url !== initial.url) {
    patch['repo.url'] = form.url
  }
  if (form.hostTag !== initial.hostTag) {
    patch['repo.host_tag'] = form.hostTag || null
  }
  for (const field of credFields.value) {
    const value = credInput[field.key]
    if (value) {
      patch[field.key] = value
    }
  }
  if (Object.keys(patch).length === 0) {
    message.info(t('repo.noChanges'))
    return
  }
  saving.value = true
  try {
    await putConfig(patch)
    message.success(t('repo.saved'))
    clearCredInput()
    await configQuery.refetch()
  } finally {
    saving.value = false
  }
}

function clearCredInput(): void {
  for (const key of Object.keys(credInput)) {
    credInput[key] = ''
  }
}

async function onTest(): Promise<void> {
  testing.value = true
  testResult.value = null
  try {
    testResult.value = await testRepo()
  } finally {
    testing.value = false
  }
}

async function onUnlock(): Promise<void> {
  unlocking.value = true
  try {
    const created = await createJob('unlock')
    unlockJobId.value = created.id
    message.success(t('repo.unlockSubmitted'))
    void statusQuery.refetch()
  } catch (error) {
    // 409 JOB_CONFLICT / NOT_CANCELLABLE 已由全局提示展示
    if (!isAppError(error)) {
      throw error
    }
  } finally {
    unlocking.value = false
  }
}
</script>

<template>
  <n-space vertical :size="16">
    <n-grid cols="1 1100:2" :x-gap="16" :y-gap="16" responsive="screen">
      <!-- 仓库与凭据 -->
      <n-gi>
        <n-card :title="t('nav.repo')" size="small">
          <n-space vertical :size="12">
            <n-form label-placement="top" size="small">
              <n-form-item :label="t('repo.type')">
                <n-space vertical :size="4" class="repo__cred">
                  <n-space align="center" :size="6">
                    <n-tag size="small" round :type="repoType === 'local' ? 'default' : 'info'">
                      {{ repoType === 'local' ? t('repo.typeLocal') : repoType === 's3' ? t('repo.typeS3') : t('repo.typeB2') }}
                    </n-tag>
                    <n-tag v-if="itemOf('repo.type')?.source === 'env'" size="tiny" round type="warning">
                      {{ t('repo.credSourceEnv') }}
                    </n-tag>
                  </n-space>
                  <n-text depth="3">{{ t('repo.typeReadonlyNote') }}</n-text>
                </n-space>
              </n-form-item>
              <n-form-item :label="t('repo.url')">
                <n-input v-model:value="form.url" :placeholder="t('repo.urlPlaceholder')" />
              </n-form-item>
              <n-form-item :label="t('repo.hostTag')">
                <n-input v-model:value="form.hostTag" :placeholder="t('repo.hostTagPlaceholder')" />
              </n-form-item>

              <!-- 凭据（脱敏：只回 has_value + source；env 覆盖的键置灰并给原因） -->
              <template v-if="credFields.length">
                <n-text strong>{{ t('repo.credentials') }}</n-text>
                <n-form-item
                  v-for="field in credFields"
                  :key="field.key"
                  :label="t(field.labelKey)"
                >
                  <n-space vertical :size="4" class="repo__cred">
                    <n-space align="center" :size="6">
                      <n-tag
                        size="tiny"
                        round
                        :type="itemOf(field.key)?.has_value ? 'success' : 'default'"
                      >
                        {{ itemOf(field.key)?.has_value ? t('repo.credSet') : t('repo.credUnset') }}
                      </n-tag>
                      <n-tag
                        v-if="itemOf(field.key)?.source === 'env'"
                        size="tiny"
                        round
                        type="warning"
                      >
                        {{ t('repo.credSourceEnv') }}
                      </n-tag>
                    </n-space>
                    <n-tooltip v-if="itemOf(field.key)?.source === 'env'" trigger="hover">
                      <template #trigger>
                        <n-input
                          :value="''"
                          :placeholder="t('repo.credEnvNote')"
                          type="password"
                          show-password-on="click"
                          disabled
                        />
                      </template>
                      {{ t('repo.credEnvNote') }}
                    </n-tooltip>
                    <n-input
                      v-else
                      v-model:value="credInput[field.key]"
                      :placeholder="t('repo.credNewPlaceholder')"
                      type="password"
                      show-password-on="click"
                    />
                  </n-space>
                </n-form-item>
              </template>
            </n-form>

            <n-space>
              <n-button type="primary" :loading="saving" @click="onSave">{{ t('repo.save') }}</n-button>
              <n-button secondary :loading="testing" @click="onTest">{{ t('repo.testBtn') }}</n-button>
            </n-space>

            <n-alert v-if="testResult?.ok" type="success" :show-icon="false">
              {{ t('repo.testOk', { n: testResult.latency_ms }) }}
            </n-alert>
            <n-alert v-else-if="testResult && !testResult.ok" type="error" :show-icon="false">
              {{ testResult.reason || t('repo.testFail') }}
            </n-alert>

            <n-text depth="3">{{ t('repo.passfileStatus') }}：
              {{ configQuery.data.value?.passfile_status === 'configured'
                ? t('repo.passfileConfigured')
                : t('repo.passfileMissing') }}
            </n-text>
          </n-space>
        </n-card>
      </n-gi>

      <!-- 仓库状态 -->
      <n-gi>
        <n-card :title="t('dashboard.repoCard')" size="small">
          <template #header-extra>
            <n-button size="tiny" secondary :loading="statusQuery.isFetching.value" @click="statusQuery.refetch()">
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
            </n-space>
            <n-alert v-if="repoStatus?.last_error" type="error" :show-icon="false">
              {{ t('dashboard.repoLastError') }}：{{ repoStatus.last_error }}
            </n-alert>
            <template v-if="repoStatus?.locked">
              <n-alert type="warning" :show-icon="false">{{ t('repo.lockedNote') }}</n-alert>
              <n-button size="small" type="warning" @click="unlockShow = true">
                {{ t('repo.unlockBtn') }}
              </n-button>
            </template>
            <n-text depth="3" class="repo__note">{{ t('dashboard.repoCacheNote') }}</n-text>

            <template v-if="unlockJobId">
              <JobProgress :job-id="unlockJobId" :title="t('repo.unlockJobTitle')" />
            </template>
          </n-space>
        </n-card>
      </n-gi>
    </n-grid>

    <!-- 手动解锁：三步确认门 -->
    <ConfirmGate
      v-model:show="unlockShow"
      :title="t('repo.unlockBtn')"
      :keyword="'UNLOCK'"
      :description="t('repo.unlockDesc')"
      :loading="unlocking"
      @confirm="onUnlock"
    >
      <template #preview>
        <n-text depth="3">{{ t('repo.unlockPreview') }}</n-text>
      </template>
    </ConfirmGate>
  </n-space>
</template>

<style scoped>
.repo__note {
  display: block;
  font-size: 12px;
}

.repo__cred {
  width: 100%;
}
</style>
