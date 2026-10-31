<script setup lang="ts">
/**
 * 设置 · 配置迁移分区（批次 E：导出/导入；候选 6 拆分）。
 *
 * 非配置卡：走 configTransfer 的 exportConfig / importConfig，不走 config patch 通道。
 * 导出用口令加密凭据段；导入支持跳过/覆盖 + 报告，覆盖走 IMPORT 确认门。
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
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { exportConfig, importConfig } from '@/api/configTransfer'
import type { ConfigExportPack, ConfigImportReport } from '@/api/types'
import ConfirmGate from '@/components/ConfirmGate.vue'
import PasswordInput from '@/components/PasswordInput.vue'
import SecretReveal from '@/components/SecretReveal.vue'
import { IconDownload, IconShieldCheck } from '@/components/icons/icons'
import { generatePassword } from '@/utils/password'
import { estimatePasswordStrength } from '@/utils/passwordStrength'

const { t } = useI18n()
const message = useMessage()
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

</script>

<template>
    <n-card id="sec-transfer" class="settings__panel" :title="t('settings.transfer.title')" size="small">
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
            
    <!-- 覆盖导入确认门：预览 → 输入 IMPORT → 二次确认（覆盖不可逆） -->
    <ConfirmGate
      v-model:show="gateShowImport"
      :title="t('settings.transfer.gateTitle')"
      :keyword="'IMPORT'"
      :description="t('settings.transfer.gateDesc')"
      :loading="importing"
      @confirm="doImport"
    />
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
</template>
