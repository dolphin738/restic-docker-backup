import { VueQueryPlugin } from '@tanstack/vue-query'
import { createPinia } from 'pinia'
import { createApp } from 'vue'
import App from './App.vue'
import { setAuthHandlers } from './api/http'
import { i18n } from './i18n'
import { router } from './router'
import { useUiStore } from './stores/ui'
import { fontFamilyMono } from './theme/tokens'

// 等宽字体常量（tokens.ts 单一来源）落成 documentElement 级 CSS 变量：
// 页面/抽屉/弹层等 portal 内容同样可继承——SFC style 内 v-bind() 只作用于组件根元素，
// 对 n-drawer/n-modal 的 portal 子树会断继承（jobs 日志抽屉实证），故不走 v-bind。
document.documentElement.style.setProperty('--font-family-mono', fontFamilyMono)

const app = createApp(App)

app.use(createPinia())
app.use(router)
app.use(i18n)
app.use(VueQueryPlugin, {
  queryClientConfig: {
    defaultOptions: {
      queries: {
        retry: 1,
        staleTime: 10_000,
        refetchOnWindowFocus: false,
      },
    },
  },
})

// http 层只发信号，跳转由这里统一执行：避免 http 反向 import router 形成循环依赖
setAuthHandlers({
  onUnauthorized: () => {
    if (router.currentRoute.value.name === 'login') {
      return
    }
    useUiStore().clearMe()
    void router.replace({ name: 'login', query: { redirect: router.currentRoute.value.fullPath } })
  },
  onBootstrapRequired: () => {
    if (router.currentRoute.value.name === 'bootstrap') {
      return
    }
    useUiStore().clearMe()
    void router.replace({ name: 'bootstrap' })
  },
})

app.mount('#app')
