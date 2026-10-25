<script setup lang="ts">
/** 恢复目标路径选择器（P16 / UI全量改造方案 §4.9，owner 裁决 D2 树双模式）。
 *
 * 复刻 Duplicati restore options 的 `app-file-tree`：「浏览树 / 手动输入」二模切换；
 * 树根 = `runtime.restoreRoot`（部署形态自动检测的单一事实源，《源数据过滤与树
 * 双模式设计方案》§3.2——Docker/主机两模式的浏览根由后端运行时下发，前端不写死）。
 * 复用既有件（S-4 禁止重复造轮子）：
 *  - `VirtualTree`（行结构/caret 手势/挂载徽章，select-dir 单击选目录并下钻）；
 *  - `SegmentedTabs`（P6 共用分段控件，作二模开关）；
 *  - mount chips 范式（SourcesPicker.vue:294-314；E33 仅根请求回 mounts，
 *    故一次性取 `/` 后过滤 restoreRoot 子树内的挂载点）；
 *  - `api/fs.ts` browseFs（E33，容器形态路径——恢复目标本就是容器形态）。
 *
 * 越界路径（restoreRoot 外 / 非绝对 / 含 `..`）内联拦截：与后端
 * `RESTORE_ALLOWED_PREFIXES` 同口径的前端第一道（V-6，提交前拦）。
 */

import { NAlert, NButton, NInput, NSpace, NText, useMessage } from 'naive-ui'
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { browseFs } from '@/api/fs'
import SegmentedTabs, { type SegmentedItem } from '@/components/dashboard/SegmentedTabs.vue'
import VirtualTree, { type TreeItem, type TreePage } from '@/components/VirtualTree.vue'
import { useRuntimeStore } from '@/stores/runtime'
import { sourceTreeViewMaxHeight, sourceTreeViewMinHeight } from '@/theme/tokens'

const props = defineProps<{
  /** 当前恢复目标（容器形态，restoreRoot 子树内）。 */
  modelValue: string
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
}>()

const { t } = useI18n()
const runtime = useRuntimeStore()
const message = useMessage()

/** 树根（单一事实源；运行时未就绪回落 /restore 常量——宁可窄不误开）。 */
const root = computed(() => runtime.restoreRoot || '/restore')

/** 二模（Duplicati pathDiscoveryMethod）：浏览树 / 手动输入。 */
const mode = ref<'browse' | 'manual'>('browse')
const modeItems = computed<SegmentedItem[]>(() => [
  { id: 'browse', label: t('restore.options.picker.browse') },
  { id: 'manual', label: t('restore.options.picker.manual') },
])

/** 树当前浏览层（挂载即根层；点目录下钻）。 */
const currentPath = ref(root.value)
/** 已选目标（父级单一来源；显示用）。 */
const picked = computed(() => props.modelValue || root.value)
/** 挂载点快跳（restoreRoot 子树内；root chip 恒在，见模板）。 */
const mountChips = ref<string[]>([])

/** 手动输入草稿：与 picked 分离——非法值只本地报错，不污染父级状态。 */
const draft = ref(props.modelValue || root.value)
watch(
  () => props.modelValue,
  (value) => {
    if (value) {
      draft.value = value
    }
  },
)

/** 运行时根就绪 / 变化时把树层与草稿收敛到根内（V-6：不带着域外值跑）。 */
watch(root, (next) => {
  if (picked.value !== next && !picked.value.startsWith(`${next}/`)) {
    draft.value = next
    emit('update:modelValue', next)
  }
  if (currentPath.value !== next && !currentPath.value.startsWith(`${next}/`)) {
    currentPath.value = next
  }
})

/** 越界校验（前端第一道；后端 RESTORE_ALLOWED_PREFIXES 第二道）。 */
const pathError = computed(() => {
  const value = draft.value.trim()
  if (!value) {
    return t('restore.options.picker.errEmpty')
  }
  if (!value.startsWith('/')) {
    return t('restore.options.picker.errAbsolute')
  }
  if (value.split('/').includes('..')) {
    return t('restore.options.picker.errTraversal')
  }
  if (value !== root.value && !value.startsWith(`${root.value}/`)) {
    return t('restore.options.picker.errOutside', { root: root.value })
  }
  return ''
})

