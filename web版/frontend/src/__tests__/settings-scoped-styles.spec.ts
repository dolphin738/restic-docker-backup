/**
 * 设置页分区样式归属护栏（候选 6 样式解耦）。
 *
 * 背景：拆分区后每个 SectionXxx.vue 是独立 scoped 样式域。Vue 的 scoped 属性
 * **不穿透到子组件模板**——父页写的 `.settings__form[data-v-父]` 匹配不到子组件
 * 渲染的节点，规则静默失效（无报错、无测试变红，只有界面上悄悄变宽）。
 *
 * 本用例守两件事：
 *   ① 分区用到的每个 .settings__* 类，都能在「自己组件的 scoped 块」或
 *      「theme/settings-panel.css（全局共用）」里找到定义；
 *   ② 父页 index.vue 不再定义任何分区专用的 .settings__* 规则。
 */

import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

const ROOT = join(process.cwd(), 'src')
const SECTIONS_DIR = join(ROOT, 'pages/settings/sections')
const INDEX = join(ROOT, 'pages/settings/index.vue')
const SHARED_CSS = join(ROOT, 'theme/settings-panel.css')

const SECTION_FILES = [
  'SectionBehavior.vue',
  'SectionCredential.vue',
  'SectionDump.vue',
  'SectionMetrics.vue',
  'SectionPassword.vue',
  'SectionSiteBackup.vue',
  'SectionSystem.vue',
  'SectionTimezone.vue',
  'SectionTransfer.vue',
]

/** 抽出某文件的 <style> 块（没有则空串）。 */
function styleBlock(source: string): string {
  const m = source.match(/<style[^>]*>([\s\S]*?)<\/style>/)
  return m ? m[1] : ''
}

/** 模板段（<template>…</template>）。 */
function templateBlock(source: string): string {
  const m = source.match(/<template>([\s\S]*)<\/template>/)
  return m ? m[1] : ''
}

/** 模板里用到的 .settings__* 类名集合。 */
function usedClasses(tpl: string): Set<string> {
  const out = new Set<string>()
  for (const m of tpl.matchAll(/class="([^"]*)"/g)) {
    for (const cls of m[1].split(/\s+/)) {
      if (cls.startsWith('settings__')) out.add(cls)
    }
  }
  return out
}

/** 样式块里定义的类名集合。 */
function definedClasses(rawCss: string): Set<string> {
  // 先剥注释：注释里提到 .settings__panel 这类名字不应算作「定义」
  const css = rawCss.replace(/\/\*[\s\S]*?\*\//g, '')
  const out = new Set<string>()
  for (const m of css.matchAll(/\.(settings__[A-Za-z0-9_-]+)/g)) out.add(m[1])
  return out
}

const sharedDefined = definedClasses(readFileSync(SHARED_CSS, 'utf-8'))

describe('设置页分区样式归属', () => {
  it('每个分区用到的 .settings__* 类都有本地或共用定义', () => {
    const problems: string[] = []
    for (const file of SECTION_FILES) {
      const src = readFileSync(join(SECTIONS_DIR, file), 'utf-8')
      const used = usedClasses(templateBlock(src))
      const local = definedClasses(styleBlock(src))
      for (const cls of used) {
        if (!local.has(cls) && !sharedDefined.has(cls)) {
          problems.push(`${file}: .${cls} 用了但既无本地 scoped 规则也无共用定义`)
        }
      }
    }
    expect(problems, problems.join('\n')).toEqual([])
  })

  it('父页 index.vue 不再定义分区专用样式（只保留外壳）', () => {
    const css = styleBlock(readFileSync(INDEX, 'utf-8'))
    const defined = [...definedClasses(css)]
    const allowed = new Set(['settings__segments'])
    const offenders = defined.filter((c) => !allowed.has(c))
    expect(offenders, `index.vue 仍定义: ${offenders.join(', ')}`).toEqual([])
  })

  it('共用面板样式在全局文件里（.settings__panel 不留在任何 scoped 块）', () => {
    expect(sharedDefined.has('settings__panel')).toBe(true)
    for (const file of SECTION_FILES) {
      const css = styleBlock(readFileSync(join(SECTIONS_DIR, file), 'utf-8'))
      expect(definedClasses(css).has('settings__panel'), `${file} 不应重复定义 panel`).toBe(false)
    }
  })
})
