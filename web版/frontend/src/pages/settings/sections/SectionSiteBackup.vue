<script setup lang="ts">
/**
 * 设置 · 全站备份与恢复分区（方案 v1.5 §3.2 / §4.2 / §5.5；W5 前端）。
 *
 * 与「配置迁移」是两条路：本卡是**整机替换**（replace）语义，导出 = 配置 + 仓库密码文件
 * +（可选）rclone 配置与 conf 口令的整体加密包；导入 = 单事务清空重建 + 文件回滚点。
 *
 * 三段式（§4.1：预检 → 导入 → 报告）：
 *   ① 导出：口令（≥10 位，可一键生成）+ rclone 勾选 → 本地 Blob 落盘；
 *   ② 预检（E70，**零业务写入**）：先看包摘要与目标现状，阻碍项非空则禁用导入；
 *   ③ 导入（E71）：ConfirmGate 关键词门 → 执行中/结果留在弹窗 → 报告 + 重新登录。
 *
 * 门禁落在两处（与 backend 同源，非前端自造）：服务端 `acknowledge` 必须逐字等于包外层
 * `created_at`（§4.2），前端 ConfirmGate 只是人工确认的第一道；两者都不可省。
 *
 * S3：口令只在组件内存与请求体里往返——不落 localStorage、不进 URL、不进日志。
 */