/** 合法输入实时上送（Duplicati 输入框直绑表单控件的同款语义）。 */
watch(draft, (value) => {
  const trimmed = value.trim()
  if (!trimmed || pathError.value) {
    return
  }
  emit('update:modelValue', trimmed)
})

/** 挂载点快跳：E33 仅根请求回 mounts → 一次性取 `/`，过滤 restoreRoot 子树内。
 *  取数失败不阻塞选择器（树与手输都可用；chips 只是快跳便利）。 */
onMounted(async () => {
  try {
    const page = await browseFs('/', null, 1)
    mountChips.value = page.mounts.filter(
      (m) => m.trim() && m !== '/' && (m === root.value || m.startsWith(`${root.value}/`)),
    )
  } catch {
    // 快跳降级：静默（无 chips 不影响选路径）
  }
})

function treeLoader(path: string, cursor?: string): Promise<TreePage> {
  return browseFs(path, cursor).then((page) => ({
    items: page.items.map((item) => ({
      key: item.path,
      name: item.name,
      type: item.type,
      path: item.path,
      is_mountpoint: item.is_mountpoint,
    })),
    nextCursor: page.next_cursor,
  }))
}

/** 点目录名 = 选为目标并下钻（Duplicati FileTree 单击语义；展开由 caret 负责）。 */
function onTreeSelect(item: TreeItem): void {
  if (item.type !== 'dir') {
    return
  }
  currentPath.value = item.path
  emit('update:modelValue', item.path)
}

/** 手输确认（Enter / 失焦）：非法时提示且不提交。 */
function commitManual(): void {
  const value = draft.value.trim()
  if (!value || pathError.value) {
    message.warning(pathError.value || t('restore.options.picker.errEmpty'))
    return
  }
  emit('update:modelValue', value)
}
</script>

<template>
  <div class="restore-picker">
    <SegmentedTabs
      v-model="mode"
      :items="modeItems"
      :aria-label="t('restore.options.picker.modeAria')"
    />

    <template v-if="mode === 'browse'">
      <!-- 挂载点快跳（树双模式 §3.4）：root chip + restoreRoot 子树内挂载点 -->
      <div class="restore-picker__mounts">
        <n-button
          size="tiny"
          round
          :type="currentPath === root ? 'primary' : 'default'"
          @click="currentPath = root"
        >
          {{ root }}
        </n-button>
        <n-button
          v-for="m in mountChips"
          :key="m"
          size="tiny"
          round
          :type="currentPath === m ? 'primary' : 'default'"
          @click="currentPath = m"
        >
          {{ m }}
        </n-button>
      </div>
      <VirtualTree
        :root-path="currentPath"
        :load-children="treeLoader"
        :min-height="sourceTreeViewMinHeight"
        :max-height="sourceTreeViewMaxHeight"
        :selected-path="picked"
        select-dir
        @select="onTreeSelect"
      />
      <n-text depth="3" class="restore-picker__picked">
        {{ t('restore.options.picker.picked') }}：<span class="restore-picker__path">{{ picked }}</span>
      </n-text>
    </template>

    <n-space v-else vertical :size="8">
      <n-text strong>{{ t('restore.options.picker.pathLabel') }}</n-text>
      <n-text depth="3">{{ t('restore.options.picker.manualHint', { root }) }}</n-text>
      <n-input
        v-model:value="draft"
        size="small"
        :placeholder="root"
        @blur="commitManual"
        @keydown.enter.prevent="commitManual"
      />
      <n-alert v-if="pathError" type="error" :show-icon="false">{{ pathError }}</n-alert>
    </n-space>
  </div>
</template>

<style scoped>
.restore-picker {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}

/* 挂载点快跳 chips（SourcesPicker 范式：根 + 各挂载点，点击即跳转） */
.restore-picker__mounts {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.restore-picker__picked {
  display: block;
  font-size: 12px;
}

/* 选中路径等宽显示（路径/技术文本口径，theme/tokens.ts fontFamilyMono 同族） */
.restore-picker__path {
  font-family: Consolas, 'Cascadia Mono', monospace;
}
</style>
