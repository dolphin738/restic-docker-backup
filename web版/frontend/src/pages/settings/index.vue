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
  NModal,
  NSpace,
  NText,
  useMessage,
} from 'naive-ui'
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { changePassword } from '@/api/auth'
import { exportConfig, importConfig } from '@/api/configTransfer'
import type { ConfigExportPack, ConfigImportReport } from '@/api/types'
import { IconDownload, IconLock, IconShieldCheck } from '@/components/icons/icons'
import ConfirmGate from '@/components/ConfirmGate.vue'
import PasswordInput from '@/components/PasswordInput.vue'
import SecretReveal from '@/components/SecretReveal.vue'
// P6 形态层：分段控件从本页私有实现提升为共用组件（S-4 单一实现）；
// 深链（#sec-<id>）与未知 id 防御已内置于组件（hashPrefix='sec'）。
import SegmentedTabs from '@/components/dashboard/SegmentedTabs.vue'
import SectionDump from '@/pages/settings/sections/SectionDump.vue'
import SectionBehavior from '@/pages/settings/sections/SectionBehavior.vue'
import SectionCredential from '@/pages/settings/sections/SectionCredential.vue'
import SectionMetrics from '@/pages/settings/sections/SectionMetrics.vue'
import SectionSystem from '@/pages/settings/sections/SectionSystem.vue'
import SectionTimezone from '@/pages/settings/sections/SectionTimezone.vue'
// G4：导出口令强度条（纯函数估算，见 utils/passwordStrength.ts）
// 生成统一走全站唯一生成器 utils/password.ts（32 位，与仓库密码同口径）
import { generatePassword } from '@/utils/password'
import { estimatePasswordStrength } from '@/utils/passwordStrength'

const { t } = useI18n()
const message = useMessage()



// ---------------------------------------------------------------- 凭据加密（R-025 / P3-A）

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

    <!-- Prometheus 指标（候选 6 拆分：SectionMetrics 自包含） -->
    <SectionMetrics v-if="activeSection === 'metrics'" />

    <!-- 系统域（候选 6 拆分：SectionSystem 自包含） -->
    <SectionSystem v-if="activeSection === 'system'" />

    <!-- 时区 / NTP / 时钟 / 对时（候选 6 拆分：SectionTimezone 自包含） -->
    <SectionTimezone v-if="activeSection === 'timezone'" />

    <n-card v-if="activeSection === 'password'" id="sec-password" class="settings__panel" :title="t('settings.password.title')" size="small">
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