import {
  NAlert,
  NButton,
  NCard,
  NCheckbox,
  NDescriptions,
  NDescriptionsItem,
  NIcon,
  NModal,
  NSpace,
  NText,
  useMessage,
} from 'naive-ui'
import { computed, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { isAppError } from '@/api/http'
import {
  LEGACY_CONFIG_PACK_FORMAT,
  SITE_PACK_FORMAT,
  exportSiteBackup,
  importSiteBackup,
  inspectSiteBackup,
} from '@/api/siteBackup'
import type { SiteBackupPack, SiteImportReport, SiteInspectReport } from '@/api/types'
import ConfirmGate from '@/components/ConfirmGate.vue'
import PasswordInput from '@/components/PasswordInput.vue'
import SecretReveal from '@/components/SecretReveal.vue'
import { IconDownload, IconSearch, IconUpload } from '@/components/icons/icons'
import { useUiStore } from '@/stores/ui'
import { generatePassword } from '@/utils/password'

const { t } = useI18n()
const message = useMessage()
const router = useRouter()
const ui = useUiStore()

/** 口令下限与后端 `SiteExportRequest.password`（min_length=10）同口径，前端先拦一道避免 422。 */
const PASSWORD_MIN = 10

// ---------------------------------------------------------------- 导出
const exportPassword = ref('')
const exporting = ref(false)
/** D1：默认随包携带 rclone.conf（含网盘 OAuth token，勾选项旁明写风险）。 */
const includeRcloneConf = ref(true)

/** 一次性明文弹窗（生成后只显示这一次；全站统一呈现走 SecretReveal）。 */
const generatedPassword = ref<string | null>(null)

function onGeneratePassword(): void {
  generatedPassword.value = generatePassword()
}

function confirmGeneratedPassword(): void {
  exportPassword.value = generatedPassword.value ?? ''
  generatedPassword.value = null
}

/** 弹窗关闭（遮罩 / ESC / 右上角）：丢弃未确认的一次性口令，不写入输入框。 */
function onSecretShow(value: boolean): void {
  if (!value) {
    generatedPassword.value = null
  }
}

const exportDisabled = computed(() => exporting.value || exportPassword.value.length < PASSWORD_MIN)

/** 导出：拿包对象 → 本地 Blob 下载（文件名日期取包外层 created_at，与服务端一致）。 */
async function onExport(): Promise<void> {
  if (exportDisabled.value) {
    return
  }
  exporting.value = true
  try {
    const pack = await exportSiteBackup({
      password: exportPassword.value,
      include_rclone_conf: includeRcloneConf.value,
    })
    const blob = new Blob([JSON.stringify(pack, null, 1)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `restic-web-site-${String(pack.created_at ?? '').slice(0, 10)}.json`
    anchor.click()
    URL.revokeObjectURL(url)
    message.success(t('settings.siteBackup.exportOk'))
    exportPassword.value = ''
  } catch {
    // PASSWORD_TOO_SHORT / SITE_PASSFILE_INCOMPLETE 等由 http 层全局提示（含具体原因）
  } finally {
    exporting.value = false
  }
}

// ---------------------------------------------------------------- 预检
const fileInput = ref<HTMLInputElement | null>(null)
const pickedPack = ref<SiteBackupPack | null>(null)
const pickedName = ref('')
const importPassword = ref('')
const inspecting = ref(false)
const inspectReport = ref<SiteInspectReport | null>(null)
const inspectError = ref('')

/** 统一错误文案（AppError 的 message 已含服务端可操作原因，如阻碍项清单）。 */
function errorText(error: unknown): string {
  return isAppError(error) ? error.message : t('errors.UNKNOWN')
}

/** 选文件：读文本 → JSON 解析 → 形态过滤（旧配置迁移包给「已停止支持」的指路文案）。 */
function onPickFile(event: Event): void {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) {
    return
  }
  void file.text().then((text) => {
    let parsed: SiteBackupPack
    try {
      parsed = JSON.parse(text) as SiteBackupPack
    } catch {
      message.error(t('settings.siteBackup.badFile'))
      return
    }
    const format = String((parsed as { format?: unknown }).format ?? '')
    if (format === LEGACY_CONFIG_PACK_FORMAT) {
      message.error(t('settings.siteBackup.legacyFile'))
      return
    }
    if (format !== SITE_PACK_FORMAT) {
      message.error(t('settings.siteBackup.badFile'))
      return
    }
    pickedPack.value = parsed
    pickedName.value = file.name
    importPassword.value = ''
    inspectReport.value = null
    inspectError.value = ''
  })
}

/** 预检（E70）：只读，不改动任何业务数据；阻碍项与导入侧 422 同源。 */
async function onInspect(): Promise<void> {
  if (!pickedPack.value || importPassword.value.length < 1) {
    return
  }
  inspecting.value = true
  inspectError.value = ''
  try {
    inspectReport.value = await inspectSiteBackup({
      pack: pickedPack.value,
      password: importPassword.value,
    })
  } catch (error) {
    inspectReport.value = null
    inspectError.value = errorText(error)
  } finally {
    inspecting.value = false
  }
}

// ---------------------------------------------------------------- 导入（ConfirmGate + 结果阶段）
const gateShow = ref(false)
/** ConfirmGate 的 awaitResult 阶段：running / done / failed；null = 未开始。 */
const phase = ref<'running' | 'done' | 'failed' | null>(null)
const phaseText = ref('')
const report = ref<SiteImportReport | null>(null)

/** 阻碍项为空且已预检才可导入（服务端仍会二次判定，这里只防误点）。 */
const canImport = computed(
  () =>
    !!inspectReport.value &&
    inspectReport.value.blockers.length === 0 &&
    phase.value !== 'running',
)

function startImport(): void {
  if (!canImport.value) {
    return
  }
  phase.value = null
  phaseText.value = ''
  gateShow.value = true
}

/** 门内告警文案（不可逆整机替换 + jobs 行数将被清空的副作用明示，§4.4）。 */
const gateDesc = computed(() => {
  const target = inspectReport.value?.target
  if (!target) {
    return t('settings.siteBackup.gateDesc')
  }
  return t('settings.siteBackup.gateDescWithJobs', { jobs: target.jobs })
})

/** 关门：遗留 failed 阶段复位（下次打开回到预览步，不残留上次结果）。 */
function onGateShow(value: boolean): void {
  gateShow.value = value
  if (!value && phase.value === 'failed') {
    phase.value = null
    phaseText.value = ''
  }
}

/** 导入（E71）：acknowledge 逐字传**包外层 created_at**（§4.2）；成功即会话全部作废。 */
async function doImport(): Promise<void> {
  const pack = pickedPack.value
  if (!pack || phase.value === 'running') {
    return
  }
  phase.value = 'running'
  phaseText.value = t('settings.siteBackup.running')
  try {
    const result = await importSiteBackup({
      pack,
      password: importPassword.value,
      mode: 'replace',
      acknowledge: String(pack.created_at ?? ''),
    })
    report.value = result
    phase.value = 'done'
    phaseText.value = t('settings.siteBackup.doneDesc')
    if (result.logged_out) {
      // §4.6：sessions 已被清空。两件事必须成对做——
      // ① 清本地会话态，避免界面仍显示「已登录」；
      // ② 置抑制位：此后任何请求拿到的 401 只清态不跳转。
      //    否则顶栏 15s 轮询的 401 会在几秒内冲掉这份报告（含回滚点目录名，
      //    误导入后唯一的回退依据），把用户甩到 /login。
      // 显式离开由「前往登录」按钮负责（goLogin）。
      ui.setSuppressAuthRedirect(true)
      ui.clearMe()
    }
    pickedPack.value = null
    pickedName.value = ''
    importPassword.value = ''
    inspectReport.value = null
    inspectError.value = ''
  } catch (error) {
    phase.value = 'failed'
    phaseText.value = errorText(error)
  }
}

/** 报告里的计数（服务端 applied 为各段写入行数；缺键回落 0，不显示 undefined）。 */
function appliedCount(key: string): number {
  return report.value?.applied?.[key] ?? 0
}

/** 显式前往登录：先清抑制位再跳转——离开本页后其他场景的 401 必须回到自动跳转。 */
function goLogin(): void {
  ui.setSuppressAuthRedirect(false)
  void router.replace({ name: 'login' })
}

// 兜底复位：用户可能不点「前往登录」而是直接切分区 / 关标签 / 改地址离开。
// 抑制位是**全局**状态，留在 set 状态会让此后所有 401 都不再跳转（其余页面行为被改坏）。
onUnmounted(() => ui.setSuppressAuthRedirect(false))

const rcloneConfText = computed(() => {
  const pack = inspectReport.value?.pack
  if (!pack) {
    return ''
  }
  if (!pack.rclone_conf_included) {
    return t('settings.siteBackup.notIncluded')
  }
  return t(pack.rclone_conf_encrypted ? 'settings.siteBackup.confEncrypted' : 'settings.siteBackup.confPlain')
})

const targetRcloneConfText = computed(() => {
  const target = inspectReport.value?.target
  if (!target) {
    return ''
  }
  return t(target.rclone_conf_encrypted ? 'settings.siteBackup.confEncrypted' : 'settings.siteBackup.confPlain')
})
</script>

<template>
  <n-card id="sec-siteBackup" class="settings__panel" :title="t('settings.siteBackup.title')" size="small">
    <n-space vertical :size="12">
      <n-text depth="3">{{ t('settings.siteBackup.desc') }}</n-text>

      <!-- §5.5 威胁模型 + 操作纪律：必须进 UI（不是可选的说明文字） -->
      <n-alert type="warning" :show-icon="false">{{ t('settings.siteBackup.securityNote') }}</n-alert>
      <n-text depth="3">{{ t('settings.siteBackup.securityDiscipline') }}</n-text>

      <!-- ① 导出 -->
      <n-text depth="2">{{ t('settings.siteBackup.exportTitle') }}</n-text>
      <n-space align="center" :size="8" wrap>
        <PasswordInput
          v-model="exportPassword"
          width="260px"
          :placeholder="t('settings.siteBackup.exportPassword')"
          :generate-label="t('common.generatePassword')"
          @generate="onGeneratePassword"
        />
        <n-button
          size="small"
          type="primary"
          secondary
          :loading="exporting"
          :disabled="exportDisabled"
          @click="onExport"
        >
          <template #icon>
            <n-icon :size="16"><IconDownload /></n-icon>
          </template>
          {{ t('settings.siteBackup.exportBtn') }}
        </n-button>
      </n-space>
      <n-space align="center" :size="8" wrap>
        <n-checkbox v-model:checked="includeRcloneConf">
          {{ t('settings.siteBackup.includeRclone') }}
        </n-checkbox>
        <n-text depth="3">{{ t('settings.siteBackup.includeRcloneHint') }}</n-text>
      </n-space>

      <!-- ② 预检 -->
      <n-text depth="2">{{ t('settings.siteBackup.importTitle') }}</n-text>
      <n-text depth="3">{{ t('settings.siteBackup.importDesc') }}</n-text>
      <n-space align="center" :size="8" wrap>
        <input
          ref="fileInput"
          type="file"
          accept="application/json,.json"
          class="settings__file-input"
          @change="onPickFile"
        />
        <n-button size="small" secondary @click="fileInput?.click()">
          <template #icon>
            <n-icon :size="16"><IconUpload /></n-icon>
          </template>
          {{ t('settings.siteBackup.pickFile') }}
        </n-button>
        <n-text v-if="pickedName" depth="2">{{ pickedName }}</n-text>
        <PasswordInput
          v-model="importPassword"
          width="240px"
          :placeholder="t('settings.siteBackup.importPassword')"
          :disabled="!pickedPack"
        />
        <n-button
          size="small"
          secondary
          :loading="inspecting"
          :disabled="!pickedPack || importPassword.length < 1"
          @click="onInspect"
        >
          <template #icon>
            <n-icon :size="16"><IconSearch /></n-icon>
          </template>
          {{ t('settings.siteBackup.inspectBtn') }}
        </n-button>
      </n-space>

      <!-- 预检失败（错口令 / 旧包 / 结构非法）：原因就地展示，不静默 */
      <n-alert v-if="inspectError" type="error" :show-icon="false">{{ inspectError }}</n-alert>

      <!-- 预检结果：包摘要 + 目标现状 + 阻碍项 -->
      <template v-if="inspectReport">
        <n-text depth="2">{{ t('settings.siteBackup.packSummary') }}</n-text>
        <n-descriptions :column="2" size="small" bordered>
          <n-descriptions-item :label="t('settings.siteBackup.packCreatedAt')">
            {{ inspectReport.pack.created_at }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('settings.siteBackup.packAppVersion')">
            {{ inspectReport.pack.app_version }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('settings.siteBackup.packSchema')">
            {{ inspectReport.pack.schema_version }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('settings.siteBackup.packRepos')">
            {{ inspectReport.pack.repos }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('settings.siteBackup.packTasks')">
            {{ inspectReport.pack.tasks }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('settings.siteBackup.packConfig')">
            {{ inspectReport.pack.config_keys }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('settings.siteBackup.packPassfiles')">
            {{ inspectReport.pack.passfiles }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('settings.siteBackup.packRcloneConf')">
            {{ rcloneConfText }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('settings.siteBackup.packRclonePass')">
            {{
              t(
                inspectReport.pack.rclone_pass_included
                  ? 'settings.siteBackup.included'
                  : 'settings.siteBackup.notIncluded',
              )
            }}
          </n-descriptions-item>
        </n-descriptions>
        <n-alert
          v-for="(warning, index) in inspectReport.pack.warnings"
          :key="index"
          type="warning"
          :show-icon="false"
        >
          {{ warning }}
        </n-alert>

        <n-text depth="2">{{ t('settings.siteBackup.targetSummary') }}</n-text>
        <n-descriptions :column="2" size="small" bordered>
          <n-descriptions-item :label="t('settings.siteBackup.targetRepos')">
            {{ inspectReport.target.repos }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('settings.siteBackup.targetReposConfigured')">
            {{ inspectReport.target.repos_configured }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('settings.siteBackup.targetTasks')">
            {{ inspectReport.target.tasks }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('settings.siteBackup.targetConfig')">
            {{ inspectReport.target.config_keys }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('settings.siteBackup.targetJobs')" :span="2">
            {{ inspectReport.target.jobs }} — {{ t('settings.siteBackup.targetJobsHint') }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('settings.siteBackup.targetActiveJobs')" :span="2">
            {{ inspectReport.target.active_jobs.join('、') || t('settings.siteBackup.none') }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('settings.siteBackup.targetRestic')">
            {{ t(inspectReport.target.restic_available ? 'settings.siteBackup.yes' : 'settings.siteBackup.no') }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('settings.siteBackup.targetPassfileWritable')">
            {{ t(inspectReport.target.passfile_writable ? 'settings.siteBackup.yes' : 'settings.siteBackup.no') }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('settings.siteBackup.targetConfWritable')">
            {{ t(inspectReport.target.rclone_conf_writable ? 'settings.siteBackup.yes' : 'settings.siteBackup.no') }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('settings.siteBackup.targetConfState')">
            {{ targetRcloneConfText }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('settings.siteBackup.targetRollbackKeep')" :span="2">
            {{ inspectReport.target.rollback_keep_count }}
          </n-descriptions-item>
        </n-descriptions>

        <!-- 阻碍项：与导入侧 422 同源，非空即不可导入 -->
        <template v-if="inspectReport.blockers.length > 0">
          <n-alert type="error" :show-icon="false">{{ t('settings.siteBackup.blockers') }}</n-alert>
          <n-text v-for="(blocker, index) in inspectReport.blockers" :key="index" depth="3">
            · {{ blocker }}
          </n-text>
        </template>

        <n-space align="center" :size="8" wrap>
          <n-button
            size="small"
            type="warning"
            secondary
            :disabled="!canImport"
            @click="startImport"
          >
            {{ t('settings.siteBackup.importBtn') }}
          </n-button>
          <n-text v-if="inspectReport.blockers.length === 0" depth="3">
            {{ t('settings.siteBackup.importReady') }}
          </n-text>
        </n-space>
      </template>

      <!-- ③ 导入报告（成功后会话已作废，故给「前往登录」） -->
      <template v-if="report">
        <n-alert type="success" :show-icon="false">{{ t('settings.siteBackup.doneTitle') }}</n-alert>
        <n-descriptions :column="2" size="small" bordered>
          <n-descriptions-item :label="t('settings.siteBackup.appliedRepos')">
            {{ appliedCount('repos') }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('settings.siteBackup.appliedTasks')">
            {{ appliedCount('tasks') }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('settings.siteBackup.appliedConfig')">
            {{ appliedCount('config') }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('settings.siteBackup.appliedForms')">
            {{ appliedCount('forms') }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('settings.siteBackup.appliedPassfiles')">
            {{ appliedCount('passfiles') }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('settings.siteBackup.appliedRcloneConf')">
            {{ appliedCount('rclone_conf') }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('settings.siteBackup.appliedRclonePass')">
            {{ appliedCount('rclone_pass') }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('settings.siteBackup.rollbackDir')">
            {{ report.rollback_dir }}
          </n-descriptions-item>
          <n-descriptions-item
            v-if="report.normalized_env_keys.length > 0"
            :label="t('settings.siteBackup.normalizedKeys')"
            :span="2"
          >
            {{ report.normalized_env_keys.join('、') }}
          </n-descriptions-item>
        </n-descriptions>
        <n-alert
          v-for="(warning, index) in report.warnings"
          :key="index"
          type="warning"
          :show-icon="false"
        >
          {{ warning }}
        </n-alert>
        <n-space v-if="report.logged_out" align="center" :size="8" wrap>
          <n-text depth="3">{{ t('settings.siteBackup.relogin') }}</n-text>
          <n-button size="small" type="primary" @click="goLogin">
            {{ t('settings.siteBackup.reloginBtn') }}
          </n-button>
        </n-space>
      </template>
    </n-space>
  </n-card>

  <!-- 生成口令的一次性明文（全站统一呈现：SecretReveal，确认后才填入输入框） -->
  <n-modal
    :show="generatedPassword !== null"
    preset="card"
    :title="t('settings.siteBackup.passwordGeneratedTitle')"
    style="width: min(520px, calc(100vw - 32px))"
    @update:show="onSecretShow"
  >
    <SecretReveal
      :secret="generatedPassword ?? ''"
      :hint="t('settings.siteBackup.passwordGeneratedHint')"
      :confirm-label="t('settings.siteBackup.passwordSavedClose')"
      @confirm="confirmGeneratedPassword"
    />
  </n-modal>

  <!-- 导入确认门（不可逆整机替换）：预览 → 输入 REPLACE → 二次确认 → 执行中/结果留在弹窗 -->
  <ConfirmGate
    :show="gateShow"
    :title="t('settings.siteBackup.gateTitle')"
    :keyword="'REPLACE'"
    :description="gateDesc"
    :confirm-label="t('settings.siteBackup.gateConfirm')"
    await-result
    :phase="phase"
    :progress-label="phaseText"
    :result-text="phaseText"
    @update:show="onGateShow"
    @confirm="doImport"
  >
    <template #preview>
      <n-text v-if="inspectReport" depth="3">
        {{ t('settings.siteBackup.gatePreview', { createdAt: inspectReport.pack.created_at }) }}
      </n-text>
    </template>
  </ConfirmGate>
</template>

<style scoped>
/* 备份包文件选择框隐藏（按钮触发；与配置迁移分区同款） */
.settings__file-input { display: none; }
</style>
