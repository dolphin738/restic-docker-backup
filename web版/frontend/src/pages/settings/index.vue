<script setup lang="ts">
/** 系统设置页（页面 11，增量设计 §4）。
 *
 * 布局（2026-10-21 改版，替代 P4 锚点长页）：顶部分段 Tab 切换分区 +
 * 单面板渲染当前分区（内容全宽，与全站单栏形态统一；桌面 / 移动同一分段条）。
 * 曾试过「左侧 NMenu 分区导航」，因与主导航并排成双竖导航（层级含混 +
 * 1366px 屏内容区被挤到约 700px）被 owner 否决——勿回退该形态。深链保留：
 * 切换即 `#sec-<id>` replaceState，刷新 / 分享链接仍直达对应分区。
 *
 * → Prometheus 指标（R-024）→ 时区（E39 实装：手动设置 + 服务器
 * 实时时钟 + NTP 时间同步 + 主备 NTP 服务器设置）→ 管理员改密（E28）
 * → 配置迁移（批次 E：导出 / 导入）。
 * dump/behavior/时区提交走 E07 patch 通道（api/settings.saveDumpBehaviorConfig）；
 * 服务器时间与时间同步走 E39（api/system.ts）。
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
  NEl,
  NForm,
  NFormItem,
  NIcon,
  NInput,
  NInputNumber,
  NModal,
  NSelect,
  NSpace,
  NText,
  useMessage,
} from 'naive-ui'
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { changePassword } from '@/api/auth'
import { getConfig } from '@/api/config'
import { exportConfig, importConfig } from '@/api/configTransfer'
import type { ConfigExportPack, ConfigImportReport, TimeSyncResponse } from '@/api/types'
import { saveDumpBehaviorConfig } from '@/api/settings'
import { getSystemTime, syncSystemTime } from '@/api/system'
import { IconCheckCircle, IconClock, IconDownload, IconLock, IconShieldCheck } from '@/components/icons/icons'
import ConfirmGate from '@/components/ConfirmGate.vue'
import PasswordInput from '@/components/PasswordInput.vue'
import SecretReveal from '@/components/SecretReveal.vue'
import StatusTag from '@/components/StatusTag.vue'
// P6 形态层：分段控件从本页私有实现提升为共用组件（S-4 单一实现）；
// 深链（#sec-<id>）与未知 id 防御已内置于组件（hashPrefix='sec'）。
import SegmentedTabs from '@/components/dashboard/SegmentedTabs.vue'
import SectionDump from '@/pages/settings/sections/SectionDump.vue'
import SectionBehavior from '@/pages/settings/sections/SectionBehavior.vue'
import SectionCredential from '@/pages/settings/sections/SectionCredential.vue'
// G4：导出口令强度条（纯函数估算，见 utils/passwordStrength.ts）
// 生成统一走全站唯一生成器 utils/password.ts（32 位，与仓库密码同口径）
import { generatePassword } from '@/utils/password'
import { estimatePasswordStrength } from '@/utils/passwordStrength'
import { formatDateTime, setDisplayTimezone } from '@/utils/datetime'

const { t } = useI18n()
const message = useMessage()


// ---------------------------------------------------------------- 配置读取（E06：dump/behavior/时区共用一份）

const configQuery = useQuery({
  queryKey: ['config', 'settings-page'],
  queryFn: getConfig,
})

/** 按 key 取配置项；E06 敏感键只回 has_value + source（S3 UI 面）。 */
function configItem(key: string) {
  return configQuery.data.value?.items?.find((item) => item.key === key) ?? null
}

// ---------------------------------------------------------------- 凭据加密（R-025 / P3-A）

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

// ---------------------------------------------------------------- 时区（E39：手动设置 + 时间同步）

const timezoneItem = computed(() => configItem('timezone'))
const timezoneEnvLocked = computed(() => timezoneItem.value?.source === 'env')

