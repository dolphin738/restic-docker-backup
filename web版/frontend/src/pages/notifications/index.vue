<script setup lang="ts">
/** 通知设置页（R-018，A 方案 2026-10-20 重构）。
 *
 * 结构（对齐仓库页「先选类型 → 出该类型配置项」范式）：
 *   ① 通知渠道卡：渠道类型下拉 → 仅渲染该类型的配置项 → 保存（只提交该渠道的键）；
 *   ② 已保存的渠道列表：四类渠道各一行（状态 pill + 脱敏摘要 + 编辑/清除）；
 *   ③ 告警规则卡：全局 on_success / skipped_threshold（对所有渠道生效）。
 *
 * 后端契约不变：E23 三态写入（undefined=不变 / ''=清除 / 非空=设置），敏感 URL
 * 只回 *_set 不回明文（S3），故本页零后端改动。
 */

import { useQuery } from '@tanstack/vue-query'
import {
  NAlert,
  NButton,
  NCard,
  NForm,
  NFormItem,
  NInput,
  NInputNumber,
  NSelect,
  NSpace,
  NSwitch,
  NText,
  useMessage,
} from 'naive-ui'
import { computed, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getNotifyConfig, testNotify, updateNotifyConfig } from '@/api/notify'
import type { NotifyConfigPatch, NotifyConfigResponse, NotifyTestResponse } from '@/api/types'
import ConfirmGate from '@/components/ConfirmGate.vue'
import StatusTag from '@/components/StatusTag.vue'

const { t } = useI18n()
const message = useMessage()

const configQuery = useQuery({ queryKey: ['notify'], queryFn: getNotifyConfig })

// ---------------------------------------------------------------- 渠道类型（A 方案：每种渠道一套配置键）

type ChannelKey = 'healthchecks' | 'webhook' | 'meow' | 'mail'

const CHANNEL_KEYS: ChannelKey[] = ['healthchecks', 'webhook', 'meow', 'mail']

const channelType = ref<ChannelKey | ''>('')

const channelOptions = computed(() => CHANNEL_KEYS.map((key) => ({ label: t(`notify.channel.${key}`), value: key })))

/** 渠道是否已配置（与 Notifier 运行期启用判定同口径：对应键有值）。 */
function isConfigured(key: ChannelKey, data: NotifyConfigResponse | undefined): boolean {
  if (!data) {
    return false
  }
  switch (key) {
    case 'healthchecks':
      return data.healthchecks_url_set
    case 'webhook':
      return data.webhook_url_set
    case 'meow':
      return data.meow_nickname.trim() !== ''
    default:
      return data.mail_password_set || data.mail_host.trim() !== ''
  }
}

const hasAnyChannel = computed(() => CHANNEL_KEYS.some((key) => isConfigured(key, configQuery.data.value)))

/** 当前所选 MeoW 套餐的额度说明（与后端 MEOW_PLAN_LIMITS 同口径，改档位文案要同步改）。 */
const planDesc = computed(() => {
  const key = {
    free: 'notify.meowPlanFreeDesc',
    premium: 'notify.meowPlanPremiumDesc',
    lifetime: 'notify.meowPlanLifetimeDesc',
  }[form.meowPlan]
  return `${t(key)}｜${t('notify.meowPlanHint')}`
})

/** 列表行摘要（脱敏口径：URL/密码一律不回显，只给状态性描述）。 */
function channelSummary(key: ChannelKey): string {
  const data = configQuery.data.value
  if (!data) {
    return t('notify.list.summaryEmpty')
  }
  if (key === 'healthchecks') {
    return data.healthchecks_url_set ? t('notify.list.summarySaved') : t('notify.list.summaryEmpty')
  }
  if (key === 'webhook') {
    if (!data.webhook_url_set) {
      return t('notify.list.summaryEmpty')
    }
    return data.webhook_template.trim() ? t('notify.list.summaryTemplateSet') : t('notify.list.summaryDefaultTemplate')
  }
  if (key === 'meow') {
    const nickname = data.meow_nickname.trim()
    return nickname ? t('notify.list.summaryNickname', { name: nickname }) : t('notify.list.summaryEmpty')
  }
  const host = data.mail_host.trim()
  if (!host && !data.mail_password_set) {
    return t('notify.list.summaryEmpty')
  }
  if (!host) {
    return t('notify.list.summarySaved')
  }
  const from = data.mail_from.trim()
  return from ? t('notify.list.summaryMail', { host, from }) : host
}

