<script lang="ts">
/** 固定行高常量：虚拟滚动的几何基准（owner 2026-10-24：32 → 36，对齐 Duplicati 树行尺寸）。
 *  注意：仅本组件使用（曾预期快照列表复用，实际无外部消费方）。 */
export const VIRTUAL_TREE_ROW_HEIGHT = 36

/** 树节点（路径为展示形态原路径语义）。 */
export interface TreeItem {
  key: string
  name: string
  /** dir | file | other */
  type: string
  path: string
  /** 是否为容器挂载点（E05/E33 标注；供行末徽章）。 */
  is_mountpoint?: boolean
  /** 行末元信息（可选，如快照文件树的 `size · mtime`）——调用方拼好文案传入，
   *  本组件不解析、不格式化；未传即无该区（E05 / E33 三处消费方零影响）。 */
  meta?: string
}

/** 单层加载结果；nextCursor 为服务端给的不透明游标，本组件只透传。 */
export interface TreePage {
  items: TreeItem[]
  nextCursor: string | null
}
</script>

<script setup lang="ts">
/** 目录树（Duplicati file-tree 视觉复刻 + 名称点击统一切换，owner 2026-10-24）。
 *
 * 行结构：[caret][folder/file 图标][名称][挂载徽章]——**无复选框、无行末按钮**，
 * 视觉对齐 Duplicati 截图：行点击统一切换备份成员资格（emit select，父组件裁决）。
 *   - highlightPaths（备份源，展示形态）：自身或祖先命中 → 蓝底高亮，
 *     选一个目录作源即「整棵子树显示为选中」；
 *   - excludedPaths（排除规则，展示形态）：自身或祖先命中 → 置灰 + 删除线，
 *     后代同样置灰（前缀匹配，无需展开加载）——「未选中」的持久视觉；
 *   - 展开/收起始终由 caret 触发；点名称在 selectDir 模式下 emit select；
 *   - 高度：固定 height（仓库/快照树）或**内容驱动**（源树、恢复树：行数×行高，
 *     min/maxHeight 夹住；曾用 height:100%+ResizeObserver，百分比链在 flex 自动
 *     高度容器里不可靠——盒子被撑高出现死空白，已废）。
 */
import { NButton, NIcon, NSpin } from 'naive-ui'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  IconChevronDown,
  IconChevronRight,
  IconFile,
  IconFolder,
  IconFolderOpen,
  IconServer,
} from '@/components/icons/icons'

const ROW_HEIGHT = VIRTUAL_TREE_ROW_HEIGHT
const OVERSCAN = 5

const props = defineProps<{
  /** 根目录（展示形态；首次挂载即加载该层直接子项）。 */
  rootPath: string
  /** 单层加载器：调用方适配 E05 / E18 / E33；一次请求只取一层直接子项，游标只透传。 */
  loadChildren: (path: string, cursor?: string) => Promise<TreePage>
  height?: number
  /** auto 模式（未传 height 时）：视口高度 = 内容行数 × 行高，并用 min/maxHeight 夹住。 */
  minHeight?: number
  maxHeight?: number
  /** true：点目录名 = 选中（emit select）并同步展开；false（默认）：点目录名只展开。
   *  展开/收起**始终**由行首三角形触发，避免与选中手势互相打架。 */
  selectDir?: boolean
  /** 高亮行（当前选中/生效路径）。 */
  selectedPath?: string | null
  /** 多选态：命中集合的行打勾样式（B1 文件级恢复；仅样式，不改点击语义）。 */
  selectedPaths?: Set<string> | null
  /** 高亮路径（备份源，展示形态）：**自身或祖先命中即高亮**——选一个目录作源，
   *  其整个子树视觉上都是「已选中」（owner 2026-10-24：点名称切换加入/取消）。
   *  Duplicati 实为蓝底高亮（非复选框），本 prop 复刻该视觉。 */
  highlightPaths?: Set<string> | null
  /** 已排除路径（展示形态，与行 path 同空间）：命中行及其**后代**全部置灰。 */
  excludedPaths?: Set<string> | null
}>()

const emit = defineEmits<{
  (e: 'select', item: TreeItem): void
}>()

const { t } = useI18n()

interface DirState {
  items: TreeItem[]
  nextCursor: string | null
  loading: boolean
  loadingMore: boolean
  error: string
}

interface Row {
  key: string
  kind: 'item' | 'more'
  item: TreeItem
  depth: number
  /** kind=more 时为待续传的目录路径。 */
  dirPath?: string
}