/** 常用时区回落清单（浏览器缺 ``Intl.supportedValuesOf`` 时用；名 → 展示名）。 */
const COMMON_TIMEZONES: Array<[string, string]> = [
  ['UTC', 'UTC'],
  ['Asia/Shanghai', '中国上海（Asia/Shanghai）'],
  ['Asia/Hong_Kong', '中国香港（Asia/Hong_Kong）'],
  ['Asia/Taipei', '中国台北（Asia/Taipei）'],
  ['Asia/Tokyo', '日本东京（Asia/Tokyo）'],
  ['Asia/Singapore', '新加坡（Asia/Singapore）'],
  ['Asia/Seoul', '韩国首尔（Asia/Seoul）'],
  ['Europe/London', '英国伦敦（Europe/London）'],
  ['Europe/Berlin', '德国柏林（Europe/Berlin）'],
  ['Europe/Moscow', '俄罗斯莫斯科（Europe/Moscow）'],
  ['America/New_York', '美国纽约（America/New_York）'],
  ['America/Chicago', '美国芝加哥（America/Chicago）'],
  ['America/Los_Angeles', '美国洛杉矶（America/Los_Angeles）'],
  ['Australia/Sydney', '澳大利亚悉尼（Australia/Sydney）'],
]

function timezoneLabel(name: string): string {
  return COMMON_TIMEZONES.find(([key]) => key === name)?.[1] ?? name
}

/** 时区下拉选项：优先浏览器 IANA 全量清单（可搜索任意 IANA 名），缺失回落常用清单。 */
const timezoneOptions = computed(() => {
  const intl = Intl as unknown as { supportedValuesOf?: (key: string) => string[] }
  let names: string[] = COMMON_TIMEZONES.map(([name]) => name)
  if (typeof intl.supportedValuesOf === 'function') {
    try {
      const all = intl.supportedValuesOf('timeZone')
      if (Array.isArray(all) && all.length > 0) {
        names = [...new Set([...all, ...names])]
      }
    } catch {
      // 老浏览器无该 API 或抛异常：回落常用清单
    }
  }
  return names.map((name) => ({ label: timezoneLabel(name), value: name }))
})

const timezoneValue = ref('')
watch(
  timezoneItem,
  (item) => {
    timezoneValue.value = item?.value ?? ''
  },
  { immediate: true },
)
const timezoneDirty = computed(() => timezoneValue.value !== (timezoneItem.value?.value ?? ''))

/** E39 服务器时间快照（30s 轮询；探测结果服务端另有 30s 缓存，不打爆 NTP 池）。 */
const timeQuery = useQuery({
  queryKey: ['system', 'time'],
  queryFn: getSystemTime,
  refetchInterval: 30_000,
  staleTime: 20_000,
})

const savingTimezone = ref(false)

async function onSaveTimezone(): Promise<void> {
  if (!timezoneValue.value || savingTimezone.value) {
    return
  }
  savingTimezone.value = true
  try {
    await saveDumpBehaviorConfig({ timezone: timezoneValue.value })
    message.success(t('settings.timezone.saved'))
    // E39 时区统一：保存成功即刷新全站展示时区（jobs/快照/报表等即刻按新时区渲染，
    // 无需刷新页面）；setDisplayTimezone 自带坏名回落浏览器本地。
    setDisplayTimezone(timezoneValue.value)
    await configQuery.refetch()
    await timeQuery.refetch()
  } catch {
    // 错误（422 键级校验 / ENV_OVERRIDDEN 等）由 http 层全局提示
  } finally {
    savingTimezone.value = false
  }
}

// ---- 系统域（compose → UI 迁移第一批：log.level / db.busy_timeout_ms）----

const LOG_LEVEL_OPTIONS = [
  { label: 'DEBUG', value: 'DEBUG' },
  { label: 'INFO', value: 'INFO' },
  { label: 'WARNING', value: 'WARNING' },
  { label: 'ERROR', value: 'ERROR' },
]

