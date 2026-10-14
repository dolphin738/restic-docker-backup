<script setup lang="ts">
import { useMediaQuery } from '@vueuse/core'
import { useQuery } from '@tanstack/vue-query'
import {
  NButton,
  NDrawer,
  NIcon,
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
  NTooltip,
  useMessage,
  type MenuOption,
} from 'naive-ui'
import { computed, h, onUnmounted, ref, watch, type Component, type VNodeChild } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { logout } from '@/api/auth'
import { listActiveJobs } from '@/api/jobs'
import { getRepoStatus } from '@/api/repo'
import {
  IconAudit,
  IconBell,
  IconDashboard,
  IconJobs,
  IconMenu,
  IconRepo,
  IconRestore,
  IconSettings,
  IconSnapshots,
} from '@/components/icons/MenuIcons'
import logoSrc from '@/assets/restic-logo.png'
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

// Duplicati 式响应式断点（设计方案 §4.2-3）：<960px 收抽屉，≥960px 常驻可折叠
const isMobile = useMediaQuery('(max-width: 959px)')
const drawerShow = ref(false)
// 跨断点（如拖窗放大）时收起抽屉，避免桌面态残留浮层
watch(isMobile, (mobile) => {
  if (!mobile) {
    drawerShow.value = false
  }
})

/** 把内联 SVG 图标包进 NIcon：统一尺寸，颜色随文字色（currentColor）继承。 */
function withNIcon(icon: Component): () => VNodeChild {
  return () => h(NIcon, null, { default: () => h(icon) })
}

/** 菜单项（R-026/Q2 退役备份源/策略页；P2 批次 A 增设系统设置 + 审计日志，共 8 项）。 */
const menuOptions = computed<MenuOption[]>(() => [
  { label: t('nav.dashboard'), key: 'dashboard', icon: withNIcon(IconDashboard) },
  { label: t('nav.repo'), key: 'repo', icon: withNIcon(IconRepo) },
  { label: t('nav.jobs'), key: 'jobs', icon: withNIcon(IconJobs) },
  { label: t('nav.snapshots'), key: 'snapshots', icon: withNIcon(IconSnapshots) },
  { label: t('nav.restore'), key: 'restore', icon: withNIcon(IconRestore) },
  { label: t('nav.notifications'), key: 'notifications', icon: withNIcon(IconBell) },
  { label: t('nav.settings'), key: 'settings', icon: withNIcon(IconSettings) },
  { label: t('nav.audit'), key: 'audit', icon: withNIcon(IconAudit) },
])

// 顶栏状态区（设计方案 §4.2-4）：15s 轮询、失败重试 1 次。
// 查询键带 header 后缀，与页面级查询（['repo','status'] / ['jobs','active']）隔离，
// 避免顶栏轮询改变仪表盘/仓库页既有的 60s 轮询节奏。
const repoStatusQuery = useQuery({
  queryKey: ['repo', 'status', 'header'],
  queryFn: getRepoStatus,
  refetchInterval: 15_000,
  retry: 1,
})
const activeJobsQuery = useQuery({
  queryKey: ['jobs', 'active', 'header'],
  queryFn: () => listActiveJobs(20),
  refetchInterval: 15_000,
  retry: 1,
})

type RepoStatusState = 'connected' | 'disconnected' | 'unknown'

/** 查询失败或尚未取到数据 → 中性 unknown；不抛出未捕获错误。 */
const repoStatusState = computed<RepoStatusState>(() => {
  if (repoStatusQuery.error.value) {
    return 'unknown'
  }
  const data = repoStatusQuery.data.value
  if (!data) {
    return 'unknown'
  }
  return data.connected ? 'connected' : 'disconnected'
})

const repoStatusTagType = computed<'success' | 'error' | 'default'>(() => {
  switch (repoStatusState.value) {
    case 'connected':
      return 'success'
    case 'disconnected':
      return 'error'
    default:
      return 'default'
  }
})

const repoStatusText = computed(() => t(`header.repoStatus.${repoStatusState.value}`))

