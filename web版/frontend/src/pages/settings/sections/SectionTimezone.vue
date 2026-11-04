<script setup lang="ts">
/**
 * 设置 · 时区分区（E39：手动时区 + NTP 主备 + 服务器实时时钟 + 时间同步）。
 *
 * 候选 6 拆分：设置页最大的一域（原 250 行 script + 126 行模板）。整体搬为一个
 * 组件而非再切：timeQuery 被时区/NTP/时钟/同步四块共用，拆开要跨文件传同一份
 * query 与确认门状态，得不偿失。
 *
 * 两个保存动作各走一条 useConfigPatch（时区 / NTP 主备），afterSave 分别做
 * 「刷新全站展示时区」与「按新链重新探测」。
 */

import { useQuery } from '@tanstack/vue-query'
import {
  NButton,
  NCard,
  NForm,
  NFormItem,
  NIcon,
  NInput,
  NSelect,
  NSpace,
  NText,
  useMessage,
} from 'naive-ui'
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getConfig } from '@/api/config'
import { saveDumpBehaviorConfig } from '@/api/settings'
import { getSystemTime, syncSystemTime } from '@/api/system'
import type { TimeSyncResponse } from '@/api/types'
import ConfirmGate from '@/components/ConfirmGate.vue'
import { IconCheckCircle, IconClock } from '@/components/icons/icons'
import StatusTag from '@/components/StatusTag.vue'
import { useConfigPatch } from '@/composables/useConfigPatch'
import { formatDateTime, setDisplayTimezone } from '@/utils/datetime'

const { t } = useI18n()
const message = useMessage()

const configQuery = useQuery({ queryKey: ['config', 'settings-page'], queryFn: getConfig })

/** 按 key 取配置项（env 锁定判定用）。 */
function configItem(key: string) {
  return configQuery.data.value?.items?.find((item) => item.key === key) ?? null
}

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



const timezonePatch = useConfigPatch({
  submit: saveDumpBehaviorConfig,
  successMessage: () => t('settings.timezone.saved'),
  notify: (text) => message.success(text),
  refetch: () => configQuery.refetch(),
  afterSave: async () => {
    // E39 时区统一：保存成功即刷新全站展示时区（jobs/快照/报表即刻按新时区渲染，
    // 无需刷新页面）；setDisplayTimezone 自带坏名回落浏览器本地。
    setDisplayTimezone(timezoneValue.value)
    await timeQuery.refetch()
  },
})
async function onSaveTimezone(): Promise<void> {
  if (!timezoneValue.value) {
    return
  }
  await timezonePatch.save({ timezone: timezoneValue.value })
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



const ntpPatch = useConfigPatch({
  submit: saveDumpBehaviorConfig,
  successMessage: () => t('settings.timezone.ntpSaved'),
  notify: (text) => message.success(text),
  refetch: () => configQuery.refetch(),
  afterSave: async () => {
    // 探测缓存键含服务器链：换链后 refetch 即按新链重新探测
    await timeQuery.refetch()
  },
})
async function onSaveNtpServers(): Promise<void> {
  if (ntpEnvLocked.value) {
    return
  }
  // 空串 = 清除该地址（回落默认链/仅用另一地址）；主备两键恒同批提交
  await ntpPatch.save({
    'time_sync.ntp_primary': ntpPrimary.value.trim(),
    'time_sync.ntp_backup': ntpBackup.value.trim(),
  })
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

</script>

<template>
    <n-card id="sec-timezone" class="settings__panel" :title="t('settings.timezone.title')" size="small">
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
                type="primary"
                :loading="ntpPatch.saving.value"
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
              type="primary"
              :loading="timezonePatch.saving.value"
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

    <!-- E39 时间同步确认门：校准系统时钟影响调度与日志时间，先预览再确认 -->
    <ConfirmGate
      v-model:show="syncGateShow"
      :title="t('settings.timezone.syncGateTitle')"
      :description="t('settings.timezone.syncGateDesc')"
      :loading="syncing"
      :confirm-label="t('settings.timezone.syncNow')"
      @confirm="doSyncTime"
    />
</template>

<style scoped>
.settings__form { max-width: 560px; }

/* 时区下拉（IANA 清单可搜索，留足宽度避免长名截断） */
.settings__tz-select { max-width: 360px; }

/* 服务器实时时钟（等宽数字，避免秒位抖动导致横向窜动） */
.settings__clock { font-variant-numeric: tabular-nums; }
</style>