const logLevelItem = computed(() => configItem('log.level'))
const busyTimeoutItem = computed(() => configItem('db.busy_timeout_ms'))
/** env 覆盖锁定（任一键被 compose 锁定即整区禁用，避免半锁半可改的困惑）。 */
const systemEnvLocked = computed(
  () => logLevelItem.value?.source === 'env' || busyTimeoutItem.value?.source === 'env',
)

const systemLogLevel = ref('INFO')
const systemBusyTimeout = ref<number | null>(5000)
const savingSystem = ref(false)

watch(configQuery.data, (data) => {
  if (!data) {
    return
  }
  const item = (key: string) => data.items.find((entry) => entry.key === key) ?? null
  systemLogLevel.value = (item('log.level')?.value ?? 'INFO').toUpperCase()
  const rawBusy = item('db.busy_timeout_ms')?.value
  const parsed = Number(rawBusy)
  systemBusyTimeout.value = rawBusy && Number.isFinite(parsed) ? parsed : 5000
})

const systemDirty = computed(
  () =>
    systemLogLevel.value !== (logLevelItem.value?.value ?? 'INFO').toUpperCase() ||
    String(systemBusyTimeout.value ?? '') !== (busyTimeoutItem.value?.value ?? '5000'),
)

async function onSaveSystem(): Promise<void> {
  if (savingSystem.value || systemEnvLocked.value) {
    return
  }
  savingSystem.value = true
  try {
    await saveDumpBehaviorConfig({
      'log.level': systemLogLevel.value,
      'db.busy_timeout_ms': String(systemBusyTimeout.value ?? 5000),
    })
    message.success(t('settings.system.saved'))
    await configQuery.refetch()
  } catch {
    // 错误（422 键级校验 / ENV_OVERRIDDEN 等）由 http 层全局提示
  } finally {
    savingSystem.value = false
  }
}

// ---- NTP 对时服务器（E39：主备两个地址；皆空回落内置默认链，见生效链展示） ----

const ntpPrimaryItem = computed(() => configItem('time_sync.ntp_primary'))
const ntpBackupItem = computed(() => configItem('time_sync.ntp_backup'))
const ntpEnvLocked = computed(() => ntpPrimaryItem.value?.source === 'env' || ntpBackupItem.value?.source === 'env')

const ntpPrimary = ref('')
const ntpBackup = ref('')
watch(
  [ntpPrimaryItem, ntpBackupItem],
  ([primary, backup]) => {
    ntpPrimary.value = primary?.value ?? ''
    ntpBackup.value = backup?.value ?? ''
  },
  { immediate: true },
)

const ntpDirty = computed(
  () =>
    ntpPrimary.value.trim() !== (ntpPrimaryItem.value?.value ?? '') ||
    ntpBackup.value.trim() !== (ntpBackupItem.value?.value ?? ''),
)

const savingNtp = ref(false)

async function onSaveNtpServers(): Promise<void> {
  if (savingNtp.value || ntpEnvLocked.value) {
    return
  }
  savingNtp.value = true
  try {
    // 空串 = 清除该地址（回落默认链/仅用另一地址）；主备两键恒同批提交
    await saveDumpBehaviorConfig({
      'time_sync.ntp_primary': ntpPrimary.value.trim(),
      'time_sync.ntp_backup': ntpBackup.value.trim(),
    })
    message.success(t('settings.timezone.ntpSaved'))
    await configQuery.refetch()
    // 探测缓存键含服务器链：换链后 refetch 即按新链重新探测
    await timeQuery.refetch()
  } catch {
    // 错误（422 键级校验 / ENV_OVERRIDDEN 等）由 http 层全局提示
  } finally {
    savingNtp.value = false
  }
}

/** 生效探测链（服务端按主备配置解析后透传，未配置时即内置默认链）。 */
const effectiveChainText = computed(() => timeQuery.data.value?.ntp_servers.join(' → ') ?? '—')

// ---- 服务器实时时钟（以服务器时刻为基准，规避本机时钟漂移） ----

const serverEpoch = ref(Date.now())
watch(
  timeQuery.data,
  (data) => {
    if (data) {
      serverEpoch.value = data.epoch_ms
    }
  },
  { immediate: true },
)

