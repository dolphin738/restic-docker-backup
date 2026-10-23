/** 部署形态与浏览根（设计方案 §3.2 单一事实源）。
 *
 * 与 ui store 的 `me` 同属「环境/会话事实」，不是仓库/任务/快照类业务数据
 * （后者仍一律走 vue-query）。登录后由路由守卫拉取一次并缓存；批次③ 的双模式
 * 源树据此决定树根（docker→/sources、host→/）与需隐藏的 deny 区域。
 */

import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getRuntime } from '@/api/system'

export const useRuntimeStore = defineStore('runtime', () => {
  /** 部署形态：docker / host。缺省 docker——取数失败时宁可窄（/sources）也不误开全盘。 */
  const deployMode = ref<'docker' | 'host'>('docker')
  /** 源树浏览根（唯一取数来源，前端不再写死 /sources）。 */
  const browseRoot = ref('/sources')
  const sourcesRoot = ref('/sources')
  const restoreRoot = ref('/restore')
  /** host 模式下不可选作备份源的前缀（仓库存储位置 / 应用数据 / 伪目录）。 */
  const denyPrefixes = ref<string[]>([])
  const loaded = ref(false)

  /** 拉取一次并缓存；失败保持 docker 缺省且不置 loaded（下次导航重试），不阻塞页面。 */
  async function init(): Promise<void> {
    if (loaded.value) return
    try {
      const runtime = await getRuntime()
      deployMode.value = runtime.deploy_mode
      browseRoot.value = runtime.browse_root
      sourcesRoot.value = runtime.sources_root
      restoreRoot.value = runtime.restore_root
      denyPrefixes.value = runtime.deny_prefixes
      loaded.value = true
    } catch {
      // 网络/鉴权异常：保留 docker 缺省，交由页面自身错误态处理（此处弹窗会打断导航）
    }
  }

  return {
    deployMode,
    browseRoot,
    sourcesRoot,
    restoreRoot,
    denyPrefixes,
    loaded,
    init,
  }
})
