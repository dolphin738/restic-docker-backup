<script setup lang="ts">
/** 统一密码输入框（**项目唯一实现**，S-4 单一实现 / 重复实现条款）。
 *
 * 为什么需要它：`n-input type="password"` 直接落在不同容器里宽度行为完全不一致——
 * - `n-form-item` 内默认 `width:100%` → 在宽面板里被拉成一行超长输入框；
 * - `n-space` 内没有 `flex-basis` → 塌缩到几十像素，32 位随机密码只看得见前几位。
 * 宽度在此处一次性收口（`width` 默认 320px，`max-width:100%` 防窄屏溢出）。
 *
 * 顺带统一了两件易漏的事：明文切换用 `show-password-on="click"`（点眼睛图标，
 * 不做 hover 预览——浏览器原生 hover 会频繁误触发），以及可选的「生成」按钮——
 * 全站唯一形态 = 输入框右侧、secondary small、盾牌图标（2026-11-03 owner 裁决，
 * 取原「配置迁移导出口令」按钮的样式（该入口已于 2026-11-08 阶段 ② 删除，样式沿用
 * 到「全站备份 / 恢复」等落点）；曾短暂改为框内后缀后废弃。生成实现由父组件
 * 决定：rclone 面板用 `utils/password.ts`，仓库密码走后端 secrets 生成）。
 *
 * 未声明的属性（`maxlength` / `@keyup.enter` / `autocomplete` 等）透传到内部 NInput。
 */
import { NButton, NIcon, NInput } from 'naive-ui'

import { IconShieldCheck } from '@/components/icons/icons'

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

/** 点击只 emit generate；事件可能缺省（测试直发 click），stopPropagation 防宿主行为误触发。 */
function onGenerate(e?: Event): void {
  e?.stopPropagation()
  emit('generate')
}
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
    <!-- 统一「生成密码」按钮（全站唯一形态，2026-11-03 owner 裁决：样式取自当时
         「配置迁移导出口令」按钮——输入框右侧、secondary small、盾牌图标；该入口已于
         2026-11-08 阶段 ② 删除，样式沿用。此前框内后缀方案废弃）。 -->
    <NButton
      v-if="generateLabel"
      size="small"
      secondary
      :loading="loading"
      :disabled="disabled"
      @click="onGenerate($event)"
    >
      <template #icon>
        <NIcon :size="16"><IconShieldCheck /></NIcon>
      </template>
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
