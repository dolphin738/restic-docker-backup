/** 批次② 过滤条件前端纯函数单测（与后端 exclude_spec.py 同口径）。 */

import { describe, expect, it } from 'vitest'
import {
  anchorPattern,
  emptyExcludeSpec,
  flattenExcludes,
  isValidSize,
  specFromFlatExcludes,
  validateGlob,
} from '@/utils/excludeSpec'

describe('validateGlob', () => {
  it('接受合法 glob', () => {
    for (const p of ['*.tmp', '**/*.log', '/sources/**/*.bak', 'node_modules', 'a?c', '[a-z]*.txt']) {
      expect(validateGlob(p)).toBeNull()
    }
  })

  it('拒绝非法 glob', () => {
    expect(validateGlob('[abc')).toBe('unclosed')
    expect(validateGlob('a[]b')).toBe('emptyClass')
    expect(validateGlob('{a,b}')).toBe('brace')
    expect(validateGlob('   ')).toBe('empty')
  })
})

describe('isValidSize', () => {
  it('合法/非法', () => {
    for (const ok of ['100M', '1g', '2048', '1.5g', '10K']) {
      expect(isValidSize(ok)).toBe(true)
    }
    for (const bad of ['', 'abc', '100MB', '10 M']) {
      expect(isValidSize(bad)).toBe(false)
    }
  })
})

describe('anchorPattern', () => {
  it('展示路径补源根，容器形态与纯 glob 原样', () => {
    expect(anchorPattern('/etc/*.conf', '/sources')).toBe('/sources/etc/*.conf')
    expect(anchorPattern('/sources/etc', '/sources')).toBe('/sources/etc')
    expect(anchorPattern('*.tmp', '/sources')).toBe('*.tmp')
    // host 源根（/）：展示形态即容器形态
    expect(anchorPattern('/etc', '/')).toBe('/etc')
  })
})

describe('flattenExcludes', () => {
  it('quick 展平 + rules 分流', () => {
    const spec = {
      quick: {
        extensions: ['tmp', '.log'],
        nameContains: 'cache',
        largerThan: '100M',
        ifPresent: ['.nobackup'],
        caches: true,
      },
      rules: [
        { pattern: '*.bak', negate: false, icase: false },
        { pattern: '*.TMP', negate: false, icase: true },
        { pattern: 'keep.bak', negate: true, icase: false },
      ],
    }
    const flat = flattenExcludes(spec, '/sources')
    expect(flat.excludes).toEqual(['**/*.tmp', '**/*.log', '**/*cache*', '*.bak', '!keep.bak'])
    expect(flat.iexcludes).toEqual(['*.TMP'])
    expect(flat.largerThan).toBe('100M')
    expect(flat.ifPresent).toEqual(['.nobackup'])
    expect(flat.caches).toBe(true)
  })

  it('锚定规则补源根', () => {
    const spec = {
      quick: { extensions: [], nameContains: '', largerThan: null, ifPresent: [], caches: false },
      rules: [{ pattern: '/etc/*.conf', negate: false, icase: false }],
    }
    expect(flattenExcludes(spec, '/sources').excludes).toEqual(['/sources/etc/*.conf'])
  })
})

describe('specFromFlatExcludes', () => {
  it('负向还原为 negate 规则', () => {
    const spec = specFromFlatExcludes(['*.log', '!keep.log'])
    expect(spec.rules).toEqual([
      { pattern: '*.log', negate: false, icase: false },
      { pattern: 'keep.log', negate: true, icase: false },
    ])
  })

  it('空输入 → 空 spec', () => {
    expect(specFromFlatExcludes([])).toEqual(emptyExcludeSpec())
  })
})
