<script setup lang="ts">
/**
 * 设置 · DB dump 分区（候选 6 拆分：原 settings/index.vue 的 dump 段整体搬出）。
 *
 * 自包含：配置读取 / 回填 / 校验 / 保存都在本组件内，父页只负责显示哪个分区。
 * 保存链走 useConfigPatch（拼 patch → 提交 → 提示 → 刷新 → loading）。
 */

import { useQuery } from '@tanstack/vue-query'
import {
  NAlert,
  NButton,
  NCard,
  NDynamicTags,
  NForm,
  NFormItem,
  NIcon,
  NInput,
  NInputNumber,
  NSelect,
  NSpace,
  NSwitch,
  NText,
  useMessage,
} from 'naive-ui'
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getConfig } from '@/api/config'
import { saveDumpBehaviorConfig } from '@/api/settings'
import { IconCheckCircle } from '@/components/icons/icons'
import PasswordInput from '@/components/PasswordInput.vue'
import StatusTag from '@/components/StatusTag.vue'
import { useConfigPatch } from '@/composables/useConfigPatch'

const { t } = useI18n()
const message = useMessage()

type DumpType = 'pg' | 'mysql' | 'sqlite'
const DUMP_TYPES: DumpType[] = ['pg', 'mysql', 'sqlite']

// 配置读取：queryKey 与父页一致 → vue-query 去重，不额外发请求
const configQuery = useQuery({ queryKey: ['config', 'settings-page'], queryFn: getConfig })

const dumpEnabled = ref(false)
const dumpType = ref<DumpType>('pg')
const dumpHost = ref('')
const dumpPort = ref<number | null>(null)
const dumpUser = ref('')
const dumpDatabases = ref<string[]>([])
const dumpRetentionDays = ref<number | null>(null)
/** 密码三态：读取回 *_set 布尔；提交时「清除」提交空串、「输入新值」提交原文、都不动 = 不变。 */
const dumpPasswordSet = ref(false)
const dumpPasswordSource = ref<'ui' | 'env' | null>(null)
const dumpPasswordClear = ref(false)
const dumpPasswordInput = ref('')


watch(configQuery.data, (data) => {
  if (!data) {
    return
  }
  const item = (key: string) => data.items.find((entry) => entry.key === key) ?? null
  dumpEnabled.value = item('dump.enabled')?.value === '1'
  const type = item('dump.type')?.value
  dumpType.value = type === 'mysql' || type === 'sqlite' ? type : 'pg'
  dumpHost.value = item('dump.host')?.value ?? ''
  const port = Number(item('dump.port')?.value)
  dumpPort.value = Number.isInteger(port) && port > 0 ? port : null
  dumpUser.value = item('dump.user')?.value ?? ''
  const databasesRaw = item('dump.databases')?.value
  try {
    const parsed: unknown = databasesRaw ? JSON.parse(databasesRaw) : []
    dumpDatabases.value = Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === 'string') : []
  } catch {
    dumpDatabases.value = []
  }
  const retention = Number(item('dump.retention_days')?.value)
  dumpRetentionDays.value = Number.isInteger(retention) && retention >= 0 ? retention : null
  const password = item('dump.password')
  dumpPasswordSet.value = password?.has_value ?? false
  dumpPasswordSource.value = password?.source ?? null
}, { immediate: true })

const dumpTypeOptions = computed(() =>
  DUMP_TYPES.map((value) => ({ label: t(`settings.dump.type_${value}`), value })),
)

/** pg 版本不匹配提示（PRD `dump.pg_version_mismatch` 口径）：仅 pg 类型下展示。 */
const showPgVersionNote = computed(() => dumpType.value === 'pg')

const dumpFormValid = computed(() => {
  if (dumpPort.value !== null && (!Number.isInteger(dumpPort.value) || dumpPort.value <= 0)) {
    return false
  }
  return dumpRetentionDays.value !== null && Number.isInteger(dumpRetentionDays.value) && dumpRetentionDays.value >= 0
})

const dumpPatch = useConfigPatch({
  submit: saveDumpBehaviorConfig,
  successMessage: () => t('settings.dump.saved'),
  notify: (text) => message.success(text),
  refetch: () => configQuery.refetch(),
  afterSave: () => {
    // 保存成功即清空密码输入框与「清除」开关（原实现同一行为）
    dumpPasswordInput.value = ''
    dumpPasswordClear.value = false
  },
})
async function onSaveDump(): Promise<void> {
  if (!dumpFormValid.value) {
    return
  }
  const patch: Record<string, string | null> = {
    'dump.enabled': dumpEnabled.value ? '1' : '0',
    'dump.type': dumpType.value,
    'dump.host': dumpHost.value.trim() || null,
    'dump.port': dumpPort.value !== null ? String(dumpPort.value) : null,
    'dump.user': dumpUser.value.trim() || null,
    'dump.databases': dumpDatabases.value.length > 0 ? JSON.stringify(dumpDatabases.value) : null,
    'dump.retention_days': dumpRetentionDays.value !== null ? String(dumpRetentionDays.value) : null,
  }
  // 密码三态：undefined = 不变（不进 patch）
  if (dumpPasswordClear.value) {
    patch['dump.password'] = ''
  } else if (dumpPasswordInput.value !== '') {
    patch['dump.password'] = dumpPasswordInput.value
  }
  await dumpPatch.save(patch)
}

