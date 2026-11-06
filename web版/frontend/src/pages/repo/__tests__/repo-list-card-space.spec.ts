/**
 * 仓库列表卡 · n-space 子节点 key 复用回归（2026-11-06）。
 *
 * 存在理由：naive-ui 2.45.3 的 `Space` 把每个插槽 item 包成
 * `createElementBlock("div", { key: 1, ... })`（`naive-ui/es/space/src/Space.mjs:129-131`，
 * key 恒为 1 而不是索引）。子节点数量一变，Vue 的 keyed diff 就撞 key —— dev 控制台报
 * `[Vue warn]: Duplicate keys found during update: 1`，最坏情况是复用错节点
 * （rclone 加密卡片「停用后多出按钮」就是这个机制造成的真故障）。
 *
 * 修法：给 n-space 关掉 wrapper（`:wrap-item="false"`），走 Fragment(key=0) 分支，
 * 间距由 style 上的 `gap` 承担。仓库列表首屏 1 个子节点、数据回来后变 N+2 个，
 * 必定触发一次 keyed diff，所以这里锁住「wrapper 关掉了」这一事实。
 *
 * ⚠️ 为什么不直接断言 DOM 结构：wrapper 分支的开关是
 * `!wrapItem && (useGap || internalUseGap)`，而 useGap 来自 `ensureSupportFlexGap()`
 * ——happy-dom 里它探到的 flex-gap 支持是 false，于是即使 wrap-item=false 也仍走
 * wrapper 分支，DOM 断言在单测里恒定通过（变异测试实测：删掉 :wrap-item 仍 2 passed）。
 * 真实行为只能在浏览器里验（已在真机确认 Duplicate keys 从 2 条降为 0 条），
 * 单测这层只负责防止属性被误删。
 */

import { flushPromises, mount } from '@vue/test-utils'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { NSpace, NConfigProvider } from 'naive-ui'
import { defineComponent, h } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { i18n } from '@/i18n'
import type { RepoOut } from '@/api/types'
import RepoListCard from '@/pages/repo/RepoListCard.vue'

vi.mock('@/api/repo', () => ({
  getRepoStatus: vi.fn(async () => ({ connected: true, failure_kind: null })),
}))

function repo(id: string, name: string): RepoOut {
  return {
    id,
    name,
    type: 'local',
    url: `/repo/${id}`,
    host_tag: null,
    url_source: 'db',
    host_tag_source: 'db',
    passfile_status: 'configured',
    created_at: '2026-10-01T00:00:00Z',
  } as RepoOut
}

function mountCard(repos: RepoOut[]) {
  const Host = defineComponent({
    setup: () => () =>
      h(RepoListCard, { repos, currentRepoId: 'default', initJobMap: {}, initTargetId: null }),
  })
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return mount(NConfigProvider, {
    slots: { default: () => h(Host) },
    global: { plugins: [i18n, [VueQueryPlugin, { queryClient }]] },
  })
}

beforeEach(() => {
  i18n.global.locale.value = 'zh-CN'
})

describe('RepoListCard · n-space 子节点 key 复用', () => {
  it('列表容器的 n-space 关闭 wrapper，避免 N 个子节点共用 key=1', async () => {
    const wrapper = mountCard([repo('default', '默认仓库'), repo('r2', '备份二号')])
    await flushPromises()

    const spaces = wrapper.findAllComponents(NSpace)
    expect(spaces.length).toBeGreaterThan(0)
    expect(spaces[0].props('wrapItem')).toBe(false)

    // 渲染没被改坏：两个仓库行都在
    expect(wrapper.findAll('.repo__dest')).toHaveLength(2)

    wrapper.unmount()
  })
})
