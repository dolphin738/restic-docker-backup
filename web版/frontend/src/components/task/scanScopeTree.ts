/** E31 / E36 dry-run **条目列表** → 可渲染的目录树（本文件的唯一职责）。
 *
 * 关键事实（决定实现，别再想当然）：
 *   ① 后端 `DryRunItem` 已带 `type`（`dir` / `file`）。restic `--verbose` 用**路径尾斜杠**
 *      标记目录（`"item":"/sources/etc/nginx/"`），解析层归一后随条目下发。
 *   ② **不能靠「有子路径」反推目录**：备份仓库的分片目录（`data/00`…）绝大多数是空目录，
 *      没有子路径可反推。真机实测 /home/123/123：261 个目录里 253 个是空的，
 *      按子路径反推会把它们全算成文件（13 个文件显示成 266 个）——这是本文件的第一版 bug。
 *   ③ 条目**不带 size**，故树不显示文件大小。
 *
 * 产出直接对齐 `VirtualTree` 的 `TreeItem` / `TreePage`：图标、展开、虚拟滚动、
 * 加载态/空态全部复用该组件，本文件只做数据形状转换。
 *
 * 纯函数、零 IO、不依赖 i18n 实例（行末 meta 文案由 ScanScopePreview 拼）。
 */
import type { TreeItem, TreePage } from '@/components/VirtualTree.vue'
import type { DryRunItem } from '@/api/types'

/** 树节点。`own` = 该路径本身在条目列表里出现过。 */
export interface ScanNode {
  /** 单段名；虚拟根为 '/'。 */
  name: string
  /** 展示形态绝对路径（与条目同空间）。 */
  path: string
  /** 后端明确告知的类型（不推断）。 */
  type: 'dir' | 'file'
  children: ScanNode[]
  /** 子树内文件条目数（自身是文件时计 1）。 */
  files: number
  /** 子树内目录数（不含自身）。 */
  dirs: number
}

export interface ScanScopeStats {
  sources: number
  dirs: number
  files: number
}

function emptyNode(name: string, path: string): ScanNode {
  return { name, path, type: 'dir', children: [], files: 0, dirs: 0 }
}

/** 条目列表 → 树。空路径、'/' 自身与重复项丢弃。
 *
 * 中间层按需补齐并**默认当目录**（RESTIC 只吐它扫描到的条目；路径中出现过的中间层
 * 一定是目录，否则不会有更深的路径）。末节点类型取后端给的 `type`。 */
export function buildScanTree(items: DryRunItem[]): ScanNode {
  const root = emptyNode('/', '/')
  const index = new Map<string, ScanNode>([['/', root]])
  const seen = new Set<string>()

  for (const item of items) {
    const path = item.display_path
    if (!path || path === '/' || seen.has(path)) {
      continue
    }
    seen.add(path)
    const parts = path.split('/').filter(Boolean)
    let cursor = root
    let acc = ''
    for (let i = 0; i < parts.length; i += 1) {
      acc += '/' + parts[i]!
      let next = index.get(acc)
      if (!next) {
        next = emptyNode(parts[i]!, acc)
        index.set(acc, next)
        cursor.children.push(next)
      }
      if (i === parts.length - 1 && item.type === 'file') {
        next.type = 'file'
      }
      cursor = next
    }
  }

  tally(root)
  return root
}

/** 自底向上汇总 files / dirs（一次后序遍历）。 */
function tally(node: ScanNode): void {
  if (node.children.length === 0) {
    node.files = node.type === 'file' ? 1 : 0
    node.dirs = 0
    return
  }
  let files = 0
  let dirs = 0
  for (const child of node.children) {
    tally(child)
    files += child.files
    dirs += child.dirs + (child.type === 'dir' ? 1 : 0)
  }
  node.files = files
  node.dirs = dirs
}

/** 按展示形态路径取节点；路径不存在返回 null。 */
export function findScanNode(root: ScanNode, path: string): ScanNode | null {
  if (!path || path === '/') {
    return root
  }
  let cursor: ScanNode | undefined = root
  for (const part of path.split('/').filter(Boolean)) {
    cursor = cursor?.children.find((child) => child.name === part)
    if (!cursor) {
      return null
    }
  }
  return cursor ?? null
}

function uniqueSources(sources: string[]): string[] {
  return [...new Set(sources.filter((source) => source.trim() !== ''))]
}

/** 备份源对应的子树根（源不在条目里时退回整棵树的根）。 */
export function scopeRoots(root: ScanNode, sources: string[]): ScanNode[] {
  const nodes: ScanNode[] = []
  for (const source of uniqueSources(sources)) {
    const node = findScanNode(root, source)
    if (node && !nodes.includes(node)) {
      nodes.push(node)
    }
  }
  return nodes.length > 0 ? nodes : [root]
}

/** 汇总栏数字：源数取去重后的非空源；目录/文件只在**源子树内**统计
 *  （从 '/' 起算会把源之外的层级也算进来——源是 /etc 时会与 '/' 同数）。
 *
 * 目录数**不含源根本身**，与 `find <源> -mindepth 1 -type d | wc -l` 同口径
 * （owner 对账时给的就是这个数）。 */
export function scanScopeStats(root: ScanNode, sources: string[]): ScanScopeStats {
  const roots = scopeRoots(root, sources)
  let dirs = 0
  let files = 0
  for (const node of roots) {
    files += node.files
    dirs += node.dirs
  }
  return { sources: uniqueSources(sources).length, dirs, files }
}

/** 树根：单源直接用该源（树里不出现无关的顶层目录），多源退回 '/'。 */
export function scanTreeRootPath(sources: string[]): string {
  const unique = uniqueSources(sources)
  return unique.length === 1 ? unique[0]! : '/'
}

/** 单链压缩：连续「只有一个子目录」的中间层并成一行（`data/1e` 而非两行）。 */
function compressChain(node: ScanNode): { node: ScanNode; label: string } {
  let cursor = node
  let label = node.name
  while (
    cursor.type === 'dir' &&
    cursor.children.length === 1 &&
    cursor.children[0]!.type === 'dir'
  ) {
    cursor = cursor.children[0]!
    label += '/' + cursor.name
  }
  return { node: cursor, label }
}

/** 取某一层的直接子项（`VirtualTree` 的 load-children 适配点）。
 *
 * 不分页：条目上限 DRY_RUN_MAX_ITEMS = 5000 且全量已在内存里，虚拟滚动只渲染可见行，
 * 故 `nextCursor` 恒为 null。meta 由调用方拼（本文件不碰 i18n）。
 */
export function scanTreePage(
  root: ScanNode,
  path: string,
  meta: (node: { dirs: number; files: number }) => string,
): TreePage {
  const node = findScanNode(root, path)
  if (!node) {
    return { items: [], nextCursor: null }
  }
  const items: TreeItem[] = node.children.map((child) => {
    const { node: deep, label } = compressChain(child)
    const isDir = deep.type === 'dir'
    return {
      key: deep.path,
      name: label,
      type: deep.type,
      path: deep.path,
      meta: isDir ? meta({ dirs: deep.dirs, files: deep.files }) : undefined,
    }
  })
  return { items, nextCursor: null }
}