</script>

<template>
    <n-card id="sec-dump" class="settings__panel" :title="t('settings.dump.title')" size="small">
      <n-space vertical :size="12">
        <n-text depth="3">{{ t('settings.dump.desc') }}</n-text>
        <n-alert v-if="showPgVersionNote" type="warning" :show-icon="false">
          {{ t('settings.dump.pgVersionNote') }}
        </n-alert>
        <n-form label-placement="top" class="settings__form">
          <n-space align="center" :size="8">
            <n-switch v-model:value="dumpEnabled" size="small" :aria-label="t('settings.dump.enabled')" />
            <n-text>{{ t('settings.dump.enabled') }}</n-text>
          </n-space>
          <n-form-item :label="t('settings.dump.type')">
            <n-select v-model:value="dumpType" :options="dumpTypeOptions" class="settings__dump-type" />
          </n-form-item>
          <n-space :size="12" class="settings__grid">
            <n-form-item :label="t('settings.dump.host')" class="settings__cell">
              <n-input v-model:value="dumpHost" :placeholder="t('settings.dump.hostPlaceholder')" />
            </n-form-item>
            <n-form-item :label="t('settings.dump.port')" class="settings__cell settings__cell--narrow">
              <n-input-number v-model:value="dumpPort" :min="1" :show-button="false" class="settings__number" />
            </n-form-item>
            <n-form-item :label="t('settings.dump.user')" class="settings__cell">
              <n-input v-model:value="dumpUser" />
            </n-form-item>
          </n-space>
          <n-form-item :label="t('settings.dump.password')">
            <n-space vertical :size="8" class="settings__full">
              <n-space align="center" :size="8">
                <!-- P3c 收口：口令已设/未设 + env 来源（色/文案不变，形态统一 pill） -->
                <StatusTag
                  :kind="dumpPasswordSet ? 'success' : 'default'"
                  :label="dumpPasswordSet ? t('settings.dump.passwordSet') : t('settings.dump.passwordUnset')"
                />
                <StatusTag v-if="dumpPasswordSource === 'env'" kind="warning" :label="t('settings.timezone.envNote')" />
                <n-button
                  v-if="dumpPasswordSet"
                  size="tiny"
                  quaternary
                  :type="dumpPasswordClear ? 'primary' : 'warning'"
                  @click="dumpPasswordClear = !dumpPasswordClear"
                >
                  {{ dumpPasswordClear ? t('settings.dump.passwordClearUndo') : t('settings.dump.passwordClear') }}
                </n-button>
              </n-space>
              <PasswordInput
                v-model="dumpPasswordInput"
                width="100%"
                :disabled="dumpPasswordClear"
                :placeholder="t('settings.dump.passwordNewPlaceholder')"
              />
            </n-space>
          </n-form-item>
          <n-form-item :label="t('settings.dump.databases')">
            <n-space vertical :size="4" class="settings__full">
              <n-dynamic-tags v-model:value="dumpDatabases" />
              <n-text depth="3">{{ t('settings.dump.databasesHint') }}</n-text>
            </n-space>
          </n-form-item>
          <n-form-item
            :label="t('settings.dump.retentionDays')"
            :validation-status="dumpFormValid ? undefined : 'error'"
            :feedback="dumpFormValid ? undefined : t('settings.dump.needRetention')"
          >
            <n-input-number
              v-model:value="dumpRetentionDays"
              :min="0"
              :show-button="false"
              class="settings__number"
            />
          </n-form-item>
          <!-- P4：吸底保存条（dump 表单最长，保存动作常驻视口底部；校验不通过时同处给出原因） -->
          <div class="settings__savebar">
            <n-text depth="3" class="settings__hint">
              {{ dumpFormValid ? t('settings.dump.unsavedHint') : t('settings.dump.needRetention') }}
            </n-text>
            <n-button type="primary" :loading="dumpPatch.saving.value" :disabled="!dumpFormValid" @click="onSaveDump">
              <template #icon>
                <n-icon :size="16"><IconCheckCircle /></n-icon>
              </template>
              {{ t('common.save') }}
            </n-button>
          </div>
        </n-form>
      </n-space>
    </n-card>

    <!-- 行为开关（批次 B 实装：behavior.* 五键） -->
</template>