// ---------------------------------------------------------------- 表单态（敏感字段留空=不修改；clear=提交空串）

const form = reactive({
  healthchecksUrl: '',
  webhookUrl: '',
  webhookTemplate: '',
  webhookRedact: false,
  meowNickname: '',
  meowMsgType: 'text' as 'text' | 'html',
  meowHtmlHeight: 200,
  meowUrl: '',
  meowImgUrl: '',
  meowPlan: 'free' as 'free' | 'premium' | 'lifetime',
  meowRedact: true,
  mailHost: '',
  mailPort: 587,
  mailUsername: '',
  mailPassword: '',
  mailFrom: '',
  mailTo: '',
  mailSecurity: 'starttls' as 'none' | 'starttls' | 'tls',
  mailRedact: false,
})

/** 敏感字段「清除」意图（点击清除后保存时提交空串）。 */
const clear = reactive({
  healthchecksUrl: false,
  webhookUrl: false,
  mailPassword: false,
})

/** 把某类渠道的已存值装进表单（敏感 URL 不回显：输入框留空 + placeholder 提示已配置）。 */
function loadChannel(key: ChannelKey): void {
  channelType.value = key
  const data = configQuery.data.value
  if (!data) {
    return
  }
  if (key === 'healthchecks') {
    form.healthchecksUrl = ''
    clear.healthchecksUrl = false
    return
  }
  if (key === 'webhook') {
    form.webhookUrl = ''
    form.webhookTemplate = data.webhook_template
    form.webhookRedact = data.webhook_redact
    clear.webhookUrl = false
    return
  }
  if (key === 'meow') {
    form.meowNickname = data.meow_nickname
    form.meowMsgType = data.meow_msg_type
    form.meowHtmlHeight = data.meow_html_height
    form.meowUrl = data.meow_url
    form.meowImgUrl = data.meow_img_url
    form.meowPlan = data.meow_plan
    form.meowRedact = data.meow_redact
    return
  }
  form.mailHost = data.mail_host
  form.mailPort = data.mail_port
  form.mailUsername = data.mail_username
  form.mailPassword = ''
  form.mailFrom = data.mail_from
  form.mailTo = data.mail_to
  form.mailSecurity = data.mail_security
  form.mailRedact = data.mail_redact
  clear.mailPassword = false
}

function channelMissing(): string | null {
  switch (channelType.value) {
    case 'healthchecks':
      return clear.healthchecksUrl || form.healthchecksUrl.trim() ? null : t('notify.channel.needUrl')
    case 'webhook':
      return clear.webhookUrl || form.webhookUrl.trim() ? null : t('notify.channel.needUrl')
    case 'meow':
      return form.meowNickname.trim() ? null : t('notify.channel.needNickname')
    case 'mail':
      return form.mailHost.trim() ? null : t('notify.channel.needHost')
    default:
      return t('notify.channel.needType')
  }
}

const savingChannel = ref(false)

