/** 路由表 + 全局前置守卫（未认证 → /login；403 BOOTSTRAP_REQUIRED → /bootstrap）。 */

import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { fetchMe } from '@/api/auth'
import { hasCode } from '@/api/http'
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
        component: () => import('@/pages/PlaceholderPage.vue'),
        meta: { titleKey: 'nav.repo' },
      },
      {
        path: 'sources',
        name: 'sources',
        component: () => import('@/pages/PlaceholderPage.vue'),
        meta: { titleKey: 'nav.sources' },
      },
      {
        path: 'policies',
        name: 'policies',
        component: () => import('@/pages/PlaceholderPage.vue'),
        meta: { titleKey: 'nav.policies' },
      },
      {
        path: 'jobs',
        name: 'jobs',
        component: () => import('@/pages/PlaceholderPage.vue'),
        meta: { titleKey: 'nav.jobs' },
      },
      {
        path: 'snapshots',
        name: 'snapshots',
        component: () => import('@/pages/PlaceholderPage.vue'),
        meta: { titleKey: 'nav.snapshots' },
      },
      {
        path: 'restore',
        name: 'restore',
        component: () => import('@/pages/PlaceholderPage.vue'),
        meta: { titleKey: 'nav.restore' },
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