const clockTick = ref(0)
let clockTimer: number | undefined
onMounted(() => {
  clockTimer = window.setInterval(() => {
    clockTick.value += 1
  }, 1000)
})
onUnmounted(() => {
  if (clockTimer !== undefined) {
    window.clearInterval(clockTimer)
  }
})

const serverClock = computed(() => new Date(serverEpoch.value + clockTick.value * 1000))

/** 按 IANA 时区格式化（Intl 原生支持；解析失败回落本地格式）。 */
function formatInTimeZone(date: Date, tz: string): string {
  try {
    return new Intl.DateTimeFormat('zh-CN', {
      timeZone: tz,
      hour12: false,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    }).format(date)
  } catch {
    return formatDateTime(date)
  }
}

const serverClockText = computed(() => {
  const tz = timezoneValue.value || timeQuery.data.value?.timezone || 'UTC'
  return formatInTimeZone(serverClock.value, tz)
})

const ntpOffsetText = computed(() => {
  const offset = timeQuery.data.value?.ntp.offset_ms
  if (offset === null || offset === undefined) {
    return '—'
  }
  return `${offset >= 0 ? '+' : ''}${Math.round(offset)} ms`
})

// ---- 时间同步（确认门；ok=false 时原因必须展示，禁止静默） ----

const syncGateShow = ref(false)
const syncing = ref(false)
const syncResult = ref<TimeSyncResponse | null>(null)

const SYNC_REASONS: readonly string[] = [
  'clock.noop',
  'clock.permission_denied',
  'clock.unsupported_platform',
  'clock.failed',
  'clock.out_of_range',
  'ntp.timeout',
  'ntp.unreachable',
  'ntp.bad_response',
  'ntp.kod',
]

function syncReasonText(code: string | null): string {
  if (!code) {
    return ''
  }
  return SYNC_REASONS.includes(code) ? t(`settings.timezone.reason.${code}`) : code
}

const syncResultText = computed(() => {
  const result = syncResult.value
  if (!result) {
    return ''
  }
  if (result.ok && result.applied) {
    return t('settings.timezone.ntpOffset', {
      offset: `${result.offset_ms !== null && result.offset_ms >= 0 ? '+' : ''}${Math.round(result.offset_ms ?? 0)} ms`,
    })
  }
  return syncReasonText(result.reason)
})

async function doSyncTime(): Promise<void> {
  if (syncing.value) {
    return
  }
  syncing.value = true
  try {
    syncResult.value = await syncSystemTime()
    if (syncResult.value.ok) {
      message.success(syncResult.value.applied ? t('settings.timezone.syncOk') : t('settings.timezone.syncNoop'))
    } else {
      message.error(t('settings.timezone.syncFailed'))
    }
    await timeQuery.refetch()
  } catch {
    // AppError 已有全局提示
  } finally {
    syncing.value = false
  }
}

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

/** 一次性明文弹窗（生成后只显示这一次，与仓库设置密码同一呈现：SecretReveal）。 */
const generatedExportPassword = ref<string | null>(null)

/** 点「生成」：CSPRNG 生成 32 位口令 → 弹窗一次性展示（确认后才填入输入框）。 */
function onGenerateExportPassword(): void {
  generatedExportPassword.value = generatePassword()
}

/** 确认已备份 → 填入导出口令输入框（随后可直接点导出）。 */
function confirmGeneratedExportPassword(): void {
  exportPassword.value = generatedExportPassword.value ?? ''
  generatedExportPassword.value = null
}