const dirStates = reactive(new Map<string, DirState>())
const expanded = ref(new Set<string>())
const scrollTop = ref(0)

// ---- 视口高度：固定值，或内容驱动（行数 × 行高，min/max 夹住） ----
const viewportHeight = computed(() => {
  if (props.height !== undefined) {
    return props.height
  }
  const content = rows.value.length * ROW_HEIGHT
  const floor = props.minHeight ?? 0
  const cap = props.maxHeight ?? Number.POSITIVE_INFINITY
  return Math.min(Math.max(content, floor), cap)
})

function isDir(item: TreeItem): boolean {
  return item.type === 'dir'
}

function errorTextOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

async function loadDir(path: string): Promise<void> {
  if (dirStates.has(path)) {
    return
  }
  dirStates.set(path, { items: [], nextCursor: null, loading: true, loadingMore: false, error: '' })
  try {
    const page = await props.loadChildren(path)
    const state = dirStates.get(path)
    if (state) {
      state.items = page.items
      state.nextCursor = page.nextCursor
    }
  } catch (error) {
    const state = dirStates.get(path)
    if (state) {
      state.error = errorTextOf(error)
    }
  } finally {
    const state = dirStates.get(path)
    if (state) {
      state.loading = false
    }
  }
}

/** 加载同一目录的下一页：cursor 原样透传，不解析不自造。 */
async function loadMore(dirPath: string | undefined): Promise<void> {
  if (!dirPath) {
    return
  }
  const state = dirStates.get(dirPath)
  if (!state || !state.nextCursor || state.loadingMore) {
    return
  }
  state.loadingMore = true
  try {
    const page = await props.loadChildren(dirPath, state.nextCursor)
    const current = dirStates.get(dirPath)
    if (current) {
      current.items = current.items.concat(page.items)
      current.nextCursor = page.nextCursor
    }
  } catch (error) {
    const current = dirStates.get(dirPath)
    if (current) {
      current.error = errorTextOf(error)
    }
  } finally {
    const current = dirStates.get(dirPath)
    if (current) {
      current.loadingMore = false
    }
  }
}

/** 展开/收起：由行首三角形触发，点体表（名称）不再折叠。 */
function toggle(item: TreeItem): void {
  if (!isDir(item)) {
    return
  }
  const next = new Set(expanded.value)
  if (next.has(item.path)) {
    next.delete(item.path)
  } else {
    next.add(item.path)
    void loadDir(item.path)
  }
  expanded.value = next
}

function onRowClick(row: Row): void {
  if (row.kind !== 'item') {
    return
  }
  const item = row.item
  // 文件永远可选中；目录仅在 selectDir 模式下可选中（默认保持"点行=展开"的旧行为）
  if (isDir(item) && !props.selectDir) {
    toggle(item)
    return
  }
  emit('select', item)
}

// ---- 排除三态（Duplicati 语义）：展示形态路径直接比对 ----

type ExcludeState = 'included' | 'excluded' | 'partial'

/** 行的排除态：自身/祖先命中 = excluded（置灰删除线）；后代命中（前缀）= partial；否则 included。 */
function excludeStateOf(path: string): ExcludeState {
  const set = props.excludedPaths
  if (!set || set.size === 0) {
    return 'included'
  }
  let cursor = path
  for (;;) {
    if (set.has(cursor)) {
      return 'excluded'
    }
    const idx = cursor.lastIndexOf('/')
    if (idx <= 0) {
      break
    }
    cursor = cursor.slice(0, idx)
  }
  for (const ex of set) {
    if (ex.startsWith(`${path}/`)) {
      return 'partial'
    }
  }
  return 'included'
}

/** 行末点击之外的全部手势收口：名称点击 = 切换备份成员资格（加入源 / 取消 / 排除），
 *  由父组件（SourcesPicker）按 sources 与排除规则现状裁决，本组件只透传 select。 */

/** 高亮态：自身或祖先命中备份源集合（选目录作源 → 整棵子树视觉已选中）。 */
function isHighlighted(path: string): boolean {
  const set = props.highlightPaths
  if (!set || set.size === 0) {
    return false
  }
  let cursor = path
  for (;;) {
    if (set.has(cursor)) {
      return true
    }
    const idx = cursor.lastIndexOf('/')
    if (idx <= 0) {
      break
    }
    cursor = cursor.slice(0, idx)
  }
  return false
}

