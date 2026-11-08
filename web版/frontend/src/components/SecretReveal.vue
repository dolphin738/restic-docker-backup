<script setup lang="ts">
/** 一次性明文展示块（**项目唯一实现**，S-4 单一实现）。
 *
 * 语义：某个密码/口令**只在生成时显示这一次**——用户必须当场复制备份，之后系统不再
 * 提供查看（服务端不回显、UI 不落库）。所有「自动生成密码」的落点共用本组件，
 * 保证呈现、复制交互、确认文案在三处以上保持一致（此前各写一套）。
 *
 * 使用场景：
 * - 仓库设置密码 E44（后端生成、写入口令文件后回显）
 * - 任务向导「新建仓库」步（2026-11-02 起：建仓生成态经 PasswordEntryModal 复用本组件；
 *   此前曾是不可复制的 message.warning toast，违背「唯一实现」约定）
 * - 仓库密钥轮换 C4（前端生成、确认后提交）
 * - 重建默认仓库结果（后端返回的一次性新密码）
 * - rclone conf 段密码生成（crypt / webdav 三处）
 * - 全站备份包导出口令生成（2026-11-08 起取代原「配置迁移导出口令」）
 *
 * 本组件只管**展示与复制**：生成实现、确认后的副作用全部由父组件决定
 * （通过 `confirm` / `back` 事件）。通常包在 `n-modal` 里使用。
 */
import { useI18n } from 'vue-i18n'
import { copyWithNotify } from '@/utils/clipboard'
import { NAlert, NButton, NInput, NSpace, useMessage } from 'naive-ui'

const props = withDefaults(
  defineProps<{
    /** 一次性明文（等宽只读展示）。 */
    secret: string
    /** 顶部提示（说明「只显示这一次」「需立即备份」等）。 */
    hint: string
    /** 确认按钮文案（如「我已备份，关闭」「确认轮换」）。 */
    confirmLabel: string
    /** 显示「返回」按钮（未提交前可退回自填之类）。 */
    showBack?: boolean
    backLabel?: string
    loading?: boolean
  }>(),
  { showBack: false, backLabel: undefined, loading: false },
)

const emit = defineEmits<{
  confirm: []
  back: []
}>()

const { t } = useI18n()
const message = useMessage()

async function copy(): Promise<void> {
  await copyWithNotify(props.secret, message, t('common.copied'), t('common.copyFailed'))
}
</script>

<template>
  <NSpace vertical :size="10">
    <NAlert type="success" :show-icon="false">{{ hint }}</NAlert>
    <!-- 等宽字体便于人工抄录（fontFamilyMono 经全局 CSS 变量） -->
    <NInput :value="secret" readonly :style="{ fontFamily: 'var(--font-family-mono)' }" />
    <NSpace justify="end">
      <NButton secondary @click="copy">{{ t('common.copy') }}</NButton>
      <NButton v-if="showBack" quaternary @click="emit('back')">
        {{ backLabel ?? t('common.cancel') }}
      </NButton>
      <NButton type="primary" :loading="loading" @click="emit('confirm')">
        {{ confirmLabel }}
      </NButton>
    </NSpace>
  </NSpace>
</template>
