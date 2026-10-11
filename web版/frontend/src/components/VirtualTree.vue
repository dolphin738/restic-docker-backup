<script lang="ts">
/** 固定行高常量：虚拟滚动的几何基准，快照列表等场景复用同一值，避免行高漂移。 */
export const VIRTUAL_TREE_ROW_HEIGHT = 32

/** 树节点（路径为展示形态原路径语义）。 */
export interface TreeItem {
  key: string
  name: string
  /** dir | file | other */
  type: string
  path: string
}

/** 单层加载结果；nextCursor 为服务端给的不透明游标，本组件只透传。 */
export interface TreePage {
  items: TreeItem[]
  nextCursor: string | null
}
</script>

<script setup lang="ts">
import { NButton, NSpin } from 'naive-ui'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

const ROW_HEIGHT = VIRTUAL_TREE_ROW_HEIGHT
const OVERSCAN = 5
const DEFAULT_HEIGHT = 360

const props = defineProps<{
  /** 根目录（展示形态；首次挂载即加载该层直接子项）。 */
  rootPath: string
  /** 单层加载器：调用方适配 E05 / E18 / E33；一次请求只取一层直接子项，游标只透传。 */
  loadChildren: (path: string, cursor?: string) => Promise<TreePage>
  height?: number
  /** true：点目录名 = 选中（emit select）并同步展开；false（默认）：点目录名只展开。
   *  展开/收起**始终**由行首三角形触发，避免与选中手势互相打架。 */
  selectDir?: boolean
  /** 高亮行（当前选中/生效路径）。 */
  selectedPath?: string | null
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

const viewportHeight = computed(() => props.height ?? DEFAULT_HEIGHT)

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
          }"
          :style="{
            top: `${row.top}px`,
            height: `${ROW_HEIGHT}px`,
            paddingLeft: `${row.depth * 16 + 8}px`,
          }"
        >
          <template v-if="row.kind === 'item'">
            <span class="vtree__caret" @click.stop="toggle(row.item)">
              {{ isDir(row.item) ? (expanded.has(row.item.path) ? '▾' : '▸') : '' }}
            </span>
            <span class="vtree__name" @click.stop="onRowClick(row)">{{ row.item.name }}</span>
          </template>
          <n-button v-else size="tiny" quaternary :loading="row.loadingMore" @click.stop="loadMore(row.dirPath)">
            {{ t('tree.loadMore') }}
          </n-button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.vtree__viewport {
  position: relative;
  overflow-y: auto;
  border: 1px solid rgba(128, 128, 128, 0.24);
  border-radius: 4px;
}

.vtree__row {
  position: absolute;
  left: 0;
  right: 0;
  display: flex;
  align-items: center;
  gap: 4px;
  cursor: pointer;
  white-space: nowrap;
  user-select: none;
}

.vtree__row--dir .vtree__name {
  font-weight: 600;
}

.vtree__caret {
  display: inline-block;
  width: 14px;
  color: rgba(128, 128, 128, 0.8);
  cursor: pointer;
  pointer-events: none;
}

/* 只有目录行的三角形可点（全局开启后文件行仍不可点） */
.vtree__row--dir .vtree__caret {
  pointer-events: auto;
}

.vtree__caret:hover {
  color: var(--n-primary-color, rgba(128, 128, 128, 0.9));
}

.vtree__row--selected {
  background: rgba(0, 128, 0, 0.12);
}

.vtree__name {
  overflow: hidden;
  text-overflow: ellipsis;
}

.vtree__loading {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 120px;
}

.vtree__error {
  padding: 12px;
  color: rgba(255, 99, 99, 0.9);
}
</style>
