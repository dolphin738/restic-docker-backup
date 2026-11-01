/**
 * 建仓编排顺序护栏（方案 v1.1 §4.3 F14b / §3.1）。
 *
 * 存在理由：这套顺序是硬约束，错了不会报「顺序错」而是报一个看似无关的错误——
 *   ① 密码文件必须早于凭据写入：cred.<id>.* 的加密密钥源自该仓库口令文件，
 *      缺文件时后端抛 CredentialKeyUnavailableError（422 VALIDATION_ERROR）。
 *   ② 切仓必须早于 init 提交：init 作业的目标仓库取自 X-Repo-Id
 *      （backend/app/routers/jobs.py:62 的 Depends(current_repo_id)），
 *      晚切会初始化到**上一个**仓库——真机上是「初始化了别的仓库」的静默事故。
 *   ③ 绝不可用 rebuild 代替 init：services/repo_lifecycle.py 对远程后端直接 422，
 *      且 rebuild 会清空既有快照。
 *
 * 断言只看调用序列，不依赖实现细节（内部函数名可改，顺序不可改）。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const calls: string[] = []

vi.mock('@/api/repo', () => ({
  createRepo: vi.fn(async (body: { id?: string }) => {
    calls.push('createRepo')
    return { id: body.id ?? 'r261103-abc123', name: 'x' }
  }),
  setRepoPassword: vi.fn(async () => {
    calls.push('setRepoPassword')
    return { ok: true, generated: true, password: 'GEN-PW', path: '/data/repos/x/password' }
  }),
  testRepo: vi.fn(async () => {
    calls.push('testRepo')
    return { ok: true, latency_ms: 5 }
  }),
  // 反向断言用：本编排绝不应触碰 rebuild
  rebuildRepo: vi.fn(async () => {
    calls.push('rebuildRepo')
    return { ok: true, generated: false, password: null, path: '' }
  }),
}))

vi.mock('@/api/config', () => ({
  putConfig: vi.fn(async () => {
    calls.push('putConfig')
    return { ok: true }
  }),
}))

vi.mock('@/api/jobs', () => ({
  createJob: vi.fn(async (type: string) => {
    calls.push(`createJob:${type}`)
    return { id: 'job-init-1' }
  }),
}))

const { useRepoProvisioning } = await import('@/composables/useRepoProvisioning')

function makeDeps() {
  const switched: string[] = []
  return {
    switched,
    deps: {
      setCurrentRepoId: (id: string) => {
        calls.push('setCurrentRepoId')
        switched.push(id)
      },
    },
  }
}

describe('useRepoProvisioning 建仓编排', () => {
  beforeEach(() => {
    calls.length = 0
  })

  it('全链路顺序：createRepo → setRepoPassword → putConfig → 切仓 → init → testRepo', async () => {
    const { deps } = makeDeps()
    const p = useRepoProvisioning(deps)
    await p.provision({
      id: '',
      name: '任务仓库',
      url: '/srv/repo-new',
      hostTag: null,
      type: 's3',
      credInput: { aws_access_key_id: 'AK', aws_secret_access_key: 'SK' },
    })
    expect(calls).toEqual([
      'createRepo',
      'setRepoPassword',
      'putConfig',
      'setCurrentRepoId',
      'createJob:init',
      'testRepo',
    ])
  })

  it('绝不走 rebuild（远程后端 rebuild 必 422，且会清空既有快照）', async () => {
    const { deps } = makeDeps()
    const p = useRepoProvisioning(deps)
    await p.provision({ id: '', name: 'x', url: null, hostTag: null, type: 'local', credInput: {} })
    expect(calls).not.toContain('rebuildRepo')
    expect(calls).toContain('createJob:init')
  })

  it('切仓在 init 提交之前（init 按 X-Repo-Id 定目标，晚切会初始化到上一个仓库）', async () => {
    const { deps, switched } = makeDeps()
    const p = useRepoProvisioning(deps)
    await p.provision({ id: '', name: 'x', url: null, hostTag: null, type: 'local', credInput: {} })
    const switchAt = calls.indexOf('setCurrentRepoId')
    const initAt = calls.indexOf('createJob:init')
    expect(switchAt).toBeGreaterThan(-1)
    expect(initAt).toBeGreaterThan(switchAt)
    // 切过去的正是新建出来的仓库
    expect(switched).toEqual(['r261103-abc123'])
  })

  it('无凭据的类型不写 cred 键（putConfig 不被无意义调用）', async () => {
    const { deps } = makeDeps()
    const p = useRepoProvisioning(deps)
    await p.provision({ id: '', name: 'x', url: '/srv/r', hostTag: null, type: 'local', credInput: {} })
    expect(calls).not.toContain('putConfig')
  })

  it('凭据键按 cred.{repoId}.{suffix} 命名空间化（R-023）', async () => {
    const { putConfig } = await import('@/api/config')
    const { deps } = makeDeps()
    const p = useRepoProvisioning(deps)
    await p.provision({
      id: 'myrepo',
      name: 'x',
      url: 's3:h/b',
      hostTag: null,
      type: 's3',
      credInput: { aws_access_key_id: 'AK', aws_default_region: '' },
    })
    expect(putConfig).toHaveBeenCalledWith({ 'cred.myrepo.aws_access_key_id': 'AK' })
  })

  it('服务端生成的密码只在返回值里，不写入任何存储', async () => {
    const { deps } = makeDeps()
    const p = useRepoProvisioning(deps)
    const result = await p.provision({ id: '', name: 'x', url: null, hostTag: null, type: 'local', credInput: {} })
    expect(result.generatedPassword).toBe('GEN-PW')
    // 红线 S3：明文不得进 localStorage
    expect(Object.keys(localStorage).filter((k) => (localStorage.getItem(k) ?? '').includes('GEN-PW'))).toEqual([])
  })
})
