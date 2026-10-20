<script setup lang="ts">
/** 仓库密码输入弹窗（E44「设置仓库密码」与 C4「密钥轮换」共用的两段式输入组件）。
 *
 * 输入方式（E44 同款）：
 * - 自填：单栏密码输入（show-password-on=click），满足 minLength 后可提交；
 * - 生成：点击「生成」由父组件产生一次性明文（E44=后端 secrets 生成且已落盘后回显；
 *   轮换=前端本地强随机、确认后才随请求提交——轮换后端不回显明文，S3），展示只读
 *   明文 + 复制 + 确认动作。
 *
 * 本组件只管输入形态：生成实现、提交副作用全部由父组件事件决定。
 * 宽度收口为 min(520px, 视口-32px)——n-modal preset=card 默认无宽度约束，
 * 不设会被内容撑成全屏宽瘦长条。
 * 复制提示复用 repo.passwordCopy* 键（该组键源于仓库页，现为共用 UI 语言）。
 */
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { NAlert, NButton, NForm, NFormItem, NInput, NModal, NSpace, useMessage } from 'naive-ui'

const props = withDefaults(
  defineProps<{
    show: boolean
    title: string
    /** 自填态顶部的风险/说明文案（warning alert） */
    intro?: string
    /** 生成态顶部的一次性明文提示（success alert） */
    generatedHint: string
    customLabel?: string
    customPlaceholder?: string
    generateLabel?: string
    /** 自填态提交按钮文案（如「使用此密码」「使用此密码轮换」） */
    submitLabel?: string
    /** 生成态确认按钮文案（如「我已备份，关闭」「确认轮换」） */
    confirmLabel: string
    /** 生成态一次性明文；null = 自填态 */
    generated: string | null
    loading: boolean
    /** 自填密码最小长度（E44=1 仅要求非空；轮换=10） */
    minLength?: number
    /** 生成态是否显示「取消/返回」（E44 生成已落盘、无路可退=false；轮换未提交、可返回=true） */
    showGeneratedCancel?: boolean
    /** 是否显示「生成」按钮。重设密码（force 覆盖）模式隐藏：生成的新密码写进文件也打不开
     *  仓库（仓库密钥仍是旧的），只会让人以为「点了没用」；该模式只接受手填的旧密码重写文件。 */
    showGenerate?: boolean
  }>(),
  { minLength: 10, showGeneratedCancel: true, showGenerate: true },
)

const emit = defineEmits<{
  'update:show': [value: boolean]
  /** 点击「生成」——生成实现属父组件（后端生成 / 前端生成） */
  generate: []
  /** 自填提交，携带明文 */
  'submit-custom': [password: string]
  /** 生成态确认（E44=关闭；轮换=执行 restic key passwd） */
  'confirm-generated': []
}>()

const { t } = useI18n()
const message = useMessage()

const pw = ref('')
// 每次打开都清空自填草稿，避免上一次会话的残留被意外提交
watch(
  () => props.show,
  (show) => {
    if (show) {
      pw.value = ''
    }
  },
)

const customReady = computed(() => pw.value.trim().length >= props.minLength)

function close(): void {
  emit('update:show', false)
}

async function copyGenerated(): Promise<void> {
  if (!props.generated) {
    return
  }
  try {
    await navigator.clipboard.writeText(props.generated)
    message.success(t('repo.passwordCopied'))
  } catch {
    message.warning(t('repo.passwordCopyFailed'))
  }
}
</script>

<template>
  <n-modal
    :show="show"
    preset="card"
    :title="title"
    style="width: min(520px, calc(100vw - 32px))"
    @update:show="emit('update:show', $event)"
  >
    <n-space vertical :size="10">
      <template v-if="generated">
        <n-alert type="success" :show-icon="false">{{ generatedHint }}</n-alert>
        <!-- 等宽字体便于人工抄录（tokens.ts fontFamilyMono 经全局变量） -->
        <n-input :value="generated" readonly style="font-family: var(--font-family-mono)" />
        <n-space justify="end">
          <n-button secondary @click="copyGenerated">{{ t('repo.passwordCopy') }}</n-button>
          <n-button v-if="showGeneratedCancel" quaternary @click="close">
            {{ t('common.cancel') }}
          </n-button>
          <n-button type="primary" :loading="loading" @click="emit('confirm-generated')">
            {{ confirmLabel }}
          </n-button>
        </n-space>
      </template>
      <template v-else>
        <n-alert type="warning" :show-icon="false">{{ intro }}</n-alert>
        <n-form label-placement="top" size="small">
          <n-form-item :label="customLabel">
            <n-input
              v-model:value="pw"
              type="password"
              show-password-on="click"
              :placeholder="customPlaceholder"
            />
          </n-form-item>
        </n-form>
        <n-space justify="end">
          <n-button quaternary @click="close">{{ t('common.cancel') }}</n-button>
          <n-button :disabled="!customReady" :loading="loading" @click="emit('submit-custom', pw)">
            {{ submitLabel }}
          </n-button>
          <n-button v-if="showGenerate" type="primary" :loading="loading" @click="emit('generate')">
            {{ generateLabel }}
          </n-button>
        </n-space>
      </template>
    </n-space>
  </n-modal>
</template>
