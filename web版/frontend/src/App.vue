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

const ui = useUiStore()

const theme = computed(() => (ui.isDark ? darkTheme : null))
const naiveLocale = computed(() => (ui.locale === 'en-US' ? naiveEnUS : naiveZhCN))
const naiveDateLocale = computed(() => (ui.locale === 'en-US' ? dateEnUS : dateZhCN))
</script>

<template>
  <n-config-provider
    :theme="theme"
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
</style>
