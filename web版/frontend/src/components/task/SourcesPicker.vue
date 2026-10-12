<script setup lang="ts">
/** 向导步骤 2：备份源选择（增量设计 §5.3，抽自退役的备份源页）。
 *
 * - VirtualTree 多选目录（展示形态，E05 无状态浏览）；
 * - E32 内置排除集只读展示（强制生效，D8 单一真源，不复制清单）；
 * - 追加排除规则输入（用户排除项，随任务入库）；
 * - 「首次备份最慢」提示。
 */

import { useQuery } from '@tanstack/vue-query'
import {
  NButton,
  NCollapse,
  NCollapseItem,
  NInput,
  NSpace,
  NTag,
  NText,
  useMessage,
} from 'naive-ui'
import { computed, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { browseSources } from '@/api/backupTasks'
import { getExclusions } from '@/api/exclusions'
import VirtualTree, { type TreeItem, type TreePage } from '@/components/VirtualTree.vue'
import { useUiStore } from '@/stores/ui'

const props = defineProps<{
  /** 已选备份源（展示形态，如 /etc）。 */
  sources: string[]
  /** 用户追加排除规则（展示形态；内置排除集不入库）。 */
  excludes: string[]
}>()

const emit = defineEmits<{
  (e: 'update:sources', value: string[]): void
  (e: 'update:excludes', value: string[]): void
}>()

const { t } = useI18n()
const ui = useUiStore()

const message = useMessage()
ui.setMessageApi(message)
onUnmounted(() => ui.setMessageApi(null))

// ---------------------------------------------------------------- 目录树（E05）
const currentPath = ref('/')

function treeLoader(path: string, cursor?: string): Promise<TreePage> {
  return browseSources(path, cursor).then((page) => ({
    items: page.items.map((item) => ({
      key: item.display_path,
      name: item.name,
      type: item.type,
      path: item.display_path,
    })),
    nextCursor: page.next_cursor,
  }))
}

/** 点目录/文件即加入已选（去重）；展开由行首三角形负责，不与选中冲突。 */
function onTreeSelect(item: TreeItem): void {
  if (props.sources.includes(item.path)) {
    message.info(t('wizard.sourceExists', { path: item.path }))
    return
  }
  emit('update:sources', [...props.sources, item.path])
  message.success(t('wizard.sourcePicked', { path: item.path }))
}

function removeSource(path: string): void {
  emit(
    'update:sources',
    props.sources.filter((item) => item !== path),
  )
}

// ---------------------------------------------------------------- 内置排除集（E32，只读）
const exclusionsQuery = useQuery({ queryKey: ['exclusions'], queryFn: () => getExclusions() })

/** E32 直接回剥好前缀的展示形态（display_core ⊆ display_items），前端不再做前缀转换。 */
const builtinRules = computed(() => exclusionsQuery.data.value?.display_items ?? [])

// ---------------------------------------------------------------- 追加排除
const excludeDraft = ref('')

function addExclude(): void {
  const value = excludeDraft.value.trim()
  if (!value || props.excludes.includes(value)) {
    excludeDraft.value = ''
    return
  }
  emit('update:excludes', [...props.excludes, value])
  excludeDraft.value = ''
}

function removeExclude(rule: string): void {
  emit(
    'update:excludes',
    props.excludes.filter((item) => item !== rule),
  )
}
</script>

<template>
  <n-space vertical :size="12">
    <n-text strong>{{ t('wizard.sources') }}</n-text>

    <VirtualTree
      :root-path="currentPath"
      :load-children="treeLoader"
      :height="320"
      select-dir
      @select="onTreeSelect"
    />
    <n-text depth="3" class="sources-picker__hint">{{ t('wizard.sourcesHint') }}</n-text>

    <n-text v-if="sources.length === 0" depth="3">{{ t('wizard.sourcesEmpty') }}</n-text>
    <n-space v-else :size="6">
      <n-tag
        v-for="path in sources"
        :key="path"
        size="small"
        closable
        @close="removeSource(path)"
      >
        {{ path }}
      </n-tag>
    </n-space>

    <n-collapse size="small">
      <n-collapse-item :title="t('wizard.exclusionsBuiltin', { n: builtinRules.length })" name="builtin">
        <n-space :size="6" class="sources-picker__rules">
          <n-tag v-for="rule in builtinRules" :key="rule" size="small" round>{{ rule }}</n-tag>
        </n-space>
      </n-collapse-item>
    </n-collapse>

    <n-text strong>{{ t('wizard.exclusionsExtra') }}</n-text>
    <n-space :size="6">
      <n-input
        v-model:value="excludeDraft"
        size="small"
        class="sources-picker__input"
        :placeholder="t('wizard.exclusionsExtra')"
        @keydown.enter.prevent="addExclude"
      />
      <n-button size="small" @click="addExclude">{{ t('wizard.extraExclude') }}</n-button>
    </n-space>
    <n-space v-if="excludes.length" :size="6">
      <n-tag
        v-for="rule in excludes"
        :key="rule"
        size="small"
        closable
        @close="removeExclude(rule)"
      >
        {{ rule }}
      </n-tag>
    </n-space>

    <n-text depth="3" class="sources-picker__hint">{{ t('wizard.first_slow') }}</n-text>
  </n-space>
</template>

<style scoped>
.sources-picker__hint {
  display: block;
  font-size: 12px;
}

.sources-picker__rules {
  max-height: 180px;
  overflow-y: auto;
}

.sources-picker__input {
  flex: 1 1 240px;
}
</style>