// QA Nit 3 + QA 意见：未连接时透出后端 last_error（服务端原文，无需翻译）；
// 已连接/未知态无 tooltip，避免噪声。
const repoStatusTooltip = computed<string>(() => {
  if (repoStatusState.value === 'disconnected') {
    return repoStatusQuery.data.value?.last_error ?? ''
  }
  return ''
})

// listActiveJobs 服务端已按 running,pending 过滤，total 即二者合计
const runningJobsCount = computed(() => activeJobsQuery.data.value?.total ?? 0)

const activeKey = computed(() => String(route.name ?? ''))
const currentUser = computed(() => ui.me?.user ?? '-')
const currentMode = computed(() => ui.me?.mode ?? '-')
const currentMount = computed(() => ui.me?.host_mount ?? '-')

function onMenuSelect(key: string | number): void {
  void router.push({ name: String(key) })
}

/** 抽屉内选中：跳转后收起抽屉。 */
function onDrawerMenuSelect(key: string | number): void {
  onMenuSelect(key)
  drawerShow.value = false
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
    <!-- 桌面态（≥960px）：常驻可折叠 sider，200px ↔ 64px -->
    <n-layout-sider
      v-if="!isMobile"
      bordered
      show-trigger
      collapse-mode="width"
      :collapsed-width="64"
      :width="200"
      :native-scrollbar="false"
      v-model:collapsed="collapsed"
    >
      <div class="brand" :class="{ 'brand--collapsed': collapsed }">
        <img class="brand__logo" :src="logoSrc" :alt="t('common.appName')" />
        <n-text v-show="!collapsed" strong>{{ t('common.appName') }}</n-text>
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
        <n-space class="header__left" align="center" :size="12" wrap>
          <!-- 移动态菜单入口：汉堡按钮滑出抽屉 -->
          <n-button v-if="isMobile" quaternary size="small" @click="drawerShow = true">
            <n-icon :size="20"><IconMenu /></n-icon>
          </n-button>
          <n-text strong class="header__title">
            {{ t(route.meta.titleKey ?? 'common.appName') }}
          </n-text>
          <n-tag size="small" round type="info">{{ currentUser }}</n-tag>
          <n-tag size="small" round>{{ currentMode }}</n-tag>
          <n-tag size="small" round :type="currentMount === 'rw' ? 'success' : 'warning'">
            {{ currentMount }}
          </n-tag>
          <n-tooltip v-if="repoStatusTooltip" trigger="hover">
            <template #trigger>
              <n-tag size="small" round :type="repoStatusTagType">{{ repoStatusText }}</n-tag>
            </template>
            {{ repoStatusTooltip }}
          </n-tooltip>
          <n-tag v-else size="small" round :type="repoStatusTagType">{{ repoStatusText }}</n-tag>
          <n-tag v-if="runningJobsCount > 0" size="small" round type="info">
            {{ t('header.runningJobs', { n: runningJobsCount }) }}
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

      <n-layout-content content-style="padding: 20px;" :native-scrollbar="false">
        <router-view />
      </n-layout-content>
    </n-layout>

    <!-- 移动态（<960px）：抽屉式菜单，标题区为品牌 logo + 应用名 -->
    <n-drawer v-model:show="drawerShow" placement="left" :width="240">
      <template #header>
        <div class="drawer-brand">
          <img class="brand__logo" :src="logoSrc" :alt="t('common.appName')" />
          <n-text strong>{{ t('common.appName') }}</n-text>
        </div>
      </template>
      <n-menu :options="menuOptions" :value="activeKey" @update:value="onDrawerMenuSelect" />
    </n-drawer>
  </n-layout>
</template>

<style scoped>
.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 56px;
  padding: 0 16px;
  overflow: hidden;
  white-space: nowrap;
}

.brand--collapsed {
  justify-content: center;
  padding: 0;
}

.brand__logo {
  flex-shrink: 0;
  width: 40px;
  height: 40px;
}

.drawer-brand {
  display: flex;
  align-items: center;
  gap: 10px;
}

.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  height: 56px;
  padding: 0 16px;
}

/* <960px 顶栏拥挤（设计方案 §4.2-3）：左组可收缩，标题文字省略 */
.header__left {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
}

.header__title {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
