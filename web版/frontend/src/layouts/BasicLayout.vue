<script setup lang="ts">
import {
  NButton,
  NLayout,
  NLayoutContent,
  NLayoutHeader,
  NLayoutSider,
  NMenu,
  NPopconfirm,
  NRadioButton,
  NRadioGroup,
  NSpace,
  NTag,
  NText,
  useMessage,
  type MenuOption,
} from 'naive-ui'
import { computed, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { logout } from '@/api/auth'
import { useUiStore, type ThemePreference } from '@/stores/ui'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const ui = useUiStore()

// 注册全局 message 出口（http 层错误提示经 ui store 转到这里）
const message = useMessage()
ui.setMessageApi(message)
onUnmounted(() => ui.setMessageApi(null))

const collapsed = ref(false)

const menuOptions = computed<MenuOption[]>(() => [
  { label: t('nav.dashboard'), key: 'dashboard' },
  { label: t('nav.repo'), key: 'repo' },
  { label: t('nav.sources'), key: 'sources' },
  { label: t('nav.policies'), key: 'policies' },
  { label: t('nav.jobs'), key: 'jobs' },
  { label: t('nav.snapshots'), key: 'snapshots' },
  { label: t('nav.restore'), key: 'restore' },
])

const activeKey = computed(() => String(route.name ?? ''))
const currentUser = computed(() => ui.me?.user ?? '-')
const currentMode = computed(() => ui.me?.mode ?? '-')
const currentMount = computed(() => ui.me?.host_mount ?? '-')

function onMenuSelect(key: string | number): void {
  void router.push({ name: String(key) })
}

function onThemeChange(value: string | number): void {
  ui.setThemePreference(String(value) as ThemePreference)
}

function onToggleLocale(): void {
  ui.setLocalePreference(ui.locale === 'zh-CN' ? 'en-US' : 'zh-CN')
}

async function onLogout(): Promise<void> {
  try {
    await logout()
  } catch {
    // 登出失败也要清本地会话态，避免停留在半登录状态
  }
  ui.clearMe()
  void router.replace({ name: 'login' })
}
</script>

<template>
  <n-layout has-sider style="height: 100vh">
    <n-layout-sider
      bordered
      show-trigger
      collapse-mode="width"
      :collapsed-width="64"
      :width="200"
      :native-scrollbar="false"
      v-model:collapsed="collapsed"
    >
      <div class="brand">
        <n-text strong>{{ t('common.appName') }}</n-text>
      </div>
      <n-menu
        :options="menuOptions"
        :value="activeKey"
        :collapsed="collapsed"
        :collapsed-width="64"
        :collapsed-icon-size="20"
        @update:value="onMenuSelect"
      />
    </n-layout-sider>

    <n-layout>
      <n-layout-header bordered class="header">
        <n-space align="center" :size="12" wrap>
          <n-text strong>{{ t(route.meta.titleKey ?? 'common.appName') }}</n-text>
          <n-tag size="small" round type="info">{{ currentUser }}</n-tag>
          <n-tag size="small" round>{{ currentMode }}</n-tag>
          <n-tag size="small" round :type="currentMount === 'rw' ? 'success' : 'warning'">
            {{ currentMount }}
          </n-tag>
        </n-space>

        <n-space align="center" :size="12">
          <n-radio-group size="small" :value="ui.themePreference" @update:value="onThemeChange">
            <n-radio-button value="dark">{{ t('theme.dark') }}</n-radio-button>
            <n-radio-button value="light">{{ t('theme.light') }}</n-radio-button>
            <n-radio-button value="auto">{{ t('theme.auto') }}</n-radio-button>
          </n-radio-group>
          <n-button size="small" quaternary @click="onToggleLocale">
            {{ t(ui.locale === 'zh-CN' ? 'common.languageEn' : 'common.languageZh') }}
          </n-button>
          <n-popconfirm @positive-click="onLogout">
            <template #trigger>
              <n-button size="small" quaternary>{{ t('nav.logout') }}</n-button>
            </template>
            {{ t('nav.logoutConfirm') }}
          </n-popconfirm>
        </n-space>
      </n-layout-header>

      <n-layout-content content-style="padding: 16px;" :native-scrollbar="false">
        <router-view />
      </n-layout-content>
    </n-layout>
  </n-layout>
</template>

<style scoped>
.brand {
  display: flex;
  align-items: center;
  height: 56px;
  padding: 0 16px;
  overflow: hidden;
  white-space: nowrap;
}

.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  height: 56px;
  padding: 0 16px;
}
</style>
