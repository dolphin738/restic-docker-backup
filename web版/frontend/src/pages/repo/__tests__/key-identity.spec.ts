/**
 * 密钥身份小节（KeyIdentity.vue）——《云端仓库安全基线》§5。
 *
 * 只锁「信息是否被正确传达」的四态：已中性化 / 暴露 / 混合 / 读取失败，以及请求
 * 是否带对 repoId（多仓库切换时不串仓）。布局与配色不在本规格（happy-dom 无 CSS 布局）。
 *
 * 存在理由：中性化是「静默失败、不报错」的设计——若本小节把 unknown 误渲染成
 * 「已中性化」，用户就永远发现不了历史仓库其实还暴露着 root@真实主机名。
 */

import { flushPromises, mount } from '@vue/test-utils'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { NConfigProvider } from 'naive-ui'
import { h } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { i18n } from '@/i18n'
import type { RepoKeyOut, RepoKeysOut } from '@/api/types'
import KeyIdentity from '@/pages/repo/KeyIdentity.vue'
import { lightThemeOverrides } from '@/theme/tokens'

const mocks = vi.hoisted(() => ({
  payload: null as RepoKeysOut | null,
  calls: [] as string[],
}))

vi.mock('@/api/repo', () => ({
  getRepoKeys: vi.fn(async (id: string) => {
    mocks.calls.push(id)
    return mocks.payload
  }),
}))

const NEUTRAL_KEY: RepoKeyOut = {
  id: 'e5f6a7b8',
  id_full: 'e5f6a7b8' + '1'.repeat(56),
  user: 'svc-backup',
  host: 'repo',
  created: '2026-10-23 22:06:48',
  created_tz: 'UTC',
  current: true,
  neutral: true,
}

const REAL_KEY: RepoKeyOut = {
  id: 'a1b2c3d4',
  id_full: 'a1b2c3d4' + '0'.repeat(56),
  user: 'root',
  host: 'Wien-laptop',
  created: '2026-10-23 21:00:00',
  created_tz: 'UTC',
  current: false,
  neutral: false,
}

function makePayload(
  overall: RepoKeysOut['overall'],
  keys: RepoKeyOut[],
  probeError: string | null = null,
): RepoKeysOut {
  return { repo_id: 'r1', overall, keys, probe_error: probeError }
}

async function mountKeys() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const wrapper = mount(NConfigProvider, {
    props: { themeOverrides: lightThemeOverrides },
    slots: { default: () => h(KeyIdentity, { repoId: 'r1' }) },
    global: { plugins: [i18n, [VueQueryPlugin, { queryClient }]] },
  })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  mocks.payload = null
  mocks.calls = []
  i18n.global.locale.value = 'zh-CN'
})

describe('KeyIdentity', () => {
  it('已中性化：显示短 id 与中性身份，且不把完整 id 铺在界面上', async () => {
    mocks.payload = makePayload('neutralized', [NEUTRAL_KEY])
    const wrapper = await mountKeys()

    const text = wrapper.text()
    expect(text).toContain('密钥身份')
    expect(text).toContain('已中性化')
    expect(text).toContain('e5f6a7b8')
    expect(text).toContain('svc-backup @ repo')
    expect(text).toContain('当前')
    expect(text).not.toContain(NEUTRAL_KEY.id_full)
    expect(mocks.calls).toEqual(['r1'])

    wrapper.unmount()
  })

  it('暴露真实身份：给出真实 user@host 与整改提示（不能显示成绿色通过）', async () => {
    mocks.payload = makePayload('exposing', [{ ...REAL_KEY, current: true }])
    const wrapper = await mountKeys()

    const text = wrapper.text()
    expect(text).toContain('1 个密钥暴露真实身份')
    expect(text).toContain('root @ Wien-laptop')
    expect(text).toContain('云端可见')
    expect(text).not.toContain('已中性化')

    wrapper.unmount()
  })

  it('多密钥（混合）：两条都列出，并说明多密钥会跳过中性化', async () => {
    mocks.payload = makePayload('mixed', [NEUTRAL_KEY, REAL_KEY])
    const wrapper = await mountKeys()

    const text = wrapper.text()
    expect(text).toContain('混合（2 个密钥）')
    expect(text).toContain('svc-backup @ repo')
    expect(text).toContain('root @ Wien-laptop')
    expect(text).toContain('多密钥仓库会被跳过')

    wrapper.unmount()
  })

  it('读取失败：显示后端原因，不留空白', async () => {
    mocks.payload = makePayload('unknown', [], 'Fatal: unable to open config file')
    const wrapper = await mountKeys()

    const text = wrapper.text()
    expect(text).toContain('无法读取')
    expect(text).toContain('Fatal: unable to open config file')

    wrapper.unmount()
  })
})
