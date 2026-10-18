<script setup lang="ts">
import {
  NConfigProvider,
  NDialogProvider,
  NGlobalStyle,
  NMessageProvider,
  darkTheme,
  dateEnUS,
  dateZhCN,
  enUS as naiveEnUS,
  zhCN as naiveZhCN,
} from 'naive-ui'
import { computed } from 'vue'
import { useUiStore } from '@/stores/ui'
import { darkThemeOverrides, lightThemeOverrides } from '@/theme/tokens'

const ui = useUiStore()

const theme = computed(() => (ui.isDark ? darkTheme : null))
const themeOverrides = computed(() => (ui.isDark ? darkThemeOverrides : lightThemeOverrides))
const naiveLocale = computed(() => (ui.locale === 'en-US' ? naiveEnUS : naiveZhCN))
const naiveDateLocale = computed(() => (ui.locale === 'en-US' ? dateEnUS : dateZhCN))
</script>

<template>
  <n-config-provider
    :theme="theme"
    :theme-overrides="themeOverrides"
    :locale="naiveLocale"
    :date-locale="naiveDateLocale"
    inline-theme-disabled
  >
    <n-global-style />
    <n-message-provider :duration="3000" :max="3">
      <n-dialog-provider>
        <router-view />
      </n-dialog-provider>
    </n-message-provider>
  </n-config-provider>
</template>

<style>
html,
body,
#app {
  height: 100%;
}

#app {
  font-family:
    v-sans, system-ui, -apple-system, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif;
}

/* 全局工具类（UI界面设计方案 §3.1 / §6 / §11 AB-1，P1 随令牌批次落地）：
   .code-text —— 数据 / 路径 / id / cron 的等宽展示块；半透明中性底，亮/暗主题自适应；
   .tabular-nums —— 等宽数字，避免 15s 轮询刷新时数值列跳动。 */
.code-text {
  font-family: var(--font-family-mono);
  background-color: rgba(125, 133, 152, 0.12);
  border: 1px solid rgba(125, 133, 152, 0.28);
  border-radius: 6px;
  padding: 2px 6px;
}

.tabular-nums {
  font-variant-numeric: tabular-nums;
}
</style>