const rows = computed<Row[]>(() => {
  const out: Row[] = []
  const walk = (path: string, depth: number): void => {
    const state = dirStates.get(path)
    if (!state) {
      return
    }
    for (const item of state.items) {
      out.push({ key: item.key, kind: 'item', item, depth })
      if (isDir(item) && expanded.value.has(item.path)) {
        walk(item.path, depth + 1)
        const child = dirStates.get(item.path)
        if (child && (child.nextCursor || child.loadingMore)) {
          out.push({ key: `more:${item.path}`, kind: 'more', item, depth: depth + 1, dirPath: item.path })
        }
      }
    }
  }
  walk(props.rootPath, 0)
  return out
})

interface VisibleRow extends Row {
  top: number
  loadingMore: boolean
}

const visibleRows = computed<VisibleRow[]>(() => {
  const start = Math.max(0, Math.floor(scrollTop.value / ROW_HEIGHT) - OVERSCAN)
  const count = Math.ceil(viewportHeight.value / ROW_HEIGHT) + OVERSCAN * 2
  return rows.value.slice(start, start + count).map((row, index) => ({
    ...row,
    top: (start + index) * ROW_HEIGHT,
    loadingMore: row.dirPath ? dirStates.get(row.dirPath)?.loadingMore ?? false : false,
  }))
})

function onScroll(event: Event): void {
  scrollTop.value = (event.target as HTMLElement).scrollTop
}

const rootState = computed(() => dirStates.get(props.rootPath))

function resetAll(): void {
  dirStates.clear()
  expanded.value = new Set()
  scrollTop.value = 0
  void loadDir(props.rootPath)
}

onMounted(() => {
  void loadDir(props.rootPath)
})

watch(
  () => props.rootPath,
  () => resetAll(),
)
</script>

<template>
  <div class="vtree">
    <div v-if="rootState?.loading" class="vtree__loading">
      <n-spin size="small" />
    </div>
    <template v-else-if="rootState?.error">
      <div class="vtree__error">{{ rootState.error }}</div>
    </template>
    <div
      v-else
      class="vtree__viewport"
      :style="{ height: `${viewportHeight}px` }"
      @scroll="onScroll"
    >
      <div class="vtree__spacer" :style="{ height: `${rows.length * ROW_HEIGHT}px` }">
        <div
          v-for="row in visibleRows"
          :key="row.key"
          class="vtree__row"
          :class="{
            'vtree__row--dir': row.kind === 'item' && isDir(row.item),
            'vtree__row--selected':
              row.kind === 'item' && !!selectedPath && row.item.path === selectedPath,
            'vtree__row--checked':
              row.kind === 'item' && !!selectedPaths && selectedPaths.has(row.item.path),
            'vtree__row--highlight':
              row.kind === 'item' && isHighlighted(row.item.path),
            'vtree__row--excluded':
              row.kind === 'item' && excludeStateOf(row.item.path) === 'excluded',
          }"
          :style="{
            top: `${row.top}px`,
            height: `${ROW_HEIGHT}px`,
            paddingLeft: `${row.depth * 18 + 10}px`,
          }"
          @click.stop="onRowClick(row)"
        >
          <template v-if="row.kind === 'item'">
            <span
              class="vtree__caret"
              :class="{ 'vtree__caret--open': expanded.has(row.item.path) }"
              @click.stop="toggle(row.item)"
            >
              <n-icon v-if="isDir(row.item)" :size="14">
                <IconChevronDown v-if="expanded.has(row.item.path)" />
                <IconChevronRight v-else />
              </n-icon>
            </span>
            <n-icon class="vtree__type" :size="16">
              <IconFolderOpen v-if="isDir(row.item) && expanded.has(row.item.path)" />
              <IconFolder v-else-if="isDir(row.item)" />
              <IconFile v-else />
            </n-icon>
            <span class="vtree__name">{{ row.item.name }}</span>
            <span v-if="row.item.meta" class="vtree__meta">{{ row.item.meta }}</span>
            <n-icon
              v-if="row.item.is_mountpoint"
              class="vtree__mount"
              :size="14"
              :title="t('tree.mountpoint')"
            >
              <IconServer />
            </n-icon>
          </template>
          <n-button v-else size="small" quaternary :loading="row.loadingMore" @click.stop="loadMore(row.dirPath)">
            {{ t('tree.loadMore') }}
          </n-button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 样式纪律（UI全量改造方案 §4.6.4 彻底档 / P5）：颜色/间距/圆角/缓动一律走
   theme/skin.css 令牌（--rw-*，亮暗双套），本文件零硬编码色值；
   混色一律 color-mix 派生（比例不是色值）。原手写 rgba(...) 已全量替换——
   旧值在暗色模式下失效（如高亮 rgba(61,91,217,.16) 在暗底上几乎不可见）。 */

