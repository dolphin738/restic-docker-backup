/** 路由表 + 全局前置守卫（未认证 → /login；403 BOOTSTRAP_REQUIRED → /bootstrap）。 */

import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { fetchMe } from '@/api/auth'
import { hasCode } from '@/api/http'
import { useRuntimeStore } from '@/stores/runtime'
import { useUiStore } from '@/stores/ui'

declare module 'vue-router' {
  interface RouteMeta {
    /** 免鉴权路由（login / bootstrap）。 */
    public?: boolean
    /** 标题对应的 i18n key。 */
    titleKey?: string
  }
}

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/pages/login/index.vue'),
    meta: { public: true, titleKey: 'login.title' },
  },
  {
    path: '/bootstrap',
    name: 'bootstrap',
    component: () => import('@/pages/bootstrap/index.vue'),
    meta: { public: true, titleKey: 'bootstrap.title' },
  },
  {
    path: '/',
    component: () => import('@/layouts/BasicLayout.vue'),
    children: [
      {
        path: '',
        name: 'dashboard',
        component: () => import('@/pages/dashboard/index.vue'),
        meta: { titleKey: 'nav.dashboard' },
      },
      {
        path: 'repo',
        name: 'repo',
        component: () => import('@/pages/repo/index.vue'),
        meta: { titleKey: 'nav.repo' },
      },
      {
        path: 'tasks/new',
        name: 'task-new',
        component: () => import('@/pages/taskWizard/index.vue'),
        meta: { titleKey: 'task.new' },
      },
      {
        path: 'tasks/:id/edit',
        name: 'task-edit',
        component: () => import('@/pages/taskWizard/index.vue'),
        meta: { titleKey: 'task.edit' },
      },
      {
        path: 'jobs',
        name: 'jobs',
        component: () => import('@/pages/jobs/index.vue'),
        meta: { titleKey: 'nav.jobs' },
      },
      {
        path: 'reports',
        name: 'reports',
        component: () => import('@/pages/reports/index.vue'),
        meta: { titleKey: 'nav.reports' },
      },
      {
        path: 'snapshots',
        name: 'snapshots',
        component: () => import('@/pages/snapshots/index.vue'),
        meta: { titleKey: 'nav.snapshots' },
      },
      // 归档（删除保护方案 D13）：只读数据面，与「快照」同级挂在「数据」导航组。
      // 三段分区：仓库归档 / rclone 配置归档 / 保留策略（配置入口在本页，不回设置页）。
      {
        path: 'archive',
        name: 'archive',
        component: () => import('@/pages/archive/index.vue'),
        meta: { titleKey: 'nav.archive' },
      },
      // 2026-10-28：恢复流程（三步：选文件 → 恢复选项 → 执行与结果）并入快照浏览页，
      // 原 `/restore` 入口页与 `/restore/:id/{select-files,options,progress}` 三个向导路由
      // **整体删除**，且**不保留 redirect 兼容**（owner 2026-10-28 裁决：全仓核实无任何
      // 可点击的旧地址引用——文档里只有代码路径与 `/api/restore/*`，唯一的使用方 e2e 探针
      // 已改为 `/snapshots?restore=<id>`）。旧地址现落兜底 → 首页。
      // 恢复流程的唯一深链是新地址 `/snapshots?restore=<id>`（快照页读 query 自动开抽屉）。
      {
        path: 'notifications',
        name: 'notifications',
        component: () => import('@/pages/notifications/index.vue'),
        meta: { titleKey: 'nav.notifications' },
      },
      {
        path: 'settings',
        name: 'settings',
        component: () => import('@/pages/settings/index.vue'),
        meta: { titleKey: 'nav.settings' },
      },
      {
        path: 'audit',
        name: 'audit',
        component: () => import('@/pages/audit/index.vue'),
        meta: { titleKey: 'nav.audit' },
      },
    ],
  },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach(async (to) => {
  if (to.meta.public) {
    return true
  }
  const ui = useUiStore()
  if (ui.me) {
    return true
  }
  try {
    ui.setMe(await fetchMe())
    // 部署形态与浏览根（设计方案 §3.2）：登录后拉一次，供批次③双模式源树取数。
    // init 内部吞异常并保持 docker 缺省，不会因它失败而阻断导航。
    await useRuntimeStore().init()
    return true
  } catch (error) {
    if (hasCode(error, 'UNAUTHORIZED')) {
      return { name: 'login', query: to.fullPath === '/' ? {} : { redirect: to.fullPath } }
    }
    if (hasCode(error, 'BOOTSTRAP_REQUIRED')) {
      return { name: 'bootstrap' }
    }
    // 网络异常 / 5xx：放行让页面自行展示错误与重试，避免与 http 层跳转相互打架
    return true
  }
})
