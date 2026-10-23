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

import { NAlert, NButton, NInput, NSelect, NSpace, NSpin, NText } from 'naive-ui'
import { onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { listSnapshotFiles, searchSnapshotFiles } from '@/api/snapshots'
import type { SnapshotFileOut } from '@/api/types'
import VirtualTree, { type TreeItem, type TreePage } from '@/components/VirtualTree.vue'

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
  (e: 'next'): void
  (e: 'back'): void
}>()

const { t } = useI18n()

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

/** P9 满高：步骤 1 专属——文件树需要确定高度才能内部滚动。
 *  诚实边界：BasicLayout 内容区没有 height 继承链，真实 flex 撑满需先给布局层高度
 *  （属 P14 视觉层），故这里以「文件树高度上探」落地，是当前唯一有实际效果的做法。 */
const FILE_TREE_HEIGHT_FILL = 520

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
      :height="FILE_TREE_HEIGHT_FILL"
      select-dir
      :selected-paths="selectedFiles"
      @select="onFileSelect"
    />
    <n-text v-if="selectedFiles.size > 0" depth="2">
      {{ t('restore.filesSelected', { n: selectedFiles.size }) }}
    </n-text>
    <n-text v-else depth="3">{{ t('restore.filesWhole') }}</n-text>
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
}
</style>
