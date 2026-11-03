/**
 * E31 / E36 扫描范围预览：条目列表 → 目录树（owner 2026-11-03 裁决「B 的汇总栏 + A 的目录树」）。
 *
 * 锁定的语义（每条都对应一次真实踩坑）：
 * - **目录类型取后端 `type`，不靠「有子路径」反推**——真机 /home/123/123 的 261 个目录里
 *   253 个是空的，靠子路径反推会把 13 个文件算成 266 个（本模块第一版的 bug）；
 * - 汇总栏数字只在源子树内统计（源是 /etc 时不能把 / 也算一份）；
 * - 单链压缩只在「子节点也是目录」时发生；
 * - 行类型直接产出 VirtualTree.TreeItem，不另造一套行结构。
 */
import { describe, expect, it } from 'vitest'
import type { DryRunItem } from '@/api/types'
import {
  buildScanTree,
  findScanNode,
  scanScopeStats,
  scanTreePage,
  scanTreeRootPath,
} from '@/components/task/scanScopeTree'

/** 造条目：`d` 前缀表目录（模拟后端给的类型），其余为文件；size 实测恒为 null。 */
function items(...paths: string[]): DryRunItem[] {
  return paths.map((raw) => {
    const isDir = raw.startsWith('d:')
    const path = isDir ? raw.slice(2) : raw
    return { path, display_path: path, type: isDir ? 'dir' : 'file', size: null }
  })
}

const meta = (node: { dirs: number; files: number }): string => `${node.dirs}/${node.files}`

describe('buildScanTree：类型全取后端，不推断', () => {
  it('空目录也被认成目录（没有子路径可反推）', () => {
    // 这正是真机数据：253 个分片目录全空
    const root = buildScanTree(items('d:/repo/data/00', 'd:/repo/data/01', '/repo/config'))
    expect(findScanNode(root, '/repo/data/00')?.type).toBe('dir')
    expect(findScanNode(root, '/repo/config')?.type).toBe('file')
    expect(findScanNode(root, '/repo')?.files).toBe(1)
    expect(findScanNode(root, '/repo')?.dirs).toBe(3) // data、data/00、data/01
  })

  it('中间层按需补齐并默认为目录', () => {
    const root = buildScanTree(items('/a/b/c.txt'))
    expect(findScanNode(root, '/a')?.type).toBe('dir')
    expect(findScanNode(root, '/a/b')?.type).toBe('dir')
    expect(findScanNode(root, '/a/b/c.txt')?.type).toBe('file')
  })

  it('空路径、根路径与重复项被丢弃', () => {
    const root = buildScanTree(items('', '/', '/etc/hosts', '/etc/hosts'))
    expect(root.files).toBe(1)
    expect(scanTreePage(root, '/etc', meta).items).toHaveLength(1)
  })

  it('files / dirs 自底向上汇总', () => {
    const root = buildScanTree(items('d:/a/b', '/a/b/c', '/a/b/d', '/a/e'))
    const a = findScanNode(root, '/a')!
    expect(a.files).toBe(3)
    expect(a.dirs).toBe(1) // b
  })

  it('真机结构对账：13 文件 + 261 目录不被算反', () => {
    // 缩样：2 个空分片目录 + 1 个非空目录 + 1 个文件
    const root = buildScanTree(items('d:/r/data/00', 'd:/r/data/01', 'd:/r/keys', '/r/config'))
    const stats = scanScopeStats(root, ['/r'])
    expect(stats.files).toBe(1)
    expect(stats.dirs).toBe(4) // data、data/00、data/01、keys（不含 /r 自身）
  })
})

describe('scanScopeStats：只在源子树内统计', () => {
  const root = buildScanTree(
    items('d:/etc/nginx', '/etc/hosts', '/etc/nginx/nginx.conf', '/var/log/syslog'),
  )

  it('源是 /etc 时不算 /var，也不把上层 / 重复计入', () => {
    // 目录数不含源根自身：/etc 下只有 nginx 一个子目录
    expect(scanScopeStats(root, ['/etc'])).toEqual({ sources: 1, dirs: 1, files: 2 })
  })

  it('多个源累加；源去重、空串忽略', () => {
    // /etc 贡献 1 子目录（nginx）；/var 贡献 1（log）
    expect(scanScopeStats(root, ['/etc', '/var', '/etc', '  '])).toEqual({
      sources: 2,
      dirs: 2,
      files: 3,
    })
  })
})

describe('scanTreeRootPath：树根收窄', () => {
  it('单源用该源，多源退回 /', () => {
    expect(scanTreeRootPath(['/etc'])).toBe('/etc')
    expect(scanTreeRootPath(['/etc', '/var'])).toBe('/')
    expect(scanTreeRootPath([])).toBe('/')
  })
})

describe('scanTreePage：产出 VirtualTree 行', () => {
  it('单链目录压成一行（data/1e 而不是两层）', () => {
    const root = buildScanTree(items('d:/repo/data/1e', '/repo/data/1e/abc'))
    const rows = scanTreePage(root, '/repo', meta).items
    expect(rows.map((r) => r.name)).toEqual(['data/1e'])
    expect(rows[0]!.key).toBe('/repo/data/1e')
    expect(rows[0]!.type).toBe('dir')
  })

  it('空目录是目录行、可展开，但不能与文件混淆', () => {
    const root = buildScanTree(items('d:/repo/data/00', '/repo/config'))
    const rows = scanTreePage(root, '/repo', meta).items
    const emptyDir = rows.find((r) => r.name === 'data/00')!
    const file = rows.find((r) => r.name === 'config')!
    expect(emptyDir.type).toBe('dir')
    expect(emptyDir.meta).toBe('0/0')
    expect(file.type).toBe('file')
    expect(file.meta).toBeUndefined()
  })

  it('文件名里带斜杠形式的单链不压缩（子节点是文件）', () => {
    const root = buildScanTree(items('d:/a/b', '/a/b/c.txt'))
    const rows = scanTreePage(root, '/a', meta).items
    expect(rows.map((r) => r.name)).toEqual(['b'])
    expect(rows[0]!.type).toBe('dir')
  })

  it('恒不分页：nextCursor 为 null', () => {
    const root = buildScanTree(items('/a/b'))
    expect(scanTreePage(root, '/a', meta).nextCursor).toBeNull()
  })

  it('路径不存在时回空页（树根取不到也不抛）', () => {
    const root = buildScanTree(items('/a/b'))
    expect(scanTreePage(root, '/nope', meta)).toEqual({ items: [], nextCursor: null })
  })
})
