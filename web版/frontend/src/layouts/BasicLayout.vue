<script setup lang="ts">
import { useMediaQuery } from '@vueuse/core'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import {
  NButton,
  NDropdown,
  NDrawer,
  NIcon,
  NLayout,
  NLayoutContent,
  NLayoutHeader,
  NLayoutSider,
  NMenu,
  NSelect,
  NSpace,
  NText,
  NTooltip,
  useDialog,
  useMessage,
  type DropdownOption,
  type MenuOption,
} from 'naive-ui'
import { computed, h, onUnmounted, ref, watch, type Component, type VNodeChild } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { logout } from '@/api/auth'
import { listActiveJobs } from '@/api/jobs'
import { getRepoStatus, listRepos } from '@/api/repo'
import {
  IconAudit,
  IconBell,
  IconCheck,
  IconDashboard,
  IconJobs,
  IconLogout,
  IconMenu,
  IconMonitor,
  IconMoon,
  IconReports,
  IconRepo,
  IconRestore,
  IconSettings,
  IconSnapshots,
  IconSun,
  IconUser,
} from '@/components/icons/icons'
import StatusTag from '@/components/StatusTag.vue'
import logoSrc from '@/assets/restic-logo.png'
import { useUiStore, type ThemePreference } from '@/stores/ui'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const ui = useUiStore()
const queryClient = useQueryClient()

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

/** 九个导航项（key = route.name，不得改名；R-026/Q2 退役备份源/策略；P2 批次 A 增设系统设置 + 审计日志）。 */
const navItems = computed<Record<string, MenuOption>>(() => ({
  dashboard: { label: t('nav.dashboard'), key: 'dashboard', icon: withNIcon(IconDashboard) },
  repo: { label: t('nav.repo'), key: 'repo', icon: withNIcon(IconRepo) },
  jobs: { label: t('nav.jobs'), key: 'jobs', icon: withNIcon(IconJobs) },
  reports: { label: t('nav.reports'), key: 'reports', icon: withNIcon(IconReports) },
  snapshots: { label: t('nav.snapshots'), key: 'snapshots', icon: withNIcon(IconSnapshots) },
  restore: { label: t('nav.restore'), key: 'restore', icon: withNIcon(IconRestore) },
  notifications: { label: t('nav.notifications'), key: 'notifications', icon: withNIcon(IconBell) },
  settings: { label: t('nav.settings'), key: 'settings', icon: withNIcon(IconSettings) },
  audit: { label: t('nav.audit'), key: 'audit', icon: withNIcon(IconAudit) },
}))

/** 展开态四组（设计方案 §4.2：看全局 / 备份跑了没 / 找回数据 / 低频管理）。 */
const groupedMenuOptions = computed<MenuOption[]>(() => [
  {
    type: 'group',
    label: t('nav.group.overview'),
    key: 'group-overview',
    children: [navItems.value.dashboard],
  },
  {
    type: 'group',
    label: t('nav.group.backup'),
    key: 'group-backup',
    children: [navItems.value.jobs, navItems.value.reports],
  },
  {
    type: 'group',
    label: t('nav.group.data'),
    key: 'group-data',
    children: [navItems.value.snapshots, navItems.value.restore],
  },
  {
    type: 'group',
    label: t('nav.group.admin'),
    key: 'group-admin',
    children: [
      navItems.value.repo,
      navItems.value.notifications,
      navItems.value.settings,
      navItems.value.audit,
    ],
  },
])

/** P0 实证结论（设计方案 §10 PV-1 候选 1）：折叠态组标题会以文字残留 64px 窄栏，
 * 故数据源按 collapsed 派生两套——折叠用扁平一维（与已 QA 复验行为逐点一致），展开用四组。 */
const menuOptions = computed<MenuOption[]>(() =>
  collapsed.value ? Object.values(navItems.value) : groupedMenuOptions.value,
)

// 顶栏状态区（设计方案 §4.2-4）：15s 轮询、失败重试 1 次。
// 查询键带 header 后缀 + 当前仓库 id，与页面级查询（['repo','status'] / ['jobs','active']）隔离，
// 避免顶栏轮询改变仪表盘/仓库页既有的 60s 轮询节奏。
const repoStatusQuery = useQuery({
  queryKey: ['repo', 'status', 'header', ui.currentRepoId],
  queryFn: () => getRepoStatus(ui.currentRepoId),
  refetchInterval: 15_000,
  retry: 1,
})

