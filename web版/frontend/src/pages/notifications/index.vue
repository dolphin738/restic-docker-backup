<script setup lang="ts">
import { useQuery } from '@tanstack/vue-query'
import {
  NAlert,
  NButton,
  NCard,
  NForm,
  NFormItem,
  NInput,
  NInputNumber,
  NSpace,
  NSwitch,
  NTag,
  NText,
  useMessage,
} from 'naive-ui'
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getNotifyConfig, testNotify, updateNotifyConfig } from '@/api/notify'
import type { NotifyTestResponse } from '@/api/types'

const { t } = useI18n()
const message = useMessage()

const configQuery = useQuery({ queryKey: ['notify'], queryFn: getNotifyConfig })

// 表单态（非敏感字段直接绑定；敏感 URL 用独立输入框，留空 = 不修改）
const healthchecksUrl = ref('')
const webhookUrl = ref('')
const webhookTemplate = ref('')
const meowNickname = ref('')
const onSuccess = ref(false)
const skippedThreshold = ref<number>(100)
const webhookRedact = ref(false)
const meowRedact = ref(true)
// 敏感 URL「清除」意图（点击清除按钮后保存时提交空串）
const clearHealthchecks = ref(false)
const clearWebhook = ref(false)

watch(configQuery.data, (data) => {
  if (!data) {
    return
  }
  webhookTemplate.value = data.webhook_template
  meowNickname.value = data.meow_nickname
  onSuccess.value = data.on_success
  skippedThreshold.value = data.skipped_threshold
  webhookRedact.value = data.webhook_redact
  meowRedact.value = data.meow_redact
})

const saving = ref(false)
const testing = ref(false)
const testResult = ref<NotifyTestResponse | null>(null)

const hasAnyChannel = computed(
  () =>
    configQuery.data.value?.healthchecks_url_set ||
    configQuery.data.value?.webhook_url_set ||
    (configQuery.data.value?.meow_nickname ?? '') !== '' ||
    healthchecksUrl.value.trim() !== '' ||
    webhookUrl.value.trim() !== '' ||
    meowNickname.value.trim() !== '',
)

async function save(): Promise<void> {
  saving.value = true
  try {
    await updateNotifyConfig({
      ...(clearHealthchecks.value ? { healthchecks_url: '' } : {}),
      ...(healthchecksUrl.value.trim() ? { healthchecks_url: healthchecksUrl.value.trim() } : {}),
      ...(clearWebhook.value ? { webhook_url: '' } : {}),
      ...(webhookUrl.value.trim() ? { webhook_url: webhookUrl.value.trim() } : {}),
      webhook_template: webhookTemplate.value,
      meow_nickname: meowNickname.value.trim(),
      on_success: onSuccess.value,
      skipped_threshold: skippedThreshold.value ?? 100,
      webhook_redact: webhookRedact.value,
      meow_redact: meowRedact.value,
    })
    message.success(t('notify.saved'))
    clearHealthchecks.value = false
    clearWebhook.value = false
    healthchecksUrl.value = ''
    webhookUrl.value = ''
    await configQuery.refetch()
  } catch (error) {
    // AppError 已有全局提示；此处仅防未处理异常冒泡
  } finally {
    saving.value = false
  }
}

async function runTest(): Promise<void> {
  testing.value = true
  testResult.value = null
  try {
    testResult.value = await testNotify()
  } catch (error) {
    // AppError 已有全局提示
  } finally {
    testing.value = false
  }
}
</script>

