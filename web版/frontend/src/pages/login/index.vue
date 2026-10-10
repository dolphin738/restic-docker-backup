<script setup lang="ts">
import { useIntervalFn } from '@vueuse/core'
import { NAlert, NButton, NCard, NForm, NFormItem, NInput, NSpace, NText, useMessage } from 'naive-ui'
import { computed, onUnmounted, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { fetchMe, login } from '@/api/auth'
import { getHealth } from '@/api/health'
import { hasCode, isAppError } from '@/api/http'
import { useUiStore } from '@/stores/ui'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const ui = useUiStore()

const message = useMessage()
ui.setMessageApi(message)
onUnmounted(() => ui.setMessageApi(null))

const form = reactive({ password: '' })
const submitting = ref(false)
const errorText = ref('')
const retryAfter = ref(0)
const healthState = ref<'ok' | 'degraded' | 'offline'>('ok')

const locked = computed(() => retryAfter.value > 0)
const redirect = computed(() => (typeof route.query.redirect === 'string' ? route.query.redirect : '/'))

const { pause: stopCountdown, resume: startCountdown } = useIntervalFn(
  () => {
    retryAfter.value = Math.max(0, retryAfter.value - 1)
    if (retryAfter.value === 0) {
      stopCountdown()
    }
  },
  1000,
  { immediate: false },
)

onMounted(async () => {
  try {
    const health = await getHealth()
    healthState.value = health.db === 'ok' ? 'ok' : 'degraded'
  } catch {
    healthState.value = 'offline'
  }
})

/** 错误码 → 词条；缺失时回退后端 message。 */
function errorTextOf(error: unknown): string {
  if (!isAppError(error)) {
    return t('errors.UNKNOWN')
  }
  const key = `errors.${error.code}`
  const translated = t(key)
  return translated === key ? error.message : translated
}

async function onSubmit(): Promise<void> {
  if (!form.password) {
    return
  }
  submitting.value = true
  errorText.value = ''
  retryAfter.value = 0
  stopCountdown()
  try {
    await login(form.password)
    ui.setMe(await fetchMe())
    await router.replace(redirect.value)
  } catch (error) {
    // BOOTSTRAP_REQUIRED 已由 http 层统一跳转到引导页，此处不再重复提示
    if (hasCode(error, 'BOOTSTRAP_REQUIRED')) {
      return
    }
    errorText.value = errorTextOf(error)
    if (isAppError(error) && (error.code === 'ACCOUNT_LOCKED' || error.code === 'RATE_LIMITED')) {
      const seconds = Number(error.data?.['retry_after'] ?? 0)
      retryAfter.value = Number.isFinite(seconds) && seconds > 0 ? Math.ceil(seconds) : 30
      startCountdown()
    }
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="login">
    <n-card class="login__card" :title="t('login.title')" size="large">
      <template #header-extra>
        <n-text depth="3">{{ t('common.appName') }}</n-text>
      </template>

      <n-space vertical :size="12">
        <n-alert v-if="healthState === 'offline'" type="error" :show-icon="false">
          {{ t('login.backendOffline') }}
        </n-alert>
        <n-alert v-else-if="healthState === 'degraded'" type="warning" :show-icon="false">
          {{ t('login.backendDegraded') }}
        </n-alert>

        <n-text depth="3">{{ t('login.subtitle') }}</n-text>

        <n-alert v-if="locked" type="error" :title="t('login.lockedTitle')" :show-icon="false">
          {{ t('login.lockedHint', { n: retryAfter }) }}
        </n-alert>
        <n-alert v-else-if="errorText" type="error" :show-icon="false">
          {{ errorText }}
        </n-alert>

        <n-form @submit.prevent="onSubmit">
          <n-form-item :label="t('login.password')">
            <n-input
              v-model:value="form.password"
              type="password"
              show-password-on="click"
              :placeholder="t('login.passwordPlaceholder')"
              :disabled="locked"
            />
          </n-form-item>
          <n-button
            type="primary"
            block
            attr-type="submit"
            :loading="submitting"
            :disabled="locked || !form.password"
          >
            {{ submitting ? t('login.submitting') : t('login.submit') }}
          </n-button>
        </n-form>

        <router-link :to="{ name: 'bootstrap' }">
          <n-button size="tiny" quaternary>{{ t('login.bootstrapLink') }}</n-button>
        </router-link>
      </n-space>
    </n-card>
  </div>
</template>

<style scoped>
.login {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  padding: 24px;
}

.login__card {
  width: 100%;
  max-width: 420px;
}
</style>