// R-023：仓库切换器数据源（顶栏下拉）。
const reposQuery = useQuery({ queryKey: ['repos'], queryFn: listRepos })
const repoOptions = computed(() =>
  (reposQuery.data.value ?? []).map((repo) => ({ label: repo.name, value: repo.id })),
)
function onRepoSwitch(value: string | number): void {
  ui.setCurrentRepoId(String(value))
  // 切换仓库：所有仓库域查询（快照/任务/状态/仪表盘）带新 X-Repo-Id 重取
  void queryClient.invalidateQueries()
}
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
// 右上角用户菜单标签：root 运行用户不原样展示（容器运行身份属部署事实，
// 界面只给中性身份词；与 TierCard 的 tier.user.* 同口径）。非 root 用户名
// 由部署者自定（如 svc-backup 本就是中性值），原样保留。
const currentUser = computed(() => {
  const user = ui.me?.user
  if (!user) {
    return '-'
  }
  return user === 'root' ? t('tier.user.root') : user
})

function onMenuSelect(key: string | number): void {
  void router.push({ name: String(key) })
}

/** 抽屉内选中：跳转后收起抽屉。 */
function onDrawerMenuSelect(key: string | number): void {
  onMenuSelect(key)
  drawerShow.value = false
}

/** 主题三态控件（设计方案 §4.3 + §10 PV-3 定稿 NDropdown）：图标按钮触发 + 三选项直接选择，
 * 当前值以勾选图标标记；aria-label 带当前值供读屏。 */
const THEME_ICONS: Record<ThemePreference, Component> = {
  dark: IconMoon,
  light: IconSun,
  auto: IconMonitor,
}
/* 图标工厂先于引用它的 computed 声明（审查 💭①）：不依赖 computed 惰性求值躲 TDZ */
const themeCheckIcon: () => VNodeChild = () =>
  h(NIcon, { size: 16 }, { default: () => h(IconCheck) })
const themeOptions = computed<DropdownOption[]>(() =>
  (['dark', 'light', 'auto'] as ThemePreference[]).map((pref) => ({
    label: t(`theme.${pref}`),
    key: pref,
    icon: ui.themePreference === pref ? themeCheckIcon : undefined,
  })),
)
const currentThemeIcon = computed(() => THEME_ICONS[ui.themePreference])
const themeAriaLabel = computed(() =>
  t('header.themeAria', { mode: t(`theme.${ui.themePreference}`) }),
)
function onThemeSelect(key: string | number): void {
  ui.setThemePreference(String(key) as ThemePreference)
}

function onToggleLocale(): void {
  ui.setLocalePreference(ui.locale === 'zh-CN' ? 'en-US' : 'zh-CN')
}

/** 用户菜单（设计方案 §4.3）：登出自独立 NPopconfirm 并入下拉，二次确认走 dialog
 * （App.vue 已挂 NDialogProvider；下拉内不宜再嵌 popconfirm，会与选项点击抢手势）。 */
const dialog = useDialog()
/* 图标工厂先于引用它的 computed 声明（审查 💭①） */
const userLogoutIcon: () => VNodeChild = () =>
  h(NIcon, { size: 16 }, { default: () => h(IconLogout) })
