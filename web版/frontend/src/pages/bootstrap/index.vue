<script setup lang="ts">
import {
  NAlert,
  NButton,
  NCard,
  NForm,
  NFormItem,
  NInput,
  NSpace,
  NStep,
  NSteps,
  NText,
  useMessage,
} from 'naive-ui'
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { changePassword, fetchBootstrapPassword, fetchMe, login } from '@/api/auth'
import { hasCode, isAppError } from '@/api/http'
import { useUiStore } from '@/stores/ui'

const { t } = useI18n()
const router = useRouter()
const ui = useUiStore()

const message = useMessage()
ui.setMessageApi(message)
onUnmounted(() => ui.setMessageApi(null))

/** 1=查看并保存随机密码 2=设置新密码 3=完成 */
const step = ref(1)
const revealedPassword = ref('')
const shownFallback = ref('')
const submitting = ref(false)
const errorText = ref('')
const autoLoginFailed = ref(false)

const form = reactive({ oldPassword: '', newPassword: '', confirmPassword: '' })

const stepStatus = computed(() => (step.value >= 3 ? 'finish' : 'process'))

function strengthOk(value: string): boolean {
  if (value.length < 10) {
    return false
  }
  const classes = [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/].filter((re) => re.test(value)).length
  return classes >= 2
}

function errorTextOf(error: unknown): string {
  if (!isAppError(error)) {
    return t('errors.UNKNOWN')
  }
  const key = `errors.${error.code}`
  const translated = t(key)
  return translated === key ? error.message : translated
}

onMounted(async () => {
  try {
    revealedPassword.value = (await fetchBootstrapPassword()).password
    form.oldPassword = revealedPassword.value
  } catch (error) {
    if (hasCode(error, 'BOOTSTRAP_SHOWN')) {
      shownFallback.value = errorTextOf(error)
      return
    }
    errorText.value = errorTextOf(error)
  }
})

async function copyPassword(): Promise<void> {
  if (!revealedPassword.value) {
    return
  }
  try {
    await navigator.clipboard.writeText(revealedPassword.value)
    ui.notify('success', t('common.copied'))
  } catch {
    // 剪贴板不可用时静默：密码本身仍可见，可手动复制
  }
}

function goChangePassword(): void {
  if (!form.oldPassword) {
    form.oldPassword = revealedPassword.value
  }
  step.value = 2
}

async function submitChange(): Promise<void> {
  errorText.value = ''
  if (form.newPassword !== form.confirmPassword) {
    errorText.value = t('bootstrap.passwordMismatch')
    return
  }
  if (!strengthOk(form.newPassword)) {
    errorText.value = t('bootstrap.passwordRule')
    return
  }
  submitting.value = true
  try {
    await changePassword(form.oldPassword, form.newPassword)
    step.value = 3
    await autoLogin(form.newPassword)
  } catch (error) {
    errorText.value = errorTextOf(error)
  } finally {
    submitting.value = false
  }
}

/** 改密后用新密码直接登录并进入仪表盘（首启场景后端不自动下发会话）。 */
async function autoLogin(newPassword: string): Promise<void> {
  try {
    await login(newPassword)
    ui.setMe(await fetchMe())
    await router.replace('/')
  } catch {
    autoLoginFailed.value = true
  }
}
</script>

<template>
  <div class="bootstrap">
    <n-card class="bootstrap__card" :title="t('bootstrap.title')" size="large">
      <n-steps :current="step" :status="stepStatus" size="small">
        <n-step :title="t('bootstrap.step1Title')" :description="t('bootstrap.step1Desc')" />
        <n-step :title="t('bootstrap.step2Title')" :description="t('bootstrap.step2Desc')" />
        <n-step :title="t('bootstrap.step3Title')" :description="t('bootstrap.step3Desc')" />
      </n-steps>

      <n-space vertical :size="12" class="bootstrap__body">
        <!-- 步骤 1：一次性随机密码 -->
        <template v-if="step === 1">
          <n-alert v-if="shownFallback" type="warning" :title="t('bootstrap.shownFallbackTitle')">
            {{ shownFallback }}
          </n-alert>
          <template v-else-if="revealedPassword">
            <n-alert type="warning" :show-icon="false">{{ t('bootstrap.onceWarning') }}</n-alert>
            <n-input :value="revealedPassword" readonly />
            <n-space>
              <n-button size="small" secondary @click="copyPassword">
                {{ t('bootstrap.copyPassword') }}
              </n-button>
              <n-button size="small" type="primary" @click="goChangePassword">
                {{ t('bootstrap.step1Next') }}
              </n-button>
            </n-space>
          </template>
        </template>

        <!-- 步骤 2：强制改密 -->
        <n-form v-else-if="step === 2" @submit.prevent="submitChange">
          <n-alert v-if="errorText" type="error" :show-icon="false">{{ errorText }}</n-alert>
          <n-form-item :label="t('bootstrap.oldPassword')">
            <n-input v-model:value="form.oldPassword" type="password" show-password-on="click" />
          </n-form-item>
          <n-form-item :label="t('bootstrap.newPassword')">
            <n-input v-model:value="form.newPassword" type="password" show-password-on="click" />
          </n-form-item>
          <n-form-item :label="t('bootstrap.confirmPassword')">
            <n-input v-model:value="form.confirmPassword" type="password" show-password-on="click" />
          </n-form-item>
          <n-text depth="3">{{ t('bootstrap.passwordRule') }}</n-text>
          <n-button
            type="primary"
            block
            attr-type="submit"
            class="bootstrap__submit"
            :loading="submitting"
          >
            {{ submitting ? t('bootstrap.submitting') : t('bootstrap.submit') }}
          </n-button>
        </n-form>

        <!-- 步骤 3：完成 -->
        <template v-else>
          <n-alert v-if="autoLoginFailed" type="warning" :show-icon="false">
            {{ t('bootstrap.step3Manual') }}
          </n-alert>
          <router-link v-if="autoLoginFailed" :to="{ name: 'login' }">
            <n-button type="primary">{{ t('bootstrap.goLogin') }}</n-button>
          </router-link>
          <n-text v-else depth="3">{{ t('bootstrap.step3Desc') }}</n-text>
        </template>
      </n-space>
    </n-card>
  </div>
</template>

<style scoped>
.bootstrap {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  padding: 24px;
}

.bootstrap__card {
  width: 100%;
  max-width: 640px;
}

.bootstrap__body {
  margin-top: 20px;
}

.bootstrap__submit {
  margin-top: 12px;
}
</style>
