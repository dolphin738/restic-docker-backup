/**
 * 仓库类型下拉单一来源护栏（方案 v1.1 §4.3 F14c / §6.1 R6）。
 *
 * 背景：8 项类型列表原先在 `pages/repo/index.vue` 与
 * `components/task/NewRepoStep.vue` 各手写一份，方案承诺抽成
 * `REPO_TYPE_PRESETS` 单一来源但长期未落地（文档超前于实现）。本用例锁住两件事：
 * - 常量本身 = 8 项、键覆盖 RepoTypeKey 全集、标签键在 zh/en 语言包都存在；
 * - 两个消费方都改为 import REPO_TYPE_PRESETS，源码里不再出现手写的类型列表。
 */

import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'

import { REPO_TYPE_PRESETS } from '@/utils/repoCredentials'
import type { RepoTypeKey } from '@/utils/repoUrl'
import enUS from '@/i18n/locales/en-US'
import zhCN from '@/i18n/locales/zh-CN'

const ALL_TYPE_KEYS: RepoTypeKey[] = ['local', 'rclone', 's3', 'b2', 'sftp', 'azure', 'gcs', 'rest']

function hasKey(node: unknown, dotted: string): boolean {
  let cur: unknown = node
  for (const seg of dotted.split('.')) {
    if (cur === null || typeof cur !== 'object') return false
    cur = (cur as Record<string, unknown>)[seg]
  }
  return cur !== undefined
}

describe('REPO_TYPE_PRESETS 单一来源', () => {
  it('恰好 8 项、顺序固定、键覆盖 RepoTypeKey 全集', () => {
    expect(REPO_TYPE_PRESETS.map((x) => x.key)).toEqual(ALL_TYPE_KEYS)
  })

  it('每个标签键在 zh-CN 与 en-US 语言包都存在', () => {
    for (const { labelKey } of REPO_TYPE_PRESETS) {
      expect(hasKey(zhCN, labelKey), `zh-CN 缺 ${labelKey}`).toBe(true)
      expect(hasKey(enUS, labelKey), `en-US 缺 ${labelKey}`).toBe(true)
    }
  })

  it('两个下拉消费方都 import REPO_TYPE_PRESETS，且不再手写 8 项列表', () => {
    const consumers = [
      'src/pages/repo/index.vue',
      'src/components/task/NewRepoStep.vue',
    ]
    for (const file of consumers) {
      const src = readFileSync(file, 'utf-8')
      expect(src, `${file} 未引用 REPO_TYPE_PRESETS`).toContain('REPO_TYPE_PRESETS')
      expect(src.includes("label: t('repo.typeLocal')"), `${file} 仍在手写类型列表`).toBe(false)
    }
  })
})