/** 保存当前类型渠道：patch 只含该渠道的键（其余键 undefined=不变）。 */
async function saveChannel(): Promise<void> {
  if (savingChannel.value) {
    return
  }
  const missing = channelMissing()
  if (missing) {
    message.warning(missing)
    return
  }
  const patch: NotifyConfigPatch = {}
  if (channelType.value === 'healthchecks') {
    if (clear.healthchecksUrl) {
      patch.healthchecks_url = ''
    } else if (form.healthchecksUrl.trim()) {
      patch.healthchecks_url = form.healthchecksUrl.trim()
    }
  } else if (channelType.value === 'webhook') {
    if (clear.webhookUrl) {
      patch.webhook_url = ''
    } else if (form.webhookUrl.trim()) {
      patch.webhook_url = form.webhookUrl.trim()
    }
    patch.webhook_template = form.webhookTemplate
    patch.webhook_redact = form.webhookRedact
  } else if (channelType.value === 'meow') {
    patch.meow_nickname = form.meowNickname.trim()
    patch.meow_msg_type = form.meowMsgType
    patch.meow_html_height = form.meowHtmlHeight ?? 200
    // 可选增强：留空即清除（E23 三态：空串 = 清除该键）
    patch.meow_url = form.meowUrl.trim()
    patch.meow_img_url = form.meowImgUrl.trim()
    patch.meow_plan = form.meowPlan
    patch.meow_redact = form.meowRedact
  } else {
    patch.mail_host = form.mailHost.trim()
    patch.mail_port = form.mailPort ?? 587
    patch.mail_username = form.mailUsername.trim()
    if (clear.mailPassword) {
      patch.mail_password = ''
    } else if (form.mailPassword) {
      patch.mail_password = form.mailPassword
    }
    patch.mail_from = form.mailFrom.trim()
    patch.mail_to = form.mailTo.trim()
    patch.mail_security = form.mailSecurity
    patch.mail_redact = form.mailRedact
  }
  if (Object.keys(patch).length === 0) {
    return
  }
  savingChannel.value = true
  try {
    await updateNotifyConfig(patch)
    message.success(t('notify.channel.saved'))
    clear.healthchecksUrl = false
    clear.webhookUrl = false
    clear.mailPassword = false
    form.healthchecksUrl = ''
    form.webhookUrl = ''
    form.mailPassword = ''
    await configQuery.refetch()
  } catch {
    // AppError 已有全局提示；此处仅防未处理异常冒泡
  } finally {
    savingChannel.value = false
  }
}

// ---------------------------------------------------------------- 清除渠道（确认门；patch 逐键置空）

const clearGateShow = ref(false)
const clearTarget = ref<ChannelKey | null>(null)
const clearing = ref(false)

function onClearClick(key: ChannelKey): void {
  clearTarget.value = key
  clearGateShow.value = true
}

/** 清除 patch：身份类键置空（端口/加密/脱敏属缺省形态，不动即不变）。 */
function clearPatchFor(key: ChannelKey): NotifyConfigPatch {
  if (key === 'healthchecks') {
    return { healthchecks_url: '' }
  }
  if (key === 'webhook') {
    return { webhook_url: '', webhook_template: '' }
  }
  if (key === 'meow') {
    return { meow_nickname: '' }
  }
  return { mail_host: '', mail_username: '', mail_password: '', mail_from: '', mail_to: '' }
}

async function confirmClear(): Promise<void> {
  const key = clearTarget.value
  if (!key || clearing.value) {
    return
  }
  clearing.value = true
  try {
    await updateNotifyConfig(clearPatchFor(key))
    message.success(t('notify.list.cleared'))
    await configQuery.refetch()
    if (channelType.value === key) {
      loadChannel(key)
    }
  } catch {
    // AppError 已有全局提示
  } finally {
    clearing.value = false
    clearTarget.value = null
  }
}

// ---------------------------------------------------------------- 告警规则（全局：on_success / skipped_threshold）

const onSuccess = ref(false)
const skippedThreshold = ref<number>(100)
const savingRules = ref(false)

watch(configQuery.data, (data) => {
  if (!data) {
    return
  }
  onSuccess.value = data.on_success
  skippedThreshold.value = data.skipped_threshold
})

