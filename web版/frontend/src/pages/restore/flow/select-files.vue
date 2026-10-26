<script setup lang="ts">
/** 恢复向导步骤 1：select-files（选快照 + 选文件）。
 *
 * 从 `flow/index.vue` 拆出（P10/P11 遗留项，2026-10-23 补做）。拆分口径：
 *  - **状态归属父级**：snapshotId / selectedFiles 由编排器持有（确认门预览、
 *    提交请求都要用），本组件只通过 props/emits 读写；
 *  - **局部自容**：文件搜索（B3 契约）与文件树 loader 只服务本步骤，
 *    连同 debounce 定时器一起内聚于此，拆走即整块搬走、无残留耦合。
 *
 * B3 前端契约：debounce ≥300ms 且关键词 ≥2 字符才发请求——后端虽有
 * 「快照+关键词」30s 缓存，前端仍须防逐键触发。
 */

import { NAlert, NButton, NIcon, NInput, NSelect, NSpace, NSpin, NText } from 'naive-ui'
import { computed, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { listSnapshotFiles, searchSnapshotFiles } from '@/api/snapshots'
import type { SnapshotFileOut } from '@/api/types'
import VirtualTree, { type TreeItem, type TreePage } from '@/components/VirtualTree.vue'
import { IconX } from '@/components/icons/icons'
import { restoreFileTreeMaxHeight, restoreFileTreeMinHeight } from '@/theme/tokens'

const props = defineProps<{
  /** 当前选中快照 id；null = 未选（列表加载中或空列表）。 */
  snapshotId: string | null
  /** 版本下拉选项（时间 · size · paths 复合文案，由父级组装）。 */
  snapshotOptions: Array<{ label: string; value: string }>
  /** 快照列表拉取中（下拉 loading）。 */
  snapshotsLoading: boolean
  /** 快照列表拉取失败文案；null = 无错误。 */
  snapshotsError: string | null
  /** 已选文件/目录集合（与文件树、搜索命中项共用；父级持有）。 */
  selectedFiles: Set<string>
}>()

const emit = defineEmits<{
  (e: 'update:snapshotId', value: string | null): void
  (e: 'toggle-file', path: string): void
  /** 从「已选清单」移除单条（owner 2026-10-26：清单项可逐条取消）。 */
  (e: 'remove-file', path: string): void
  /** 清空全部已选（回到「恢复整个快照」）。 */
  (e: 'clear-files'): void
  (e: 'next'): void
  (e: 'back'): void
}>()

const { t } = useI18n()

/** 已选清单（排序后展示，避免勾选顺序随点击抖动；Set 无重复天然去重）。 */
const selectedList = computed(() => [...props.selectedFiles].sort((a, b) => a.localeCompare(b)))

// ---------------------------------------------------------------- 文件搜索（B3 契约）

const SEARCH_DEBOUNCE_MS = 300
const SEARCH_MIN_QUERY = 2

const searchInput = ref('')
const searchHits = ref<SnapshotFileOut[]>([])
const searchTotal = ref<number | null>(null)
const searching = ref(false)
let searchTimer: ReturnType<typeof setTimeout> | null = null

async function runSearch(keyword: string): Promise<void> {
  const id = props.snapshotId
  if (!id) {
    searching.value = false
    return
  }
  try {
    const data = await searchSnapshotFiles(id, keyword)
    searchHits.value = data.items
    searchTotal.value = data.total
  } catch {
    // 失败即清空结果区（错误提示走全局消息，不在此吞掉语义）
    searchHits.value = []
    searchTotal.value = null
  } finally {
    searching.value = false
  }
}

watch(searchInput, (value) => {
  if (searchTimer) {
    clearTimeout(searchTimer)
    searchTimer = null
  }
  const keyword = value.trim()
  if (keyword.length < SEARCH_MIN_QUERY) {
    // 未达触发门槛：清空结果且不显示 spinner（避免"刚输 1 个字符就转圈"）
    searchHits.value = []
    searchTotal.value = null
    searching.value = false
    return
  }
  searching.value = true
  searchTimer = setTimeout(() => void runSearch(keyword), SEARCH_DEBOUNCE_MS)
})

/** 换快照即清空搜索：命中是快照内语义，跨快照不可复用。 */
watch(
  () => props.snapshotId,
  () => {
    searchInput.value = ''
    searchHits.value = []
    searchTotal.value = null
  },
)

/** 命中项与文件树共用同一套勾选集合：key 取 display_path（与 treeLoader 口径一致）。 */
function toggleHit(hit: SnapshotFileOut): void {
  emit('toggle-file', hit.display_path)
}

onUnmounted(() => {
  if (searchTimer) {
    clearTimeout(searchTimer)
    searchTimer = null
  }
})

// ---------------------------------------------------------------- 文件树多选（B1，E18）

// 树视口高度走 VirtualTree 的 auto 模式（行数 × 行高，min/max 夹住）——
// owner 2026-10-26：原固定 520px 在快照根只有两三项时留下大片死空白，
// 把下方「已选清单」推出首屏。详见 theme/tokens 的 restoreFileTree* 注释。

function treeLoader(path: string, cursor?: string): Promise<TreePage> {
  const id = props.snapshotId ?? ''
  return listSnapshotFiles(id, path, cursor).then((data) => ({
    items: data.items.map((item) => ({
      key: item.path,
      name: item.name,
      type: item.type,
      path: item.display_path,
    })),
    nextCursor: data.next_cursor,
  }))
}

function onFileSelect(item: TreeItem): void {
  emit('toggle-file', item.path)
}

function onSnapshotChange(value: string | null): void {
  emit('update:snapshotId', value)
}
</script>

<template>
  <n-select
    :value="snapshotId"
    :options="snapshotOptions"
    :loading="snapshotsLoading"
    :placeholder="t('restore.pickPlaceholder')"
    filterable
    @update:value="onSnapshotChange"
  />
  <n-alert v-if="snapshotsError" type="error" :show-icon="false">
    {{ snapshotsError }}
  </n-alert>

  <!-- P10 步骤重组：目标模式与目标目录已移至 options（步骤 2）的「恢复到哪」分区，
       覆盖策略移至「已存在文件」分区——步骤 1 只负责「选快照 + 选文件」，
       对齐 Duplicati 的 select-files / options 划分。 -->

  <!-- B1：文件级多选（可选）；不选 = 整快照 -->
  <n-space vertical :size="6">
    <n-text strong>{{ t('restore.filesTitle') }}</n-text>
    <n-text depth="3">{{ t('restore.filesHint') }}</n-text>

    <!-- P10 搜索框（R6 / B3 契约）：debounce 300ms + 关键词 ≥2 字符才发请求；
         命中靠服务端过滤（V-3），前端绝不拉全量再过滤。 -->
    <n-input
      v-model:value="searchInput"
      class="restore__search"
      :placeholder="t('restore.search.placeholder')"
      clearable
      size="small"
    >
      <template #suffix>
        <n-spin v-if="searching" size="small" />
      </template>
    </n-input>
    <n-text v-if="searchTotal !== null" depth="2">
      {{ t('restore.search.resultCount', { n: searchTotal }) }}
    </n-text>
    <n-space v-if="searchHits.length" vertical :size="4">
      <n-text depth="3">{{ t('restore.search.hint') }}</n-text>
      <div
        v-for="hit in searchHits"
        :key="hit.path"
        class="restore__hit"
        :class="{ 'is-selected': selectedFiles.has(hit.display_path) }"
        @click="toggleHit(hit)"
      >
        {{ hit.display_path }}
      </div>
    </n-space>

    <VirtualTree
      v-if="snapshotId"
      :key="`files-${snapshotId}`"
      root-path="/"
      :load-children="treeLoader"
      :min-height="restoreFileTreeMinHeight"
      :max-height="restoreFileTreeMaxHeight"
      select-dir
      :selected-paths="selectedFiles"
      @select="onFileSelect"
    />
    <n-text v-if="selectedFiles.size > 0" depth="2">
      {{ t('restore.filesSelected', { n: selectedFiles.size }) }}
    </n-text>
    <n-text v-else depth="3">{{ t('restore.filesWhole') }}</n-text>

    <!-- 已选清单（owner 2026-10-26）：不只报数量，还要把「到底选了哪些」列出来，
         支持逐条取消与一键清空——长树里靠高亮找不全时以本清单为最终核对口径。 -->
    <div v-if="selectedFiles.size > 0" class="restore__picked">
      <n-space justify="space-between" align="center" :size="8">
        <n-text depth="3">{{ t('restore.filesPickedHint') }}</n-text>
        <n-button size="tiny" quaternary @click="emit('clear-files')">
          {{ t('restore.filesClear') }}
        </n-button>
      </n-space>
      <ul class="restore__picked-list">
        <li v-for="path in selectedList" :key="path" class="restore__picked-item">
          <span class="restore__picked-path" :title="path">{{ path }}</span>
          <n-button
            size="tiny"
            quaternary
            circle
            :title="t('restore.filesRemove')"
            :aria-label="t('restore.filesRemove')"
            @click="emit('remove-file', path)"
          >
            <template #icon>
              <n-icon :size="14"><IconX /></n-icon>
            </template>
          </n-button>
        </li>
      </ul>
    </div>
  </n-space>

  <!-- P10 页脚两端对齐（对标 Duplicati 向导页脚）：左返回入口 / 右下一步 -->
  <n-space justify="space-between" align="center">
    <n-button quaternary @click="emit('back')">{{ t('restore.flow.backToEntry') }}</n-button>
    <n-button type="primary" :disabled="!snapshotId" @click="emit('next')">
      {{ t('confirm.next') }}
    </n-button>
  </n-space>
</template>

<style scoped>
/* P10 搜索命中项：点击即切换勾选（与文件树共用 selectedFiles 集合）；
   选中态加粗，hover 用主题 action 色（不硬编码）。 */
.restore__hit {
  padding: 4px 8px;
  border-radius: 8px;
  font-size: 12px;
  cursor: pointer;
  word-break: break-all;
}

.restore__hit:hover {
  background: var(--n-action-color, rgba(128, 128, 128, 0.12));
}

.restore__hit.is-selected {
  font-weight: 600;
  /* 与文件树的 checked 行同色（focus 16% 混 surface），保证「搜索命中」与
     「树里勾选」两处选中视觉一致，不再只有一个字重差。 */
  background: color-mix(in srgb, var(--rw-focus) 16%, var(--rw-surface));
}

/* 已选清单：卡片式容器 + 内部滚动（选了很多条也不把页面撑长），
   路径等宽字体 + 单行省略，标题靠 title 属性看全路径。 */
.restore__picked {
  display: flex;
  flex-direction: column;
  gap: var(--rw-space-1);
  max-height: 180px;
  overflow-y: auto;
  padding: var(--rw-space-2);
  border: 1px solid var(--rw-border);
  border-radius: var(--rw-radius-control);
  background: var(--rw-surface);
}

.restore__picked-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.restore__picked-item {
  display: flex;
  align-items: center;
  gap: var(--rw-space-2);
  padding: 2px 4px;
  border-radius: var(--rw-radius-control);
}

.restore__picked-item:hover {
  background: var(--rw-surface-hover);
}

.restore__picked-path {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--font-family-mono);
  font-size: 12px;
}
</style>
