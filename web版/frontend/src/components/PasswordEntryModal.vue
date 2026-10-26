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
 * 生成态的呈现与复制已抽到 `SecretReveal`（唯一实现）——本组件只管两段式输入形态。
 */
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { NAlert, NButton, NForm, NFormItem, NModal, NSpace } from 'naive-ui'
import PasswordInput from '@/components/PasswordInput.vue'
import SecretReveal from '@/components/SecretReveal.vue'

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
      <!-- 生成态 = 一次性明文展示，复用全站唯一实现（SecretReveal）：
           呈现/复制/确认三件事在 rclone、导出口令、仓库密码等处完全一致 -->
      <SecretReveal
        v-if="generated"
        :secret="generated"
        :hint="generatedHint"
        :confirm-label="confirmLabel"
        :show-back="showGeneratedCancel"
        :loading="loading"
        @confirm="emit('confirm-generated')"
        @back="close"
      />
      <template v-else>
        <n-alert type="warning" :show-icon="false">{{ intro }}</n-alert>
        <n-form label-placement="top" size="small">
          <n-form-item :label="customLabel">
            <PasswordInput v-model="pw" width="100%" :placeholder="customPlaceholder" />
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