<template>
  <n-space vertical :size="16">
    <n-card :title="t('nav.notifications')" size="small">
      <n-space vertical :size="12">
        <n-alert type="info" :show-icon="false">{{ t('notify.intro') }}</n-alert>
        <n-alert v-if="!hasAnyChannel" type="warning" :show-icon="false">
          {{ t('notify.noChannel') }}
        </n-alert>

        <n-form label-placement="left" label-width="180" size="small" class="notify__form">
          <n-form-item :label="t('notify.healthchecksUrl')">
            <n-space vertical :size="4" class="notify__field">
              <n-input
                v-model:value="healthchecksUrl"
                :placeholder="
                  configQuery.data.value?.healthchecks_url_set
                    ? t('notify.urlConfigured')
                    : t('notify.healthchecksPlaceholder')
                "
                :disabled="clearHealthchecks"
              />
              <n-text v-if="configQuery.data.value?.healthchecks_url_set" depth="3" class="notify__hint">
                {{ t('notify.urlKeepHint') }}
                <n-button size="tiny" quaternary type="warning" @click="clearHealthchecks = !clearHealthchecks">
                  {{ clearHealthchecks ? t('notify.urlClearUndo') : t('notify.urlClear') }}
                </n-button>
              </n-text>
            </n-space>
          </n-form-item>

          <n-form-item :label="t('notify.webhookUrl')">
            <n-space vertical :size="4" class="notify__field">
              <n-input
                v-model:value="webhookUrl"
                :placeholder="
                  configQuery.data.value?.webhook_url_set
                    ? t('notify.urlConfigured')
                    : t('notify.webhookPlaceholder')
                "
                :disabled="clearWebhook"
              />
              <n-text v-if="configQuery.data.value?.webhook_url_set" depth="3" class="notify__hint">
                {{ t('notify.urlKeepHint') }}
                <n-button size="tiny" quaternary type="warning" @click="clearWebhook = !clearWebhook">
                  {{ clearWebhook ? t('notify.urlClearUndo') : t('notify.urlClear') }}
                </n-button>
              </n-text>
            </n-space>
          </n-form-item>

          <n-form-item :label="t('notify.webhookTemplate')">
            <n-input
              v-model:value="webhookTemplate"
              type="textarea"
              :rows="3"
              :placeholder="t('notify.templatePlaceholder')"
              class="notify__field"
            />
          </n-form-item>

          <n-form-item :label="t('notify.meowNickname')">
            <n-input
              v-model:value="meowNickname"
              :placeholder="t('notify.meowPlaceholder')"
              class="notify__field"
            />
          </n-form-item>

          <n-form-item :label="t('notify.onSuccess')">
            <n-space align="center" :size="8">
              <n-switch v-model:value="onSuccess" size="small" />
              <n-text depth="3" class="notify__hint">{{ t('notify.onSuccessHint') }}</n-text>
            </n-space>
          </n-form-item>

          <n-form-item :label="t('notify.skippedThreshold')">
            <n-space align="center" :size="8">
              <n-input-number v-model:value="skippedThreshold" :min="0" size="small" class="notify__num" />
              <n-text depth="3" class="notify__hint">{{ t('notify.skippedThresholdHint') }}</n-text>
            </n-space>
          </n-form-item>

          <n-form-item :label="t('notify.webhookRedact')">
            <n-space align="center" :size="8">
              <n-switch v-model:value="webhookRedact" size="small" />
              <n-text depth="3" class="notify__hint">{{ t('notify.redactHint') }}</n-text>
            </n-space>
          </n-form-item>

          <n-form-item :label="t('notify.meowRedact')">
            <n-space align="center" :size="8">
              <n-switch v-model:value="meowRedact" size="small" />
              <n-text depth="3" class="notify__hint">{{ t('notify.redactHint') }}</n-text>
            </n-space>
          </n-form-item>
        </n-form>

        <n-space :size="10">
          <n-button size="small" type="primary" :loading="saving" @click="save">
            {{ t('common.save') }}
          </n-button>
          <n-button size="small" secondary :loading="testing" :disabled="!hasAnyChannel" @click="runTest">
            {{ t('notify.test') }}
          </n-button>
        </n-space>

        <!-- E24 逐渠道结果（失败必须带原因，禁止静默） -->
        <n-space v-if="testResult" vertical :size="6">
          <n-space align="center" :size="8">
            <n-tag size="small" round :type="testResult.ok ? 'success' : 'error'">
              {{ testResult.ok ? t('notify.testOk') : t('notify.testFailed') }}
            </n-tag>
            <n-text depth="3" class="notify__hint">{{ testResult.latency_ms }} ms</n-text>
          </n-space>
          <template v-for="channel in testResult.channels" :key="channel.channel">
            <n-space align="center" :size="8">
              <n-tag size="small" round :type="channel.ok ? 'success' : 'error'">{{ channel.channel }}</n-tag>
              <n-text v-if="channel.reason" depth="2" class="notify__hint">{{ channel.reason }}</n-text>
            </n-space>
          </template>
          <n-text v-if="testResult.reason" depth="2" class="notify__hint">{{ testResult.reason }}</n-text>
        </n-space>
      </n-space>
    </n-card>
  </n-space>
</template>

<style scoped>
.notify__form {
  max-width: 720px;
}

.notify__field {
  width: 100%;
}

.notify__hint {
  font-size: 12px;
}

.notify__num {
  width: 160px;
}
</style>