async function saveRules(): Promise<void> {
  if (savingRules.value) {
    return
  }
  savingRules.value = true
  try {
    await updateNotifyConfig({ on_success: onSuccess.value, skipped_threshold: skippedThreshold.value ?? 100 })
    message.success(t('notify.rules.saved'))
    await configQuery.refetch()
  } catch {
    // AppError 已有全局提示
  } finally {
    savingRules.value = false
  }
}

// ---------------------------------------------------------------- E24 测试消息（逐渠道结果，失败必须带原因）

const testing = ref(false)
const testResult = ref<NotifyTestResponse | null>(null)

async function runTest(): Promise<void> {
  testing.value = true
  testResult.value = null
  try {
    testResult.value = await testNotify()
  } catch {
    // AppError 已有全局提示
  } finally {
    testing.value = false
  }
}
</script>

<template>
  <n-space vertical :size="16">
    <!-- ① 通知渠道：类型下拉 + 类型专属配置项 -->
    <n-card :title="t('notify.channel.title')" size="small">
      <n-space vertical :size="12">
        <n-alert type="info" :show-icon="false">{{ t('notify.channel.hint') }}</n-alert>

        <n-form label-placement="left" label-width="160" size="small" class="notify__form">
          <n-form-item :label="t('notify.channel.type')">
            <n-select
              v-model:value="channelType"
              :options="channelOptions"
              :placeholder="t('notify.channel.typePlaceholder')"
              class="notify__type"
              @update:value="(value) => value && loadChannel(value as ChannelKey)"
            />
          </n-form-item>

          <!-- 双层 template：外层按类型分支，内层为该类型字段组 -->
          <template v-if="channelType === 'healthchecks'">
            <n-form-item :label="t('notify.healthchecksUrl')">
              <n-space vertical :size="4" class="notify__field">
                <n-input
                  v-model:value="form.healthchecksUrl"
                  :placeholder="
                    configQuery.data.value?.healthchecks_url_set
                      ? t('notify.urlConfigured')
                      : t('notify.healthchecksPlaceholder')
                  "
                  :disabled="clear.healthchecksUrl"
                />
                <n-text v-if="configQuery.data.value?.healthchecks_url_set" depth="3" class="notify__hint">
                  {{ t('notify.urlKeepHint') }}
                  <n-button size="tiny" quaternary type="warning" @click="clear.healthchecksUrl = !clear.healthchecksUrl">
                    {{ clear.healthchecksUrl ? t('notify.urlClearUndo') : t('notify.urlClear') }}
                  </n-button>
                </n-text>
              </n-space>
            </n-form-item>
          </template>

          <template v-else-if="channelType === 'webhook'">
            <n-form-item :label="t('notify.webhookUrl')">
              <n-space vertical :size="4" class="notify__field">
                <n-input
                  v-model:value="form.webhookUrl"
                  :placeholder="
                    configQuery.data.value?.webhook_url_set ? t('notify.urlConfigured') : t('notify.webhookPlaceholder')
                  "
                  :disabled="clear.webhookUrl"
                />
                <n-text v-if="configQuery.data.value?.webhook_url_set" depth="3" class="notify__hint">
                  {{ t('notify.urlKeepHint') }}
                  <n-button size="tiny" quaternary type="warning" @click="clear.webhookUrl = !clear.webhookUrl">
                    {{ clear.webhookUrl ? t('notify.urlClearUndo') : t('notify.urlClear') }}
                  </n-button>
                </n-text>
              </n-space>
            </n-form-item>
            <n-form-item :label="t('notify.webhookTemplate')">
              <n-input
                v-model:value="form.webhookTemplate"
                type="textarea"
                :rows="3"
                :placeholder="t('notify.templatePlaceholder')"
                class="notify__field"
              />
            </n-form-item>
            <n-form-item :label="t('notify.webhookRedact')">
              <n-space align="center" :size="8">
                <n-switch v-model:value="form.webhookRedact" size="small" :aria-label="t('notify.redactHint')" />
                <n-text depth="3" class="notify__hint">{{ t('notify.redactHint') }}</n-text>
              </n-space>
            </n-form-item>
          </template>

          <template v-else-if="channelType === 'meow'">
            <n-form-item :label="t('notify.meowNickname')">
              <n-input
                v-model:value="form.meowNickname"
                :placeholder="t('notify.meowPlaceholder')"
                class="notify__field"
              />
            </n-form-item>
            <n-form-item :label="t('notify.meowMsgType')">
              <n-space vertical :size="4" class="notify__field">
                <n-select
                  v-model:value="form.meowMsgType"
                  :options="[
                    { label: t('notify.meowMsgTypeText'), value: 'text' },
                    { label: t('notify.meowMsgTypeHtml'), value: 'html' },
                  ]"
                  class="notify__type"
                />
                <n-text depth="3" class="notify__hint">{{ t('notify.meowMsgTypeHint') }}</n-text>
              </n-space>
            </n-form-item>
            <template v-if="form.meowMsgType === 'html'">
              <n-form-item :label="t('notify.meowHtmlHeight')">
                <n-space align="center" :size="8">
                  <n-input-number
                    v-model:value="form.meowHtmlHeight"
                    :min="50"
                    :max="2000"
                    size="small"
                    class="notify__num"
                  />
                  <n-text depth="3" class="notify__hint">{{ t('notify.meowHtmlHeightHint') }}</n-text>
                </n-space>
              </n-form-item>
            </template>
            <n-form-item :label="t('notify.meowUrl')">
              <n-input
                v-model:value="form.meowUrl"
                :placeholder="t('notify.meowUrlPlaceholder')"
                class="notify__field"
              />
            </n-form-item>
            <n-form-item :label="t('notify.meowImgUrl')">
              <n-space vertical :size="4" class="notify__field">
                <n-input
                  v-model:value="form.meowImgUrl"
                  :placeholder="t('notify.meowImgUrlPlaceholder')"
                />
                <n-text depth="3" class="notify__hint">{{ t('notify.meowImgUrlHint') }}</n-text>
              </n-space>
            </n-form-item>
            <n-form-item :label="t('notify.meowRedact')">
              <n-space align="center" :size="8">
                <n-switch v-model:value="form.meowRedact" size="small" :aria-label="t('notify.redactHint')" />
                <n-text depth="3" class="notify__hint">{{ t('notify.redactHint') }}</n-text>
              </n-space>
            </n-form-item>
            <!-- 套餐等级：本地限流档位的开关（免费版守 API §5.8 限频；高级/终身版只守日总量） -->
            <n-form-item :label="t('notify.meowPlan')">
              <n-space vertical :size="4" class="notify__field">
                <n-select
                  v-model:value="form.meowPlan"
                  :options="[
                    { label: t('notify.meowPlanFree'), value: 'free' },
                    { label: t('notify.meowPlanPremium'), value: 'premium' },
                    { label: t('notify.meowPlanLifetime'), value: 'lifetime' },
                  ]"
                  class="notify__type"
                />
                <n-text depth="3" class="notify__hint">{{ planDesc }}</n-text>
              </n-space>
            </n-form-item>
            <!-- 使用限制（API §5.8/§5.9）：写在配置区而非只在文档里——
                 限频是会被真实踩到的约束，超限那条消息直接丢（服务端 429）。 -->
            <n-alert type="info" :show-icon="false" class="notify__limits">
              {{ t('notify.meowLimits') }}
            </n-alert>
          </template>

          <template v-else-if="channelType === 'mail'">
            <n-form-item :label="t('notify.mailHost')">
              <n-input
                v-model:value="form.mailHost"
                :placeholder="t('notify.mailHostPlaceholder')"
                class="notify__field"
              />
            </n-form-item>
            <n-form-item :label="t('notify.mailPort')">
              <n-input-number v-model:value="form.mailPort" :min="1" :max="65535" size="small" class="notify__num" />
            </n-form-item>
            <n-form-item :label="t('notify.mailUsername')">
              <n-input
                v-model:value="form.mailUsername"
                :placeholder="t('notify.mailUsernamePlaceholder')"
                class="notify__field"
              />
            </n-form-item>
            <n-form-item :label="t('notify.mailPassword')">
              <n-space vertical :size="4" class="notify__field">
                <n-input
                  v-model:value="form.mailPassword"
                  type="password"
                  show-password-on="click"
                  :placeholder="
                    configQuery.data.value?.mail_password_set
                      ? t('notify.urlConfigured')
                      : t('notify.mailPasswordPlaceholder')
                  "
                  :disabled="clear.mailPassword"
                />
                <n-text v-if="configQuery.data.value?.mail_password_set" depth="3" class="notify__hint">
                  {{ t('notify.urlKeepHint') }}
                  <n-button size="tiny" quaternary type="warning" @click="clear.mailPassword = !clear.mailPassword">
                    {{ clear.mailPassword ? t('notify.urlClearUndo') : t('notify.urlClear') }}
                  </n-button>
                </n-text>
              </n-space>
            </n-form-item>
            <n-form-item :label="t('notify.mailFrom')">
              <n-input
                v-model:value="form.mailFrom"
                :placeholder="t('notify.mailFromPlaceholder')"
                class="notify__field"
              />
            </n-form-item>
            <n-form-item :label="t('notify.mailTo')">
              <n-input
                v-model:value="form.mailTo"
                :placeholder="t('notify.mailToPlaceholder')"
                class="notify__field"
              />
            </n-form-item>
            <n-form-item :label="t('notify.mailSecurity')">
              <n-select
                v-model:value="form.mailSecurity"
                :options="[
                  { label: 'STARTTLS', value: 'starttls' },
                  { label: 'TLS/SSL', value: 'tls' },
                  { label: t('notify.mailSecurityNone'), value: 'none' },
                ]"
                class="notify__num"
              />
            </n-form-item>
            <n-form-item :label="t('notify.mailRedact')">
              <n-space align="center" :size="8">
                <n-switch v-model:value="form.mailRedact" size="small" :aria-label="t('notify.redactHint')" />
                <n-text depth="3" class="notify__hint">{{ t('notify.redactHint') }}</n-text>
              </n-space>
            </n-form-item>
          </template>
        </n-form>

        <!-- P12 加载态：配置未到位时列表整体不渲染——否则首载会闪「四个渠道全部未配置」
             + noChannel 警告（误导）；saveRules/saveChannel 的 patch 是**全量字段**语义，
             加载未完成就保存等于把表单默认值写回后端，故保存按钮同步禁用（仅首载）。 -->
        <n-space :size="8">
          <n-button
            size="small"
            type="primary"
            :loading="savingChannel"
            :disabled="!channelType || configQuery.isLoading.value"
            @click="saveChannel"
          >
            {{ t('common.save') }}
          </n-button>
        </n-space>
      </n-space>
    </n-card>

    <!-- ② 已保存的渠道列表 -->
    <n-card :title="t('notify.list.title')" size="small">
      <n-space vertical :size="12">
        <template v-if="configQuery.data.value">
          <n-alert v-if="!hasAnyChannel" type="warning" :show-icon="false">{{ t('notify.noChannel') }}</n-alert>

          <n-space v-for="key in CHANNEL_KEYS" :key="key" align="center" class="notify__row">
            <StatusTag
              :kind="isConfigured(key, configQuery.data.value) ? 'success' : 'default'"
              :label="isConfigured(key, configQuery.data.value) ? t('notify.list.configured') : t('notify.list.unconfigured')"
            />
            <n-text class="notify__row-name">{{ t(`notify.channel.${key}`) }}</n-text>
            <n-text depth="3" class="notify__row-summary">{{ channelSummary(key) }}</n-text>
            <n-space :size="8">
              <n-button size="tiny" secondary @click="loadChannel(key)">{{ t('notify.list.edit') }}</n-button>
              <n-button
                size="tiny"
                quaternary
                type="warning"
                :disabled="!isConfigured(key, configQuery.data.value)"
                @click="onClearClick(key)"
              >
                {{ t('notify.list.clear') }}
              </n-button>
            </n-space>
          </n-space>
        </template>
        <n-text v-else depth="3">{{ t('common.loading') }}</n-text>

        <n-space :size="8">
          <n-button size="small" secondary :loading="testing" :disabled="!hasAnyChannel" @click="runTest">
            {{ t('notify.test') }}
          </n-button>
        </n-space>

        <!-- E24 逐渠道结果（失败必须带原因，禁止静默） -->
        <n-space v-if="testResult" vertical :size="8">
          <n-space align="center" :size="8">
            <StatusTag
              :kind="testResult.ok ? 'success' : 'error'"
              :label="testResult.ok ? t('notify.testOk') : t('notify.testFailed')"
            />
            <n-text depth="3" class="notify__hint">{{ testResult.latency_ms }} ms</n-text>
          </n-space>
          <template v-for="channel in testResult.channels" :key="channel.channel">
            <n-space align="center" :size="8">
              <StatusTag :kind="channel.ok ? 'success' : 'error'" :label="channel.channel" />
              <n-text v-if="channel.reason" depth="2" class="notify__hint">{{ channel.reason }}</n-text>
            </n-space>
          </template>
          <n-text v-if="testResult.reason" depth="2" class="notify__hint">{{ testResult.reason }}</n-text>
        </n-space>
      </n-space>
    </n-card>

    <!-- ③ 告警规则（全局） -->
    <n-card :title="t('notify.rules.title')" size="small">
      <n-space vertical :size="12">
        <n-text depth="3">{{ t('notify.rules.desc') }}</n-text>
        <n-form label-placement="left" label-width="160" size="small" class="notify__form">
          <n-form-item :label="t('notify.onSuccess')">
            <n-space align="center" :size="8">
              <n-switch v-model:value="onSuccess" size="small" :aria-label="t('notify.onSuccessHint')" />
              <n-text depth="3" class="notify__hint">{{ t('notify.onSuccessHint') }}</n-text>
            </n-space>
          </n-form-item>
          <n-form-item :label="t('notify.skippedThreshold')">
            <n-space align="center" :size="8">
              <n-input-number v-model:value="skippedThreshold" :min="0" size="small" class="notify__num" />
              <n-text depth="3" class="notify__hint">{{ t('notify.skippedThresholdHint') }}</n-text>
            </n-space>
          </n-form-item>
        </n-form>
        <n-button size="small" type="primary" :loading="savingRules" :disabled="configQuery.isLoading.value" @click="saveRules">
          {{ t('common.save') }}
        </n-button>
      </n-space>
    </n-card>

    <!-- 清除渠道确认门（无关键词单次确认；清除不可撤销） -->
    <ConfirmGate
      v-model:show="clearGateShow"
      :title="t('notify.list.clearTitle')"
      :description="clearTarget ? t('notify.list.clearDesc', { name: t(`notify.channel.${clearTarget}`) }) : ''"
      :loading="clearing"
      :confirm-label="t('notify.list.clear')"
      @confirm="confirmClear"
    />
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

.notify__limits {
  margin-top: 4px;
  font-size: 12px;
}

.notify__type {
  max-width: 280px;
}

.notify__row {
  flex-wrap: wrap;
  row-gap: 6px;
}

.notify__row-name {
  min-width: 180px;
  font-weight: 500;
}

.notify__row-summary {
  flex: 1 1 220px;
  min-width: 0;
}
</style>
