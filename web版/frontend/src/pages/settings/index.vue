<script setup lang="ts">
/** 系统设置页（页面 11，增量设计 §4）。
 *
 * 分区：DB dump（批次 B 实装：dump.* 八键，密码三态沿 notify.* 先例）
 * → 行为开关（批次 B 实装：behavior.* 五键）→ 时区（既有键只读展示）→ 管理员改密（E28）。
 * dump/behavior 提交走 E07 patch 通道（api/settings.saveDumpBehaviorConfig）。
 * 缓存维护（E25）已于 2026-10-19 并入仓库页维护区，本页不再保留入口。
 */

import { useQuery } from '@tanstack/vue-query'
import {
  NAlert,
  NButton,
  NCard,
  NCheckbox,
  NDescriptions,
  NDescriptionsItem,
  NDynamicTags,
  NEl,
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
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { changePassword } from '@/api/auth'
import { getConfig } from '@/api/config'
import { exportConfig, importConfig } from '@/api/configTransfer'
import type { ConfigExportPack, ConfigImportReport } from '@/api/types'
import { saveDumpBehaviorConfig } from '@/api/settings'
import ConfirmGate from '@/components/ConfirmGate.vue'
import StatusTag from '@/components/StatusTag.vue'
// G4：导出口令强度条（纯函数估算 + 强随机生成，见 utils/passwordStrength.ts）
import { estimatePasswordStrength, generateStrongPassword } from '@/utils/passwordStrength'

const { t } = useI18n()
const message = useMessage()

type DumpType = 'pg' | 'mysql' | 'sqlite'
const DUMP_TYPES: DumpType[] = ['pg', 'mysql', 'sqlite']

// ---------------------------------------------------------------- 配置读取（E06：dump/behavior/时区共用一份）

const configQuery = useQuery({
  queryKey: ['config', 'settings-page'],
  queryFn: getConfig,
})

/** 按 key 取配置项；E06 敏感键只回 has_value + source（S3 UI 面）。 */
function configItem(key: string) {
  return configQuery.data.value?.items.find((item) => item.key === key) ?? null
}

// ---------------------------------------------------------------- DB dump（批次 B：dump.* 八键）

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
const savingDump = ref(false)

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

async function onSaveDump(): Promise<void> {
  if (!dumpFormValid.value || savingDump.value) {
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
  savingDump.value = true
  try {
    await saveDumpBehaviorConfig(patch)
    message.success(t('settings.dump.saved'))
    dumpPasswordInput.value = ''
    dumpPasswordClear.value = false
    await configQuery.refetch()
  } catch {
    // 错误（422 校验 / ENV_OVERRIDDEN 等）由 http 层全局提示
  } finally {
    savingDump.value = false
  }
}

// ---------------------------------------------------------------- 行为开关（批次 B：behavior.* 五键）

const behaviorAutoInit = ref(false)
const behaviorAutoUnlock = ref(false)
const behaviorCheckEnabled = ref(false)
const behaviorCheckSampleRatio = ref<number>(0)
const behaviorMonthlyFullDay = ref<number>(1)
const savingBehavior = ref(false)

watch(configQuery.data, (data) => {
  if (!data) {
    return
  }
  const item = (key: string) => data.items.find((entry) => entry.key === key) ?? null
  behaviorAutoInit.value = item('behavior.auto_init')?.value === '1'
  behaviorAutoUnlock.value = item('behavior.auto_unlock')?.value === '1'
  behaviorCheckEnabled.value = item('behavior.check_enabled')?.value === '1'
  const ratio = Number(item('behavior.check_sample_ratio')?.value)
  behaviorCheckSampleRatio.value = Number.isFinite(ratio) ? ratio : 0
  const day = Number(item('behavior.monthly_full_day')?.value)
  behaviorMonthlyFullDay.value = Number.isInteger(day) && day >= 1 && day <= 28 ? day : 1
}, { immediate: true })

const behaviorRatioValid = computed(
  () => Number.isFinite(behaviorCheckSampleRatio.value) && behaviorCheckSampleRatio.value >= 0 && behaviorCheckSampleRatio.value <= 1,
)
const behaviorDayValid = computed(
  () => Number.isInteger(behaviorMonthlyFullDay.value) && behaviorMonthlyFullDay.value >= 1 && behaviorMonthlyFullDay.value <= 28,
)
const behaviorFormValid = computed(() => behaviorRatioValid.value && behaviorDayValid.value)

async function onSaveBehavior(): Promise<void> {
  if (!behaviorFormValid.value || savingBehavior.value) {
    return
  }
  savingBehavior.value = true
  try {
    await saveDumpBehaviorConfig({
      'behavior.auto_init': behaviorAutoInit.value ? '1' : '0',
      'behavior.auto_unlock': behaviorAutoUnlock.value ? '1' : '0',
      'behavior.check_enabled': behaviorCheckEnabled.value ? '1' : '0',
      'behavior.check_sample_ratio': String(behaviorCheckSampleRatio.value),
      'behavior.monthly_full_day': String(behaviorMonthlyFullDay.value),
    })
    message.success(t('settings.behavior.saved'))
    await configQuery.refetch()
  } catch {
    // 错误（422 键级校验等）由 http 层全局提示
  } finally {
    savingBehavior.value = false
  }
}

// ---------------------------------------------------------------- 凭据加密（R-025 / P3-A）

/** 缺省开（与后端 owner 2026-10-16 裁决一致）：键缺失 = 开；仅显式 '0' 为关。 */
const credEncryption = ref(true)
/** 密码文件就位与否（E06 passfile_status）：缺失时开关置灰，启用被后端 422 拒绝。 */
const credEncryptionAvailable = ref(true)
const savingCredEncryption = ref(false)

watch(configQuery.data, (data) => {
  if (!data) {
    return
  }
  const item = (key: string) => data.items.find((entry) => entry.key === key) ?? null
  credEncryption.value = item('security.cred_encryption')?.value !== '0'
  credEncryptionAvailable.value = data.passfile_status === 'configured'
}, { immediate: true })

/** 即时保存（单开关不设保存按钮）：失败回滚开关，错误文案由 http 层全局提示。 */
async function onToggleCredEncryption(value: boolean): Promise<void> {
  if (savingCredEncryption.value) {
    return
  }
  savingCredEncryption.value = true
  try {
    await saveDumpBehaviorConfig({ 'security.cred_encryption': value ? '1' : '0' })
    message.success(t('settings.credential.saved'))
    await configQuery.refetch()
  } catch {
    credEncryption.value = !value
  } finally {
    savingCredEncryption.value = false
  }
}

// ---------------------------------------------------------------- Prometheus 指标（R-024 / P3-B）

/** 令牌三态（同 dump.password 先例）：读取回 *_set 布尔；清除提交空串、输入新值提交原文。
 * 后端只存 SHA-256 硬哈希——设置后不可读，遗失需重新生成。 */
const metricsTokenSet = ref(false)
const metricsTokenInput = ref('')
const metricsTokenClear = ref(false)
const savingMetrics = ref(false)

watch(configQuery.data, (data) => {
  if (!data) {
    return
  }
  const item = (key: string) => data.items.find((entry) => entry.key === key) ?? null
  metricsTokenSet.value = item('metrics.token')?.has_value ?? false
}, { immediate: true })

const metricsTokenDirty = computed(() => metricsTokenClear.value || metricsTokenInput.value !== '')

async function onSaveMetricsToken(): Promise<void> {
  if (savingMetrics.value || !metricsTokenDirty.value) {
    return
  }
  const patch: Record<string, string | null> = {}
  if (metricsTokenClear.value) {
    patch['metrics.token'] = ''
  } else if (metricsTokenInput.value !== '') {
    patch['metrics.token'] = metricsTokenInput.value
  }
  savingMetrics.value = true
  try {
    await saveDumpBehaviorConfig(patch)
    message.success(t('settings.metrics.saved'))
    metricsTokenInput.value = ''
    metricsTokenClear.value = false
    await configQuery.refetch()
  } catch {
    // 错误（422 校验等）由 http 层全局提示
  } finally {
    savingMetrics.value = false
  }
}

// ---------------------------------------------------------------- 时区（既有键只读展示，本批无编辑入口）

const timezoneItem = computed(() => configItem('timezone'))

// ---------------------------------------------------------------- 管理员改密（E28）

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

// ---------------------------------------------------------------- 批次 E 配置迁移
const exportPassword = ref('')
const exporting = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)
const pickedPack = ref<ConfigExportPack | null>(null)
const pickedName = ref('')
const importPassword = ref('')
const replace_existing = ref(false)
const importing = ref(false)
const gateShowImport = ref(false)
const report = ref<ConfigImportReport | null>(null)

// ---------------------------------------------------------------- G4 导出口令强度条
/** 当前口令强度（0 = 未达 10 位门槛；1–4 = 弱→强）。用于四段式强度条与文案分级。 */
const exportStrength = computed(() => estimatePasswordStrength(exportPassword.value))

/** 强度条配色：红（弱）→ 橙 → 绿（强），沿用主题语义色基准值（暗色档同色系）。 */
const strengthColors = ['#B91C1C', '#DC2626', '#B45309', '#15803D', '#15803D']

function strengthColor(index: number): string {
  const score = exportStrength.value.score
  return index < score ? strengthColors[score] : 'rgba(128, 128, 140, 0.24)'
}

const strengthLabelKey = computed(() => {
  const score = exportStrength.value.score
  if (exportPassword.value.length === 0) {
    return 'settings.transfer.strengthEmpty'
  }
  if (score === 0) {
    return 'settings.transfer.strengthTooShort'
  }
  return `settings.transfer.strength${score}`
})

/** 生成强随机口令（crypto 随机源，20 位必然落在「强」档）；生成后可直接导出。 */
function onGenerateExportPassword(): void {
  exportPassword.value = generateStrongPassword()
}

// ---------------------------------------------------------------- 导出动作

/** 导出：拿包对象 → 本地 Blob 下载（文件名带日期，凭据在包内为加密形态）。 */
async function onExport(): Promise<void> {
  if (exportPassword.value.length < 10) {
    return
  }
  exporting.value = true
  try {
    const pack = await exportConfig({ password: exportPassword.value })
    const blob = new Blob([JSON.stringify(pack, null, 1)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `restic-web-config-${(pack.created_at || '').slice(0, 10)}.json`
    anchor.click()
    URL.revokeObjectURL(url)
    message.success(t('settings.transfer.exportOk'))
    exportPassword.value = ''
  } catch {
    // PASSWORD_TOO_SHORT 等由 http 层全局提示
  } finally {
    exporting.value = false
  }
}

/** 选文件：读文本 → JSON 解析（非本系统导出文件立即报错，不发请求）。 */
function onPickFile(event: Event): void {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) {
    return
  }
  void file.text().then((text) => {
    try {
      const parsed = JSON.parse(text) as ConfigExportPack
      if (parsed?.format !== 'restic-web-config-export') {
        message.error(t('settings.transfer.badFile'))
        return
      }
      pickedPack.value = parsed
      pickedName.value = file.name
      report.value = null
    } catch {
      message.error(t('settings.transfer.badFile'))
    }
  })
}

/** 导入：口令随请求体提交；错口令 401 由 http 层提示；报告逐项展示。 */
async function doImport(): Promise<void> {
  if (!pickedPack.value) {
    return
  }
  gateShowImport.value = false
  importing.value = true
  try {
    report.value = await importConfig({
      pack: pickedPack.value,
      password: importPassword.value,
      replace_existing: replace_existing.value,
    })
    message.success(t('settings.transfer.done'))
    importPassword.value = ''
    replace_existing.value = false
  } catch {
    // PASSWORD_WRONG（错口令）/ VALIDATION_ERROR 由 http 层全局提示
  } finally {
    importing.value = false
  }
}

// ---------------------------------------------------------------- 锚点目录（P4：长页 8 区块导航）

/** 区块 id 与卡片标题键一一对应（复用既有 title 键 → 语言切换自动跟随，零新增目录文案）。
 *  缓存维护（E25）已并入仓库页维护区，不再作为本页区块。 */
const SECTIONS = [
  { id: 'dump', title: 'settings.dump.title' },
  { id: 'behavior', title: 'settings.behavior.title' },
  { id: 'credential', title: 'settings.credential.title' },
  { id: 'metrics', title: 'settings.metrics.title' },
  { id: 'timezone', title: 'settings.timezone.title' },
  { id: 'password', title: 'settings.password.title' },
  { id: 'transfer', title: 'settings.transfer.title' },
] as const

type SectionId = (typeof SECTIONS)[number]['id']

/** 目录项：文案经 computed 派生，保证切换语言后即时刷新。 */
const sectionLinks = computed(() =>
  SECTIONS.map((item) => ({ id: item.id, anchor: `sec-${item.id}`, label: t(item.title) })),
)

const activeSection = ref<SectionId>('dump')

/** 点击后短暂锁定高亮，覆盖平滑滚动全程：
 *  否则滚动途中的中间态会把高亮刷成途经区块，出现「点了 A 却高亮 B」。
 *  锁定时长按滚动距离估算（审查 💭③）：固定 1.2s 在超长页（如 dump→改密，滚动 >1.2s）会被
 *  scroll 处理器提前接管；smooth scroll 速度约 2500–3500px/s，取 0.4ms/px + 300ms 底数，
 *  夹在 [1.2s, 2s]——短距离维持原 1.2s 行为，超长页给足全程。 */
let pinnedUntil = 0

/** 跳转并立即高亮。 */
function scrollToSection(id: SectionId): void {
  activeSection.value = id
  const el = document.getElementById(`sec-${id}`)
  if (!el) {
    return
  }
  const distance = Math.abs(el.getBoundingClientRect().top)
  pinnedUntil = Date.now() + Math.min(2000, Math.max(1200, 300 + distance * 0.4))
  el.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

/** 滚动跟随高亮：scroll 事件不冒泡，须在 window 捕获阶段（capture:true）接收内层滚动容器的滚动。
 *
 * 判定线按视口高度自适应（0.45×视口，夹在 140–520px）：固定小判定线（如 120px）在
 * 「页面滚到底、末尾区块无法再推到顶部」时失效——末尾两三个区块永远越不过线，高亮会
 * 卡在中间某个区块（实测点末尾区块后高亮仍停在中途区块）。 */
let rafPending = false
function onScrollCapture(): void {
  if (rafPending) {
    return
  }
  rafPending = true
  requestAnimationFrame(() => {
    rafPending = false
    if (Date.now() < pinnedUntil) {
      return
    }
    const line = Math.min(Math.max(140, window.innerHeight * 0.45), 520)
    let current: SectionId = SECTIONS[0].id
    for (const item of SECTIONS) {
      const el = document.getElementById(`sec-${item.id}`)
      if (el && el.getBoundingClientRect().top <= line) {
        current = item.id
      }
    }
    activeSection.value = current
  })
}

onMounted(() => window.addEventListener('scroll', onScrollCapture, true))
onUnmounted(() => window.removeEventListener('scroll', onScrollCapture, true))
</script>

<template>
  <n-space vertical :size="16">
    <!-- P4：锚点目录（7 区块长页导航；吸顶常驻，窄屏横向滚动）。
         用 n-el 承载：naive 把 bodyColor 暴露为 --n-color，只在自渲染元素上定义；
         普通 div 取不到该变量，暗色下背景会回落成白条。 -->
    <n-el class="settings__anchor">
      <n-space :size="6" :wrap="false" class="settings__anchor-inner">
        <n-button
          v-for="item in sectionLinks"
          :key="item.id"
          size="tiny"
          :type="activeSection === item.id ? 'primary' : 'default'"
          :secondary="activeSection !== item.id"
          @click="scrollToSection(item.id)"
        >
          {{ item.label }}
        </n-button>
      </n-space>
    </n-el>

    <!-- DB dump（批次 B 实装：dump.* 八键 + 密码三态） -->
    <n-card id="sec-dump" :title="t('settings.dump.title')" size="small">
      <n-space vertical :size="10">
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
            <n-space vertical :size="6" class="settings__full">
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
              <n-input
                v-model:value="dumpPasswordInput"
                type="password"
                show-password-on="click"
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
            <n-button type="primary" :loading="savingDump" :disabled="!dumpFormValid" @click="onSaveDump">
              {{ t('common.save') }}
            </n-button>
          </div>
        </n-form>
      </n-space>
    </n-card>

    <!-- 行为开关（批次 B 实装：behavior.* 五键） -->
    <n-card id="sec-behavior" :title="t('settings.behavior.title')" size="small">
      <n-space vertical :size="12">
        <n-text depth="3">{{ t('settings.behavior.desc') }}</n-text>
        <n-form label-placement="top" class="settings__form">
          <n-space vertical :size="12">
            <n-space vertical :size="2">
              <n-space align="center" :size="8">
                <n-switch v-model:value="behaviorAutoInit" size="small" :aria-label="t('settings.behavior.autoInit')" />
                <n-text>{{ t('settings.behavior.autoInit') }}</n-text>
              </n-space>
              <n-text depth="3" class="settings__hint">{{ t('settings.behavior.autoInitHint') }}</n-text>
            </n-space>
            <n-space vertical :size="2">
              <n-space align="center" :size="8">
                <n-switch v-model:value="behaviorAutoUnlock" size="small" :aria-label="t('settings.behavior.autoUnlock')" />
                <n-text>{{ t('settings.behavior.autoUnlock') }}</n-text>
              </n-space>
              <n-text depth="3" class="settings__hint">{{ t('settings.behavior.autoUnlockHint') }}</n-text>
            </n-space>
            <n-space vertical :size="2">
              <n-space align="center" :size="8">
                <n-switch v-model:value="behaviorCheckEnabled" size="small" :aria-label="t('settings.behavior.checkEnabled')" />
                <n-text>{{ t('settings.behavior.checkEnabled') }}</n-text>
              </n-space>
              <n-text depth="3" class="settings__hint">{{ t('settings.behavior.checkEnabledHint') }}</n-text>
            </n-space>
            <n-form-item
              :label="t('settings.behavior.checkSampleRatio')"
              :validation-status="behaviorRatioValid ? undefined : 'error'"
              :feedback="behaviorRatioValid ? undefined : t('settings.behavior.invalidRatio')"
              class="settings__ratio-item"
            >
              <n-space align="center" :size="10">
                <n-input-number
                  v-model:value="behaviorCheckSampleRatio"
                  :min="0"
                  :max="1"
                  :step="0.05"
                  :show-button="false"
                  class="settings__number"
                />
                <n-text depth="3">{{ t('settings.behavior.checkSampleRatioHint') }}</n-text>
              </n-space>
            </n-form-item>
            <n-form-item
              :label="t('settings.behavior.monthlyFullDay')"
              :validation-status="behaviorDayValid ? undefined : 'error'"
              :feedback="behaviorDayValid ? undefined : t('settings.behavior.invalidDay')"
              class="settings__ratio-item"
            >
              <n-space align="center" :size="10">
                <n-input-number
                  v-model:value="behaviorMonthlyFullDay"
                  :min="1"
                  :max="28"
                  :show-button="false"
                  class="settings__number"
                />
                <n-text depth="3">{{ t('settings.behavior.monthlyFullDayHint') }}</n-text>
              </n-space>
            </n-form-item>
          </n-space>
          <n-space justify="end">
            <n-button type="primary" :loading="savingBehavior" :disabled="!behaviorFormValid" @click="onSaveBehavior">
              {{ t('common.save') }}
            </n-button>
          </n-space>
        </n-form>
      </n-space>
    </n-card>

    <!-- 凭据加密（R-025 / P3-A：密钥源自仓库密码文件，默认开） -->
    <n-card id="sec-credential" :title="t('settings.credential.title')" size="small">
      <n-space vertical :size="10">
        <n-text depth="3">{{ t('settings.credential.desc') }}</n-text>
        <n-alert v-if="!credEncryptionAvailable" type="warning" :show-icon="false">
          {{ t('settings.credential.unavailable') }}
        </n-alert>
        <n-space align="center" :size="8">
          <n-switch
            :value="credEncryption"
            :aria-label="t('settings.credential.enabled')"
            size="small"
            :loading="savingCredEncryption"
            :disabled="!credEncryptionAvailable"
            @update:value="onToggleCredEncryption"
          />
          <n-text>{{ t('settings.credential.enabled') }}</n-text>
        </n-space>
        <n-text depth="3" class="settings__hint">{{ t('settings.credential.hint') }}</n-text>
      </n-space>
    </n-card>

    <!-- Prometheus 指标（R-024 / P3-B：抓取令牌三态 + 端点提示） -->
    <n-card id="sec-metrics" :title="t('settings.metrics.title')" size="small">
      <n-space vertical :size="10">
        <n-text depth="3">{{ t('settings.metrics.desc') }}</n-text>
        <n-form label-placement="top" class="settings__form">
          <n-space align="center" :size="8">
            <n-text>{{ t('settings.metrics.token') }}</n-text>
            <!-- P3c 收口：抓取令牌三态（原 success / 无 type 两形态统一） -->
            <StatusTag v-if="metricsTokenSet" kind="success" :label="t('settings.metrics.tokenSet')" />
            <StatusTag v-else kind="default" :label="t('settings.metrics.tokenUnset')" />
          </n-space>
          <n-form-item :label="t('settings.metrics.newToken')">
            <n-input
              v-model:value="metricsTokenInput"
              type="password"
              show-password-on="click"
              :placeholder="t('settings.metrics.newTokenPlaceholder')"
            />
          </n-form-item>
          <n-space align="center" :size="8">
            <n-checkbox v-model:checked="metricsTokenClear" :disabled="!metricsTokenSet">
              {{ t('settings.metrics.clear') }}
            </n-checkbox>
            <n-button
              size="small"
              type="primary"
              :loading="savingMetrics"
              :disabled="!metricsTokenDirty"
              @click="onSaveMetricsToken"
            >
              {{ t('settings.metrics.save') }}
            </n-button>
          </n-space>
        </n-form>
        <n-text depth="3" class="settings__hint">{{ t('settings.metrics.hint') }}</n-text>
      </n-space>
    </n-card>

    <!-- 时区（既有键只读展示） -->
    <n-card id="sec-timezone" :title="t('settings.timezone.title')" size="small">
      <n-space vertical :size="8">
        <n-space align="center" :size="8">
          <n-text depth="2">{{ t('settings.timezone.current') }}</n-text>
          <!-- P3c 收口：当前时区值（原无 type）+ env 来源提示（warning） -->
          <StatusTag v-if="timezoneItem" kind="default" :label="timezoneItem.value ?? t('settings.timezone.unset')" />
          <StatusTag v-if="timezoneItem?.source === 'env'" kind="warning" :label="t('settings.timezone.envNote')" />
          <n-text v-if="!timezoneItem" depth="3">{{ t('settings.timezone.unset') }}</n-text>
        </n-space>
        <n-text depth="3">{{ t('settings.timezone.note') }}</n-text>
      </n-space>
    </n-card>

    <!-- 管理员改密（E28） -->
    <n-card id="sec-password" :title="t('settings.password.title')" size="small">
      <n-form label-placement="top" class="settings__password-form">
        <n-form-item :label="t('settings.password.oldPassword')">
          <n-input
            v-model:value="oldPassword"
            type="password"
            show-password-on="click"
            :placeholder="t('settings.password.oldPassword')"
          />
        </n-form-item>
        <n-form-item :label="t('settings.password.newPassword')">
          <n-input
            v-model:value="newPassword"
            type="password"
            show-password-on="click"
            :placeholder="t('settings.password.newPassword')"
          />
        </n-form-item>
        <n-form-item
          :label="t('settings.password.confirmPassword')"
          :validation-status="passwordMismatch ? 'error' : undefined"
          :feedback="passwordMismatch ? t('settings.password.mismatch') : undefined"
        >
          <n-input
            v-model:value="confirmPassword"
            type="password"
            show-password-on="click"
            :placeholder="t('settings.password.confirmPassword')"
          />
        </n-form-item>
        <n-text depth="3">{{ t('settings.password.passwordRule') }}</n-text>
        <n-space justify="end">
          <n-button type="primary" :loading="changing" :disabled="!canSubmitPassword" @click="onChangePassword">
            {{ t('settings.password.submit') }}
          </n-button>
        </n-space>
      </n-form>
    </n-card>

    <!-- 批次 E 配置迁移：导出（口令加密凭据段）+ 导入（跳过/覆盖 + 报告） -->
    <n-card id="sec-transfer" :title="t('settings.transfer.title')" size="small">
      <n-space vertical :size="12">
        <n-text depth="3">{{ t('settings.transfer.desc') }}</n-text>
        <n-space align="center" :size="8" wrap>
          <n-input
            v-model:value="exportPassword"
            type="password"
            show-password-on="click"
            :placeholder="t('settings.transfer.exportPassword')"
            class="settings__transfer-pw"
          />
          <!-- G4：生成强随机口令（对齐 Duplicati export.component.html 的口令区） -->
          <n-button size="small" secondary @click="onGenerateExportPassword">
            {{ t('settings.transfer.generatePassword') }}
          </n-button>
          <n-button
            size="small"
            type="primary"
            secondary
            :loading="exporting"
            :disabled="exportPassword.length < 10"
            @click="onExport"
          >
            {{ t('settings.transfer.exportBtn') }}
          </n-button>
        </n-space>

        <!-- G4 强度条：四段式 + 文案分级；aria-live 让改口令时的等级变化被读出 -->
        <n-space :size="8" align="center" class="settings__strength">
          <div class="settings__strength-bar" role="presentation">
            <span
              v-for="index in 4"
              :key="index"
              class="settings__strength-seg"
              :style="{ background: strengthColor(index - 1) }"
            />
          </div>
          <n-text depth="3" aria-live="polite">{{ t(strengthLabelKey) }}</n-text>
        </n-space>
        <n-space align="center" :size="8" wrap>
          <input
            ref="fileInput"
            type="file"
            accept="application/json,.json"
            class="settings__file-input"
            @change="onPickFile"
          />
          <n-button size="small" secondary @click="fileInput?.click()">
            {{ t('settings.transfer.pickFile') }}
          </n-button>
          <n-text v-if="pickedName" depth="2">{{ pickedName }}</n-text>
          <n-input
            v-model:value="importPassword"
            type="password"
            show-password-on="click"
            :placeholder="t('settings.transfer.importPassword')"
            class="settings__transfer-pw"
            :disabled="!pickedPack"
          />
          <n-checkbox v-model:checked="replace_existing" :disabled="!pickedPack">
            {{ t('settings.transfer.replace_existing') }}
          </n-checkbox>
          <n-button
            size="small"
            type="warning"
            secondary
            :loading="importing"
            :disabled="!pickedPack || importPassword.length < 1"
            @click="replace_existing ? (gateShowImport = true) : doImport()"
          >
            {{ t('settings.transfer.importBtn') }}
          </n-button>
        </n-space>

        <!-- 导入报告（逐项展示；跳过/警告全量可见，绝不静默） -->
        <template v-if="report">
          <n-alert type="success" :show-icon="false">{{ t('settings.transfer.done') }}</n-alert>
          <n-descriptions :column="2" size="small" bordered>
            <n-descriptions-item :label="t('settings.transfer.reposCreated')">
              {{ report.repos.created.join(', ') || '—' }}
            </n-descriptions-item>
            <n-descriptions-item :label="t('settings.transfer.reposReused')">
              {{ report.repos.reused.join(', ') || '—' }}
            </n-descriptions-item>
            <n-descriptions-item :label="t('settings.transfer.reposUpdated')">
              {{ report.repos.updated.join(', ') || '—' }}
            </n-descriptions-item>
            <n-descriptions-item :label="t('settings.transfer.reposSkipped')">
              {{ report.repos.skipped.map((x) => `${x.id}（${x.reason}）`).join('；') || '—' }}
            </n-descriptions-item>
            <n-descriptions-item :label="t('settings.transfer.tasksCreated')">
              {{ report.tasks.created.join(', ') || '—' }}
            </n-descriptions-item>
            <n-descriptions-item :label="t('settings.transfer.tasksUpdated')">
              {{ report.tasks.updated.join(', ') || '—' }}
            </n-descriptions-item>
            <n-descriptions-item :label="t('settings.transfer.tasksSkipped')" :span="2">
              {{ report.tasks.skipped.map((x) => `${x.name}（${x.reason}）`).join('；') || '—' }}
            </n-descriptions-item>
            <n-descriptions-item :label="t('settings.transfer.creds')">
              {{ report.creds_imported }}
            </n-descriptions-item>
            <n-descriptions-item :label="t('settings.transfer.notify')">
              {{ report.notify_updated }}
            </n-descriptions-item>
          </n-descriptions>
          <n-alert
            v-for="(w, i) in report.mirror_warnings"
            :key="i"
            type="warning"
            :show-icon="false"
          >
            {{ w.mirror_id }}：{{ w.reason }}
          </n-alert>
        </template>
      </n-space>
    </n-card>

    <!-- 覆盖导入确认门：预览 → 输入 IMPORT → 二次确认（覆盖不可逆） -->
    <ConfirmGate
      v-model:show="gateShowImport"
      :title="t('settings.transfer.gateTitle')"
      :keyword="'IMPORT'"
      :description="t('settings.transfer.gateDesc')"
      :loading="importing"
      @confirm="doImport"
    />
  </n-space>
</template>

<style scoped>
/* P4：锚点目录吸顶条——背景跟随主题；窄屏横向滚动（不换行，避免占满两行挤压内容）。
   背景必须用 --n-color：naive 把 common.bodyColor 暴露为 --n-color（外层由 n-el 提供），
   --n-body-color / --n-card-color 并不存在——写成它们会静默回落 #fff，暗色下留一条白带（实测）。 */
.settings__anchor {
  position: sticky;
  top: 0;
  z-index: 3;
  padding: 8px 0;
  margin: -8px 0 0;
  background: var(--n-color, #fff);
}

.settings__anchor-inner {
  flex-wrap: nowrap;
  overflow-x: auto;
  padding-bottom: 2px;
}

/* P4：dump 吸底保存条——表单最长（8 键 + 密码三态），保存动作常驻视口底部。
   背景继承所在 NCard 的 --n-color（= cardColor），故此处不能包 n-el（会把变量重置成 bodyColor）。 */
.settings__savebar {
  position: sticky;
  bottom: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  padding: 10px 0 2px;
  background: var(--n-color, #fff);
  border-top: 1px solid var(--n-border-color, rgba(125, 133, 152, 0.28));
}

.settings__form {
  max-width: 560px;
}

.settings__password-form {
  max-width: 420px;
}

.settings__grid {
  flex-wrap: wrap;
}

.settings__cell {
  width: 220px;
}

.settings__cell--narrow {
  width: 120px;
}

.settings__full {
  width: 100%;
}

.settings__dump-type {
  max-width: 220px;
}

.settings__number {
  width: 120px;
}

.settings__ratio-item {
  max-width: 480px;
}

.settings__hint {
  font-size: 12px;
}

.settings__transfer-pw {
  width: 260px;
}

/* G4 强度条：四段等宽，颜色由 JS 按分档给出（语义色沿用主题基准值） */
.settings__strength {
  flex-wrap: wrap;
}

.settings__strength-bar {
  display: flex;
  gap: 4px;
  width: 160px;
}

.settings__strength-seg {
  flex: 1 1 0;
  height: 4px;
  border-radius: 2px;
  transition: background 0.15s;
}

.settings__file-input {
  display: none;
}
</style>