/** 弹窗关闭（点遮罩 / ESC / 右上角）：丢弃未确认的一次性口令，不写入输入框。 */
function onExportSecretShow(value: boolean): void {
  if (!value) {
    generatedExportPassword.value = null
  }
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

// ---------------------------------------------------------------- 分区导航（2026-10-21 改版：点选切换替代锚点滚动）

/** 区块 id 与卡片标题键一一对应（复用既有 title 键 → 语言切换自动跟随，零新增目录文案）。
 *  缓存维护（E25）已并入仓库页维护区，不再作为本页区块。 */
const SECTIONS = [
  { id: 'dump', title: 'settings.dump.title' },
  { id: 'behavior', title: 'settings.behavior.title' },
  { id: 'credential', title: 'settings.credential.title' },
  { id: 'metrics', title: 'settings.metrics.title' },
  { id: 'timezone', title: 'settings.timezone.title' },
  { id: 'system', title: 'settings.system.title' },
  { id: 'password', title: 'settings.password.title' },
  { id: 'transfer', title: 'settings.transfer.title' },
] as const

type SectionId = (typeof SECTIONS)[number]['id']

/** 目录项：文案经 computed 派生，保证切换语言后即时刷新。 */
const sectionLinks = computed(() =>
  SECTIONS.map((item) => ({ id: item.id, label: t(item.title) })),
)

const activeSection = ref<SectionId>('dump')

/** 分段切换（P6：SegmentedTabs 受控 emit；未知 id 防御与 #sec-<id> 深链
 *  均已内置于组件——这里只做联合类型收窄后赋值）。 */
function onSectionChange(id: string): void {
  activeSection.value = id as SectionId
}
</script>

<template>
  <n-el class="settings">
    <!-- 分区切换：顶部分段条（P6 提升为共用 SegmentedTabs；全断点统一形态；
         吸顶常驻，滚长表单时仍在视口）。按钮主/次态走 naive 主题内部配色（NButton），
         亮暗双主题一致；深链 #sec-<id> 与未知 id 防御内置（hashPrefix='sec'）。
         吸顶背景用 --n-color（根 n-el 提供 bodyColor，暗色不回落白条）。 -->
    <div class="settings__segments">
      <SegmentedTabs
        :items="sectionLinks"
        :model-value="activeSection"
        hash-prefix="sec"
        :aria-label="t('settings.sectionsAria')"
        @update:model-value="onSectionChange"
      />
    </div>

    <!-- DB dump（批次 B 实装：dump.* 八键 + 密码三态） -->
    <!-- DB dump（候选 6 拆分：SectionDump 自包含） -->
    <SectionDump v-if="activeSection === 'dump'" />

    <!-- 行为开关（候选 6 拆分：SectionBehavior 自包含） -->
    <SectionBehavior v-if="activeSection === 'behavior'" />

    <!-- 凭据加密（候选 6 拆分：SectionCredential 自包含） -->
    <SectionCredential v-if="activeSection === 'credential'" />

    <n-card v-if="activeSection === 'metrics'" id="sec-metrics" class="settings__panel" :title="t('settings.metrics.title')" size="small">
      <n-space vertical :size="12">
        <n-text depth="3">{{ t('settings.metrics.desc') }}</n-text>
        <n-form label-placement="top" class="settings__form">
          <n-space align="center" :size="8">
            <n-text>{{ t('settings.metrics.token') }}</n-text>
            <!-- P3c 收口：抓取令牌三态（原 success / 无 type 两形态统一） -->
            <StatusTag v-if="metricsTokenSet" kind="success" :label="t('settings.metrics.tokenSet')" />
            <StatusTag v-else kind="default" :label="t('settings.metrics.tokenUnset')" />
          </n-space>
          <n-form-item :label="t('settings.metrics.newToken')">
            <PasswordInput
              v-model="metricsTokenInput"
              width="100%"
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
              <template #icon>
                <n-icon :size="16"><IconCheckCircle /></n-icon>
              </template>
              {{ t('settings.metrics.save') }}
            </n-button>
          </n-space>
        </n-form>
        <n-text depth="3" class="settings__hint">{{ t('settings.metrics.hint') }}</n-text>
      </n-space>
    </n-card>

    <!-- 时区（E39：手动设置 + 服务器时间展示 + NTP 时间同步） -->
    <n-card v-else-if="activeSection === 'timezone'" id="sec-timezone" class="settings__panel" :title="t('settings.timezone.title')" size="small">
      <n-space vertical :size="12">
        <n-text depth="3">{{ t('settings.timezone.desc') }}</n-text>

        <!-- 当前时区 + 来源（env 覆盖只读） -->
        <n-space align="center" :size="8" wrap>
          <n-text depth="2">{{ t('settings.timezone.current') }}</n-text>
          <StatusTag v-if="timezoneItem" kind="default" :label="timezoneItem.value ?? t('settings.timezone.unset')" />
          <StatusTag v-if="timezoneItem?.source === 'env'" kind="warning" :label="t('settings.timezone.envNote')" />
          <n-text v-if="!timezoneItem" depth="3">{{ t('settings.timezone.unset') }}</n-text>
        </n-space>

        <!-- 服务器实时时钟（以服务器时刻为基准）+ NTP 偏差 -->
        <n-space vertical :size="8">
          <n-space align="center" :size="8" wrap>
            <n-text depth="2">{{ t('settings.timezone.serverTime') }}</n-text>
            <n-text strong class="settings__clock">{{ serverClockText }}</n-text>
            <n-text depth="3">{{ timeQuery.data.value?.utc_offset }}</n-text>
            <n-button size="tiny" quaternary :loading="timeQuery.isFetching.value" @click="timeQuery.refetch()">
              {{ t('settings.timezone.refresh') }}
            </n-button>
          </n-space>
          <n-space align="center" :size="8" wrap>
            <n-text depth="2">{{ t('settings.timezone.ntpLabel') }}</n-text>
            <template v-if="timeQuery.data.value?.ntp.available">
              <StatusTag kind="success" :label="t('settings.timezone.ntpOk')" />
              <n-text depth="3">
                {{ t('settings.timezone.ntpOffset', { offset: ntpOffsetText }) }}（{{ timeQuery.data.value?.ntp.server }}）
              </n-text>
            </template>
            <template v-else>
              <StatusTag kind="warning" :label="t('settings.timezone.ntpUnavailable')" />
              <n-text depth="3">{{ syncReasonText(timeQuery.data.value?.ntp.reason ?? null) }}</n-text>
            </template>
          </n-space>
        </n-space>

        <!-- NTP 对时服务器（E39：主备两地址；皆空回落内置默认链） -->
        <n-space vertical :size="8">
          <n-text depth="2">{{ t('settings.timezone.ntpServersTitle') }}</n-text>
          <n-text depth="3">{{ t('settings.timezone.ntpServersDesc') }}</n-text>
          <n-form label-placement="top" class="settings__form">
            <n-form-item :label="t('settings.timezone.ntpPrimary')">
              <n-input
                v-model:value="ntpPrimary"
                placeholder="pool.ntp.org"
                :disabled="ntpEnvLocked"
              />
            </n-form-item>
            <n-form-item :label="t('settings.timezone.ntpBackup')">
              <n-input
                v-model:value="ntpBackup"
                placeholder="ntp.aliyun.com"
                :disabled="ntpEnvLocked"
              />
            </n-form-item>
            <n-space align="center" :size="8">
              <n-button
                size="small"
                type="primary"
                :loading="savingNtp"
                :disabled="!ntpDirty || ntpEnvLocked"
                @click="onSaveNtpServers"
              >
                <template #icon>
                  <n-icon :size="16"><IconCheckCircle /></n-icon>
                </template>
                {{ t('common.save') }}
              </n-button>
              <n-text v-if="ntpEnvLocked" depth="3">{{ t('settings.timezone.envLocked') }}</n-text>
            </n-space>
          </n-form>
          <n-text depth="3">{{ t('settings.timezone.ntpEffective', { chain: effectiveChainText }) }}</n-text>
        </n-space>

        <!-- 时区选择 + 保存（env 覆盖时禁用并指路） -->
        <n-form label-placement="top" class="settings__form">
          <n-form-item :label="t('settings.timezone.select')">
            <n-select
              v-model:value="timezoneValue"
              :options="timezoneOptions"
              filterable
              :placeholder="t('settings.timezone.search')"
              :disabled="timezoneEnvLocked"
              class="settings__tz-select"
            />
          </n-form-item>
          <n-space align="center" :size="8">
            <n-button
              size="small"
              type="primary"
              :loading="savingTimezone"
              :disabled="!timezoneDirty || !timezoneValue || timezoneEnvLocked"
              @click="onSaveTimezone"
            >
              <template #icon>
                <n-icon :size="16"><IconCheckCircle /></n-icon>
              </template>
              {{ t('common.save') }}
            </n-button>
            <n-text v-if="timezoneEnvLocked" depth="3">{{ t('settings.timezone.envLocked') }}</n-text>
          </n-space>
        </n-form>

        <!-- 时间同步（NTP 校准系统时钟；结果必须可见，禁止静默） -->
        <n-space vertical :size="8">
          <n-text depth="2">{{ t('settings.timezone.syncTitle') }}</n-text>
          <n-text depth="3">{{ t('settings.timezone.syncDesc') }}</n-text>
          <n-space align="center" :size="8" wrap>
            <n-button size="small" secondary :loading="syncing" @click="syncGateShow = true">
              <template #icon>
                <n-icon :size="16"><IconClock /></n-icon>
              </template>
              {{ t('settings.timezone.syncNow') }}
            </n-button>
            <template v-if="syncResult">
              <StatusTag
                :kind="syncResult.ok ? 'success' : 'error'"
                :label="syncResult.ok ? t('settings.timezone.syncOk') : t('settings.timezone.syncFailed')"
              />
              <n-text depth="3">{{ syncResultText }}</n-text>
            </template>
          </n-space>
        </n-space>
      </n-space>
    </n-card>

    <!-- 管理员改密（E28） -->
    <!-- 系统域（compose → UI 迁移第一批）：日志级别 + SQLite 锁等待 -->
    <n-card v-else-if="activeSection === 'system'" id="sec-system" class="settings__panel" :title="t('settings.system.title')" size="small">
      <n-space vertical :size="12">
        <n-text depth="3">{{ t('settings.system.desc') }}</n-text>
        <n-alert v-if="systemEnvLocked" type="warning" :show-icon="false">
          {{ t('settings.system.envLocked') }}
        </n-alert>
        <n-form label-placement="top" class="settings__form">
          <n-space vertical :size="12">
            <n-form-item :label="t('settings.system.logLevel')">
              <n-select
                v-model:value="systemLogLevel"
                :options="LOG_LEVEL_OPTIONS"
                :disabled="systemEnvLocked"
                size="small"
                class="settings__select"
              />
            </n-form-item>
            <n-text depth="3" class="settings__hint">{{ t('settings.system.logLevelHint') }}</n-text>
            <n-form-item :label="t('settings.system.busyTimeout')">
              <n-input-number
                v-model:value="systemBusyTimeout"
                :min="100"
                :max="600000"
                :step="1000"
                :show-button="false"
                :disabled="systemEnvLocked"
                size="small"
                class="settings__number"
              />
            </n-form-item>
            <n-text depth="3" class="settings__hint">{{ t('settings.system.busyTimeoutHint') }}</n-text>
          </n-space>
          <n-space justify="end">
            <n-button
              type="primary"
              :loading="savingSystem"
              :disabled="!systemDirty || systemEnvLocked"
              @click="onSaveSystem"
            >
              <template #icon>
                <n-icon :size="16"><IconCheckCircle /></n-icon>
              </template>
              {{ t('common.save') }}
            </n-button>
          </n-space>
        </n-form>
      </n-space>
    </n-card>

    <n-card v-else-if="activeSection === 'password'" id="sec-password" class="settings__panel" :title="t('settings.password.title')" size="small">
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

    <!-- 批次 E 配置迁移：导出（口令加密凭据段）+ 导入（跳过/覆盖 + 报告） -->
    <n-card v-else-if="activeSection === 'transfer'" id="sec-transfer" class="settings__panel" :title="t('settings.transfer.title')" size="small">
      <n-space vertical :size="12">
        <n-text depth="3">{{ t('settings.transfer.desc') }}</n-text>
        <!-- 生成口令后的一次性明文（全站统一呈现 SecretReveal；确认后才填入输入框） -->
        <n-modal
          :show="generatedExportPassword !== null"
          preset="card"
          :title="t('settings.transfer.passwordGeneratedTitle')"
          style="width: min(520px, calc(100vw - 32px))"
          @update:show="onExportSecretShow"
        >
          <SecretReveal
            :secret="generatedExportPassword ?? ''"
            :hint="t('settings.transfer.passwordGeneratedHint')"
            :confirm-label="t('settings.transfer.passwordSavedClose')"
            @confirm="confirmGeneratedExportPassword"
          />
        </n-modal>
        <n-space align="center" :size="8" wrap>
          <PasswordInput
            v-model="exportPassword"
            width="260px"
            :placeholder="t('settings.transfer.exportPassword')"
          />
          <!-- 生成 32 位导出口令 → 弹窗一次性展示（SecretReveal），确认后才填入输入框 -->
          <n-button size="small" secondary @click="onGenerateExportPassword">
            <template #icon>
              <n-icon :size="16"><IconShieldCheck /></n-icon>
            </template>
            {{ t('common.generatePassword') }}
          </n-button>
          <n-button
            size="small"
            type="primary"
            secondary
            :loading="exporting"
            :disabled="exportPassword.length < 10"
            @click="onExport"
          >
            <template #icon>
              <n-icon :size="16"><IconDownload /></n-icon>
            </template>
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
          <PasswordInput
            v-model="importPassword"
            width="260px"
            :placeholder="t('settings.transfer.importPassword')"
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

    <!-- E39 时间同步确认门：校准系统时钟影响调度与日志时间，先预览再确认 -->
    <ConfirmGate
      v-model:show="syncGateShow"
      :title="t('settings.timezone.syncGateTitle')"
      :description="t('settings.timezone.syncGateDesc')"
      :loading="syncing"
      :confirm-label="t('settings.timezone.syncNow')"
      @confirm="doSyncTime"
    />
  </n-el>
</template>

<style scoped>
/* 布局（2026-10-21 改版，替代 P4 锚点长页）：顶部分段 Tab + 单面板，内容全宽。
   曾用「左 NMenu 分区导航 + 右内容」双栏，因与主导航并排成双竖导航被否决
   （见文件头）——勿回退双栏形态。根 n-el 提供 --n-color（naive 把 bodyColor
   暴露在自渲染元素上），分段条吸顶背景随主题，暗色不回落白条。 */

/* 分区切换分段条：全断点统一形态；吸顶常驻（滚长表单时仍在视口），
   窄屏横向滚动不换行（P6 起由共用 SegmentedTabs 的 .rw-segmented 承担）。
   按钮主/次态由 NButton 主题内部配色。 */
.settings__segments {
  position: sticky;
  top: 0;
  z-index: 3;
  margin: 0 0 16px;
  padding: 8px 0;
  background: var(--n-color, #fff);
}

/* 内容面板：仅渲染当前分区；分区卡片带该类，切换（v-if 换卡）即重放入场动画。
   尊重系统减少动效偏好（WCAG 2.3.3）。 */
.settings__panel {
  animation: settings-panel-in 0.16s ease-out;
}

@keyframes settings-panel-in {
  from {
    opacity: 0;
    transform: translateY(4px);
  }
}

@media (prefers-reduced-motion: reduce) {
  .settings__panel {
    animation: none;
  }
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
  padding: 12px 0 4px;
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

/* E39：时区下拉（IANA 清单可搜索，留足宽度避免长名截断） */
.settings__tz-select {
  max-width: 360px;
}

/* E39：服务器实时时钟（等宽数字，避免秒位抖动导致横向窜动） */
.settings__clock {
  font-variant-numeric: tabular-nums;
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
