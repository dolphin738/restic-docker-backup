<script setup lang="ts">
/**
 * 设置 · 凭据加密分区（R-025 / P3-A；候选 6 拆分）。
 *
 * 单开关即时保存（不设保存按钮）：失败回滚开关状态，错误文案由 http 层全局提示。
 */

import { useQuery } from '@tanstack/vue-query'
import { NAlert, NCard, NSpace, NSwitch, NText, useMessage } from 'naive-ui'
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getConfig } from '@/api/config'
import { saveDumpBehaviorConfig } from '@/api/settings'
import { useConfigPatch } from '@/composables/useConfigPatch'

const { t } = useI18n()
const message = useMessage()

const configQuery = useQuery({ queryKey: ['config', 'settings-page'], queryFn: getConfig })

/** 缺省开（owner 2026-10-16 裁决）：键缺失 = 开；仅显式 0 为关。 */
const credEncryption = ref(true)
/** 密码文件就位与否（E06 passfile_status）：缺失时开关置灰，启用会被后端 422 拒绝。 */
const credEncryptionAvailable = ref(true)

watch(configQuery.data, (data) => {
  if (!data) {
    return
  }
  const item = (key: string) => data.items.find((entry) => entry.key === key) ?? null
  credEncryption.value = item('security.cred_encryption')?.value !== '0'
  credEncryptionAvailable.value = data.passfile_status === 'configured'
}, { immediate: true })

const credPatch = useConfigPatch({
  submit: saveDumpBehaviorConfig,
  successMessage: () => t('settings.credential.saved'),
  notify: (text) => message.success(text),
  refetch: () => configQuery.refetch(),
})

/** 即时保存：失败回滚开关（错误文案由 http 层全局提示）。 */
async function onToggleCredEncryption(value: boolean): Promise<void> {
  const ok = await credPatch.save({ 'security.cred_encryption': value ? '1' : '0' })
  if (!ok) {
    credEncryption.value = !value
  }
}

</script>

<template>
    <n-card id="sec-credential" class="settings__panel" :title="t('settings.credential.title')" size="small">
      <n-space vertical :size="12">
        <n-text depth="3">{{ t('settings.credential.desc') }}</n-text>
        <n-alert v-if="!credEncryptionAvailable" type="warning" :show-icon="false">
          {{ t('settings.credential.unavailable') }}
        </n-alert>
        <n-space align="center" :size="8">
          <n-switch
            :value="credEncryption"
            :aria-label="t('settings.credential.enabled')"
            size="small"
            :loading="credPatch.saving.value"
            :disabled="!credEncryptionAvailable"
            @update:value="onToggleCredEncryption"
          />
          <n-text>{{ t('settings.credential.enabled') }}</n-text>
        </n-space>
        <n-text depth="3" class="settings__hint">{{ t('settings.credential.hint') }}</n-text>
      </n-space>
    </n-card>
</template>
