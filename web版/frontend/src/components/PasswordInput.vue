<script setup lang="ts">
/** 统一密码输入框（**项目唯一实现**，S-4 单一实现 / 重复实现条款）。
 *
 * 为什么需要它：`n-input type="password"` 直接落在不同容器里宽度行为完全不一致——
 * - `n-form-item` 内默认 `width:100%` → 在宽面板里被拉成一行超长输入框；
 * - `n-space` 内没有 `flex-basis` → 塌缩到几十像素，32 位随机密码只看得见前几位。
 * 宽度在此处一次性收口（`width` 默认 320px，`max-width:100%` 防窄屏溢出）。
 *
 * 顺带统一了两件易漏的事：明文切换用 `show-password-on="click"`（点眼睛图标，
 * 不做 hover 预览——浏览器原生 hover 会频繁误触发），以及可选的「生成」按钮
 * （生成实现由父组件决定：rclone 面板用 `utils/password.ts` 的 Web Crypto 生成）。
 *
 * 未声明的属性（`maxlength` / `@keyup.enter` / `autocomplete` 等）透传到内部 NInput。
 */
import { NButton, NInput } from 'naive-ui'

// class/style/事件等一律透传给内部 NInput（见模板 v-bind="$attrs"），不落在外层容器上
defineOptions({ inheritAttrs: false })

withDefaults(
  defineProps<{
    placeholder?: string
    disabled?: boolean
    /** 输入框宽度（CSS 值）。默认 320px——32 位随机密码可完整显示。 */
    width?: string
    /** 非空时显示「生成」按钮（点击仅 emit，生成逻辑归父组件）。 */
    generateLabel?: string
    loading?: boolean
    /** 等宽字体（展示混淆值/已生成明文时更易核对）。 */
    mono?: boolean
  }>(),
  { width: '320px' },
)

const model = defineModel<string>({ default: '' })

const emit = defineEmits<{
  generate: []
}>()
</script>

<template>
  <div class="pw-input" :style="{ width }">
    <NInput
      v-model:value="model"
      type="password"
      show-password-on="click"
      :placeholder="placeholder"
      :disabled="disabled"
      :style="mono ? { fontFamily: 'var(--font-family-mono)' } : undefined"
      v-bind="$attrs"
    />
    <NButton
      v-if="generateLabel"
      size="small"
      :loading="loading"
      :disabled="disabled"
      @click="emit('generate')"
    >
      {{ generateLabel }}
    </NButton>
  </div>
</template>

<style scoped>
.pw-input {
  display: flex;
  align-items: center;
  gap: 8px;
  max-width: 100%;
}
/* 输入框吃掉剩余宽度、按钮保持自身宽度；min-width:0 允许它在窄容器里正常收缩 */
.pw-input > :first-child {
  flex: 1 1 auto;
  min-width: 0;
}
</style>