.vtree__viewport {
  position: relative;
  overflow-y: auto;
  border: 1px solid var(--rw-border);
  border-radius: var(--rw-radius-control);
  background: var(--rw-surface);
}

.vtree__row {
  position: absolute;
  left: 0;
  right: 0;
  display: flex;
  align-items: center;
  gap: var(--rw-space-1);
  cursor: pointer;
  white-space: nowrap;
  user-select: none;
  font-size: 13px;
  transition: background-color 0.15s var(--rw-ease);
}

.vtree__row:hover {
  background: var(--rw-surface-hover);
}

.vtree__row--dir .vtree__name {
  font-weight: 600;
}

/* 备份源高亮：focus 16% 混 surface（亮/暗各一套，随 .rw-dark 自动切换） */
.vtree__row--highlight,
.vtree__row--highlight:hover {
  background: color-mix(in srgb, var(--rw-focus) 16%, var(--rw-surface));
}

/* 单选态（selectedPath，如仓库/快照浏览的「当前项」）与多选态（selectedPaths，
   如恢复向导的文件勾选）此前只挂了类名、**没有任何样式规则**——点了毫无反馈，
   用户会误以为不支持选择。此处补齐（2026-10-26 owner 反馈）。
   多选态额外加一条左侧竖线，使「已勾选」在长列表里可一眼扫到；颜色全部取
   --rw-focus 令牌 + color-mix 派生，零硬编码色值（亮暗自动适配）。 */
.vtree__row--selected,
.vtree__row--selected:hover {
  background: color-mix(in srgb, var(--rw-focus) 12%, var(--rw-surface));
}

.vtree__row--checked,
.vtree__row--checked:hover {
  background: color-mix(in srgb, var(--rw-focus) 16%, var(--rw-surface));
  box-shadow: inset 2px 0 0 var(--rw-focus);
}

/* 排除态优先于高亮：border 45% 混 surface 的中性灰底 */
.vtree__row--excluded,
.vtree__row--excluded:hover {
  background: color-mix(in srgb, var(--rw-border) 45%, var(--rw-surface));
}

.vtree__row--excluded .vtree__name {
  opacity: 0.45;
  text-decoration: line-through;
}

.vtree__row--excluded .vtree__type {
  opacity: 0.45;
}

.vtree__caret {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  color: color-mix(in srgb, var(--rw-focus) 35%, var(--rw-border));
  cursor: pointer;
}

.vtree__caret--open {
  color: var(--rw-focus);
}

/* 目录图标走 focus 主色（Duplicati 的文件夹即主色），文件图标降一档 */
.vtree__type {
  flex: none;
  color: var(--rw-focus);
}

.vtree__row:not(.vtree__row--dir) .vtree__type {
  color: color-mix(in srgb, var(--rw-focus) 45%, var(--rw-border));
}

/* 名称区撑满行内剩余宽度：名字文字 + 后面空白同属一个点击热区
   （行级 click 已覆盖，flex 撑满让 hover/选中视觉连续，对齐 Duplicati 文本行） */
.vtree__name {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 行末元信息（可选）：等宽 + 降一档（opacity 继承行文本色，亮暗自适应，零硬编码色值）。
   名称区已 flex:1 撑满，故 meta 天然贴行末；窄屏由名称区 ellipsis 让位。 */
.vtree__meta {
  flex: none;
  margin-left: var(--rw-space-2);
  font-family: var(--font-family-mono);
  font-size: 12px;
  opacity: 0.65;
}

/* 挂载点徽章（行末小图标，说明该目录是独立挂载点） */
.vtree__mount {
  flex: none;
  margin-left: var(--rw-space-2);
  color: color-mix(in srgb, var(--rw-focus) 35%, var(--rw-border));
}

.vtree__loading {
  display: flex;
  align-items: center;
  justify-content: center;
  height: calc(var(--rw-space-6) * 4);
}

.vtree__error {
  padding: var(--rw-space-3);
  color: var(--rw-danger);
}
</style>
