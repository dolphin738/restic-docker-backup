<script setup lang="ts">
/**
 * 设置 · 管理员改密分区（E28；候选 6 拆分）。
 *
 * 非配置卡：走 changePassword，不走 config patch 通道（故不用 useConfigPatch）。
 * 密码规则与首启引导同口径：至少 10 位且两类字符。
 */

import { NButton, NCard, NForm, NFormItem, NIcon, NSpace, NText, useMessage } from 'naive-ui'
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { changePassword } from '@/api/auth'
import { IconLock } from '@/components/icons/icons'
import PasswordInput from '@/components/PasswordInput.vue'

const { t } = useI18n()
const message = useMessage()

const oldPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const changing = ref(false)

/** 与首启引导同规则：至少 10 位且两类字符。 */
const newPasswordValid = computed(() => {
  const value = newPassword.value
  if (value.length < 10) {
    return false
  }
  let kinds = 0
  if (/[a-z]/.test(value)) kinds += 1
  if (/[A-Z]/.test(value)) kinds += 1
  if (/\d/.test(value)) kinds += 1
  if (/[^a-zA-Z\d]/.test(value)) kinds += 1
  return kinds >= 2
})

const passwordMismatch = computed(() => confirmPassword.value !== '' && confirmPassword.value !== newPassword.value)

const canSubmitPassword = computed(
  () => oldPassword.value !== '' && newPasswordValid.value && !passwordMismatch.value && !changing.value,
)

async function onChangePassword(): Promise<void> {
  if (!canSubmitPassword.value) {
    return
  }
  changing.value = true
  try {
    await changePassword(oldPassword.value, newPassword.value)
    message.success(t('settings.password.saved'))
    oldPassword.value = ''
    newPassword.value = ''
    confirmPassword.value = ''
  } catch {
    // 错误文案（PASSWORD_WRONG 等）由 http 层全局提示
  } finally {
    changing.value = false
  }
}

</script>

<template>
    <n-card id="sec-password" class="settings__panel" :title="t('settings.password.title')" size="small">
      <n-form label-placement="top" class="settings__password-form">
        <n-form-item :label="t('settings.password.oldPassword')">
          <PasswordInput
            v-model="oldPassword"
            width="100%"
            :placeholder="t('settings.password.oldPassword')"
          />
        </n-form-item>
        <n-form-item :label="t('settings.password.newPassword')">
          <PasswordInput
            v-model="newPassword"
            width="100%"
            :placeholder="t('settings.password.newPassword')"
          />
        </n-form-item>
        <n-form-item
          :label="t('settings.password.confirmPassword')"
          :validation-status="passwordMismatch ? 'error' : undefined"
          :feedback="passwordMismatch ? t('settings.password.mismatch') : undefined"
        >
          <PasswordInput
            v-model="confirmPassword"
            width="100%"
            :placeholder="t('settings.password.confirmPassword')"
          />
        </n-form-item>
        <n-text depth="3">{{ t('settings.password.passwordRule') }}</n-text>
        <n-space justify="end">
          <n-button type="primary" :loading="changing" :disabled="!canSubmitPassword" @click="onChangePassword">
            <template #icon>
              <n-icon :size="16"><IconLock /></n-icon>
            </template>
            {{ t('settings.password.submit') }}
          </n-button>
        </n-space>
      </n-form>
    </n-card>
</template>

<style scoped>
.settings__password-form { max-width: 420px; }
</style>