const userMenuOptions = computed<DropdownOption[]>(() => [
  { label: t('nav.logout'), key: 'logout', icon: userLogoutIcon },
])
function onUserMenuSelect(key: string | number): void {
  if (String(key) !== 'logout') {
    return
  }
  dialog.warning({
    title: t('nav.logout'),
    content: t('nav.logoutConfirm'),
    positiveText: t('common.confirm'),
    negativeText: t('common.cancel'),
    onPositiveClick: () => {
      void onLogout()
    },
  })
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
    <!-- 桌面态（≥960px）：常驻可折叠 sider，232px ↔ 64px（P2 起展开态四组导航） -->
    <n-layout-sider
      v-if="!isMobile"
      bordered
      show-trigger
      collapse-mode="width"
      :collapsed-width="64"
      :width="232"
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
        <!-- 左 · 上下文区：页面标题（仓库切换等全局态移右区，§4.3） -->
        <n-space class="header__left" align="center" :size="12" wrap>
          <!-- 移动态菜单入口：汉堡按钮滑出抽屉 -->
          <n-button v-if="isMobile" quaternary size="small" :aria-label="t('header.openMenu')" @click="drawerShow = true">
            <n-icon :size="20"><IconMenu /></n-icon>
          </n-button>
          <n-text strong class="header__title">
            {{ t(route.meta.titleKey ?? 'common.appName') }}
          </n-text>
        </n-space>

        <!-- 右 · 全局区：仓库切换 → 状态 → 主题 → 语言 → 用户（§4.3）
             移动态（<960px）紧凑化：仓库切换器移入抽屉（导航枢纽）、运行中 tag 隐藏
             （仪表盘可见）、间距 12→8、用户按钮只留图标——保证 390px 不撑破 56px 顶栏 -->
        <n-space
          class="header__right"
          :class="{ 'header__right--mobile': isMobile }"
          align="center"
          :size="isMobile ? 8 : 12"
        >
          <span v-if="!isMobile" class="header__repo-switch">
            <n-icon class="header__zone-icon" :size="16"><IconRepo /></n-icon>
            <n-select
              :value="ui.currentRepoId"
              :options="repoOptions"
              size="small"
              class="header__repo-select"
              :placeholder="t('repo.switchPlaceholder')"
              @update:value="onRepoSwitch"
            />
          </span>
          <!-- 状态组（连接态 + 运行中）：收为一个不收缩单元，与左侧仓库切换器清晰分离，
               避免窄顶栏下切换器（n-select 最长 200px）被压缩后贴近状态 pill 造成「两个框重叠」观感 -->
          <span class="header__status-group">
            <!-- 状态 tag：绿/红/灰点 + 文案（P3a 收口为 StatusTag；点取 currentColor 继承语义色） -->
            <n-tooltip v-if="repoStatusTooltip" trigger="hover">
              <template #trigger>
                <StatusTag :kind="repoStatusTagType" :label="repoStatusText" />
              </template>
              {{ repoStatusTooltip }}
            </n-tooltip>
            <StatusTag v-else :kind="repoStatusTagType" :label="repoStatusText" />
            <!-- 运行中任务（P3c 收口：info 态 + 圆点，与全站状态语言统一） -->
            <StatusTag
              v-if="runningJobsCount > 0 && !isMobile"
              kind="info"
              :label="t('header.runningJobs', { n: runningJobsCount })"
            />
          </span>
          <!-- 主题三态：NDropdown（P0 定稿），图标按钮 + 直接选择 + 勾选当前值 -->
          <n-dropdown :options="themeOptions" trigger="click" @select="onThemeSelect">
            <n-button size="small" quaternary :aria-label="themeAriaLabel">
              <n-icon :size="18"><component :is="currentThemeIcon" /></n-icon>
            </n-button>
          </n-dropdown>
          <n-button size="small" quaternary @click="onToggleLocale">
            {{ t(ui.locale === 'zh-CN' ? 'common.languageEn' : 'common.languageZh') }}
          </n-button>
          <!-- 用户菜单：登出并入（dialog 二次确认） -->
          <n-dropdown :options="userMenuOptions" trigger="click" @select="onUserMenuSelect">
            <n-button size="small" quaternary>
              <n-icon :size="16"><IconUser /></n-icon>
              <span class="header__user-name">{{ currentUser }}</span>
            </n-button>
          </n-dropdown>
        </n-space>
      </n-layout-header>

      <n-layout-content content-style="padding: 24px;" :native-scrollbar="false">
        <router-view />
      </n-layout-content>
    </n-layout>

    <!-- 移动态（<960px）：抽屉式菜单（240px，非折叠 → 用分组数据源），标题区为品牌 logo + 应用名。
         仓库切换器随抽屉提供（顶栏 390px 放不下全套全局控件，移入导航枢纽） -->
    <n-drawer v-model:show="drawerShow" placement="left" :width="240">
      <template #header>
        <div class="drawer-brand">
          <img class="brand__logo" :src="logoSrc" :alt="t('common.appName')" />
          <n-text strong>{{ t('common.appName') }}</n-text>
        </div>
      </template>
      <n-select
        v-if="isMobile"
        :value="ui.currentRepoId"
        :options="repoOptions"
        size="small"
        class="drawer__repo-select"
        :placeholder="t('repo.switchPlaceholder')"
        @update:value="onRepoSwitch"
      />
      <n-menu :options="groupedMenuOptions" :value="activeKey" @update:value="onDrawerMenuSelect" />
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

/* 右区全局态：不收缩，极端窄宽下优雅截断而不撑破 56px 顶栏 */
.header__right {
  flex: 0 0 auto;
  min-width: 0;
  overflow: hidden;
}

.header__repo-switch {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex: 0 0 auto;
}

/* 状态组（连接态 + 运行中）：不收缩的稳定单元，与仓库切换器靠 n-space 间距明确分离 */
.header__status-group {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex: 0 0 auto;
  min-width: 0;
}

.header__zone-icon {
  flex: 0 0 auto;
}

.header__repo-select {
  flex: 0 0 auto;
  /* 显式宽度：n-select 根节点是 width:100%，放在 width:auto 的 inline-flex 父级里会循环
     尺寸计算——父 span 收缩为 select 同宽，图标+gap 把 select 向右顶出父级 20px，
     吃掉 n-space 12px 间距后再与状态 tag 重叠 8px（多宽度实测恒定 -8px）。 */
  width: 160px;
}

.header__user-name {
  max-width: 120px;
  margin-left: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 移动态紧凑：用户按钮只留图标（仓库切换器已移入抽屉，运行中 tag 已隐藏） */
.header__right--mobile .header__user-name {
  display: none;
}

/* 抽屉内仓库切换器（移动态导航枢纽） */
.drawer__repo-select {
  margin-bottom: 12px;
}
</style>
