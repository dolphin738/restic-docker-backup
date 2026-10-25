import { VueQueryPlugin } from '@tanstack/vue-query'
import { createPinia } from 'pinia'
import { createApp, watch } from 'vue'
import App from './App.vue'
import { getSystemTime } from './api/system'
import { setAuthHandlers } from './api/http'
import { i18n } from './i18n'
import { router } from './router'
import { useUiStore } from './stores/ui'
import { fontFamilyMono } from './theme/tokens'
import { setDisplayTimezone } from './utils/datetime'
import './theme/skin.css'

// 等宽字体常量（tokens.ts 单一来源）落成 documentElement 级 CSS 变量：
// 页面/抽屉/弹层等 portal 内容同样可继承——SFC style 内 v-bind() 只作用于组件根元素，
// 对 n-drawer/n-modal 的 portal 子树会断继承（jobs 日志抽屉实证），故不走 v-bind。
document.documentElement.style.setProperty('--font-family-mono', fontFamilyMono)

const app = createApp(App)

app.use(createPinia())

// P5 暗色皮肤钩子：skin.css 的卡片三档阴影暗色值挂在 .rw-dark 下——naive 只暴露组件级
// --n-* 变量、无全局暗色皮肤变量，且 n-modal/n-drawer 的 portal 子树断继承（jobs 日志抽屉实证），
// 故与 --font-family-mono 同模式挂 documentElement。useUiStore 依赖已安装的 pinia，置于此处；
// 初次同步 + 主题切换同步（auto 偏好经 usePreferredDark 同样走 isDark）。
const uiStore = useUiStore()
const syncDarkSkinClass = (): void => {
  document.documentElement.classList.toggle('rw-dark', uiStore.isDark)
}
syncDarkSkinClass()
watch(() => uiStore.isDark, syncDarkSkinClass)
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

// E39 时区统一：拉一次生效时区（config.timezone → settings.tz → UTC）注入展示层，
// 使 jobs/快照/报表等全站时间按「系统设置-时区」渲染，而非跟随浏览器机器时区。
// 失败（未登录/网络异常）静默回落浏览器本地——时区是展示增强，不阻塞启动。
void getSystemTime()
  .then((res) => setDisplayTimezone(res.timezone))
  .catch(() => undefined)

app.mount('#app')
