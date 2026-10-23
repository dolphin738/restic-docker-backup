<script setup lang="ts">
/** 向导步骤 2：备份源选择（增量设计 §5.3，抽自退役的备份源页）。
 *
 * 版式对齐 Duplicati「编辑备份 → 源数据」（projects/ngclient .../source-data.component.html）：
 *   左栏 = 目录树（浏览容器 /sources，点名称即加入已选）；
 *   右栏 = 选项卡：① 路径（只读行 + 铅笔编辑 + 垃圾桶删除 + 直接添加）
 *                 ② 排除（内置集只读 + 追加规则可编辑行）。
 * 原单栏纵向版式已废：树与已选/排除挤在一列，长列表要来回滚动才能对照。
 *
 * 不做的事（有意为之，勿回退）：
 *   - 不做「新增远程目录」对话框：Duplicati 由自家服务端直连 70+ 云，我们的备份源恒为
 *     容器内 /sources 挂载点，远端一律经宿主机挂载后映射进容器，此处只收绝对路径；
 *   - 不做「显示隐藏文件」开关：E05 /api/sources 无 hidden 参数，UI 上给了也只是假开关；
 *   - 不做过滤条件下拉（Duplicati 14 项）：restic 只有 glob exclude，映射结论见 .e2e 探针，
 *     待 owner 定方案后另起组件。
 */

import { useQuery } from '@tanstack/vue-query'
import {
  NButton,
  NCard,
  NCollapse,
  NCollapseItem,
  NIcon,
  NInput,
  NTag,
  NText,
  useMessage,
} from 'naive-ui'
import { computed, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { browseSources } from '@/api/backupTasks'
import { getExclusions } from '@/api/exclusions'
import { IconPlusCircle } from '@/components/icons/icons'
import ExcludeEditor from '@/components/task/ExcludeEditor.vue'
import VirtualTree, { type TreeItem, type TreePage } from '@/components/VirtualTree.vue'
import { useRuntimeStore } from '@/stores/runtime'
import { useUiStore } from '@/stores/ui'
import { flattenExcludes, specFromFlatExcludes, type ExcludeSpec } from '@/utils/excludeSpec'

const props = defineProps<{
  /** 已选备份源（展示形态，如 /etc）。 */
  sources: string[]
  /** 用户追加排除规则（展示形态；内置排除集不入库）。 */
  excludes: string[]
  /** 批次② 结构化过滤条件（快捷卡 + 规则）；缺省由 excludes 反推（编辑预填兼容）。 */
  excludeSpec?: ExcludeSpec
}>()

const emit = defineEmits<{
  (e: 'update:sources', value: string[]): void
  (e: 'update:excludes', value: string[]): void
  (e: 'update:excludeSpec', value: ExcludeSpec): void
}>()

const { t } = useI18n()
const ui = useUiStore()

const message = useMessage()
ui.setMessageApi(message)
onUnmounted(() => ui.setMessageApi(null))

// ---------------------------------------------------------------- 左栏：目录树（E05）
const currentPath = ref('/')
/** 挂载点快跳（展示形态；仅根目录响应带，点击即跳转）。 */
const mountChips = ref<string[]>([])

function treeLoader(path: string, cursor?: string): Promise<TreePage> {
  return browseSources(path, cursor).then((page) => {
    // 非根目录 mounts 为空数组 → 同步清空快跳
    mountChips.value = page.mounts ?? []
    return {
      items: page.items.map((item) => ({
        key: item.display_path,
        name: item.name,
        type: item.type,
        path: item.display_path,
        is_mountpoint: item.is_mountpoint,
      })),
      nextCursor: page.next_cursor,
    }
  })
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

// ---------------------------------------------------------------- 右栏①：路径行（铅笔/垃圾桶）
/** 正在编辑的源路径（null = 无行处于编辑态）；同一时刻只允许一行编辑，避免半改状态互相覆盖。 */
const editingPath = ref<string | null>(null)
const pathDraft = ref('')

function startPathEdit(path: string): void {
  editingPath.value = path
  pathDraft.value = path
}

function cancelPathEdit(): void {
  editingPath.value = null
  pathDraft.value = ''
}

function removeSource(path: string): void {
  if (editingPath.value === path) {
    cancelPathEdit()
  }
  emit(
    'update:sources',
    props.sources.filter((item) => item !== path),
  )
}

/** 路径形态校验：与「直接添加」同口径（绝对路径 + 无 ".." 穿越段）。 */
function validatePath(value: string): boolean {
  if (!value.startsWith('/') || value.includes('..')) {
    message.warning(t('wizard.pathInvalid'))
    return false
  }
  return true
}

function commitPathEdit(): void {
  const old = editingPath.value
  if (!old) {
    return
  }
  const value = pathDraft.value.trim()
  if (!value || value === old) {
    cancelPathEdit()
    return
  }
  if (!validatePath(value)) {
    return
  }
  if (props.sources.includes(value)) {
    message.info(t('wizard.sourceExists', { path: value }))
    return
  }
  emit(
    'update:sources',
    props.sources.map((item) => (item === old ? value : item)),
  )
  cancelPathEdit()
}

/** 直接添加路径（原「手动输入挂载路径（远端源）」）：远端经宿主机挂载进 /sources，此处只收绝对路径。 */
const manualPath = ref('')

function addManualPath(): void {
  const value = manualPath.value.trim()
  if (!value) {
    return
  }
  if (!validatePath(value)) {
    return
  }
  if (props.sources.includes(value)) {
    message.info(t('wizard.sourceExists', { path: value }))
    manualPath.value = ''
    return
  }
  emit('update:sources', [...props.sources, value])
  message.success(t('wizard.sourcePicked', { path: value }))
  manualPath.value = ''
}

// ---------------------------------------------------------------- 右栏②：排除
const exclusionsQuery = useQuery({ queryKey: ['exclusions'], queryFn: () => getExclusions() })

/** E32 直接回剥好前缀的展示形态（display_core ⊆ display_items），前端不再做前缀转换。 */
const builtinRules = computed(() => exclusionsQuery.data.value?.display_items ?? [])

// 批次②：结构化过滤条件（快捷卡 + 有序规则），编辑由 ExcludeEditor 承担；
// 结果同时下发扁平 excludes（兼容 dry-run / 既有契约）与 excludeSpec（新契约）。
const runtime = useRuntimeStore()
const sourceRoot = computed(() => runtime.browseRoot || '/sources')
const spec = ref<ExcludeSpec>(props.excludeSpec ?? specFromFlatExcludes(props.excludes))

watch(
  () => props.excludeSpec,
  (next) => {
    if (next) {
      spec.value = next
    }
  },
)

watch(
  () => props.excludes,
  (next) => {
    if (!props.excludeSpec) {
      spec.value = specFromFlatExcludes(next)
    }
  },
)

function onSpecUpdate(next: ExcludeSpec): void {
  spec.value = next
  emit('update:excludeSpec', next)
  emit('update:excludes', flattenExcludes(next, sourceRoot.value).excludes)
}
</script>

<template>
  <div class="sources-picker">
    <!-- 左栏：源数据（目录树） -->
    <section class="sources-picker__left">
      <n-text strong class="sources-picker__title">{{ t('wizard.sources') }}</n-text>
      <!-- 挂载点快跳（批次③ §3.4）：根 + 各挂载点，点击即跳转 -->
      <div class="sources-picker__mounts">
        <n-button
          size="tiny"
          round
          :type="currentPath === '/' ? 'primary' : 'default'"
          @click="currentPath = '/'"
        >
          /
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
        :height="320"
        select-dir
        @select="onTreeSelect"
      />
      <n-text depth="3" class="sources-picker__hint">{{ t('wizard.sourcesHint') }}</n-text>
    </section>

    <!-- 右栏：选项（路径 / 排除） -->
    <section class="sources-picker__right">
      <n-card size="small" class="sources-picker__card">
        <template #header>{{ t('wizard.pathsCard') }}</template>
        <n-text depth="3" class="sources-picker__desc">{{ t('wizard.pathsDesc') }}</n-text>

        <div class="sources-picker__rows">
          <n-text v-if="sources.length === 0" depth="3">{{ t('wizard.sourcesEmpty') }}</n-text>
          <div v-for="path in sources" :key="path" class="sources-picker__row">
            <template v-if="editingPath === path">
              <n-input
                v-model:value="pathDraft"
                size="small"
                class="sources-picker__row-input"
                :placeholder="t('wizard.editPathPlaceholder')"
                @keydown.enter.prevent="commitPathEdit"
              />
              <n-button
                size="small"
                quaternary
                :aria-label="t('common.save')"
                @click="commitPathEdit"
              >
                <template #icon>
                  <n-icon :size="16"><IconCheck /></n-icon>
                </template>
              </n-button>
              <n-button
                size="small"
                quaternary
                :aria-label="t('common.cancel')"
                @click="cancelPathEdit"
              >
                <template #icon>
                  <n-icon :size="16"><IconX /></n-icon>
                </template>
              </n-button>
            </template>
            <template v-else>
              <n-input :value="path" size="small" readonly class="sources-picker__row-input" />
              <n-button
                size="small"
                quaternary
                :aria-label="t('wizard.editPath')"
                @click="startPathEdit(path)"
              >
                <template #icon>
                  <n-icon :size="16"><IconEdit /></n-icon>
                </template>
              </n-button>
              <n-button
                size="small"
                quaternary
                :aria-label="t('wizard.removePath')"
                @click="removeSource(path)"
              >
                <template #icon>
                  <n-icon :size="16"><IconTrash /></n-icon>
                </template>
              </n-button>
            </template>
          </div>

          <!-- 直接添加路径（Duplicati「Add a direct path」同位置：列表末尾的行内输入 + 加号） -->
          <div class="sources-picker__row">
            <n-input
              v-model:value="manualPath"
              size="small"
              class="sources-picker__row-input"
              :placeholder="t('wizard.addDirectPath')"
              @keydown.enter.prevent="addManualPath"
            />
            <n-button
              size="small"
              type="primary"
              :aria-label="t('wizard.manualPathAdd')"
              @click="addManualPath"
            >
              <template #icon>
                <n-icon :size="16"><IconPlusCircle /></n-icon>
              </template>
            </n-button>
          </div>
        </div>

        <n-text depth="3" class="sources-picker__hint">{{ t('wizard.manualPathHint') }}</n-text>
      </n-card>

      <n-card size="small" class="sources-picker__card">
        <template #header>{{ t('wizard.excludeCard') }}</template>
        <n-text depth="3" class="sources-picker__desc">{{ t('wizard.excludeDesc') }}</n-text>

        <n-collapse size="small">
          <n-collapse-item
            :title="t('wizard.exclusionsBuiltin', { n: builtinRules.length })"
            name="builtin"
          >
            <div class="sources-picker__rules">
              <n-tag v-for="rule in builtinRules" :key="rule" size="small" round>{{ rule }}</n-tag>
            </div>
          </n-collapse-item>
        </n-collapse>

        <ExcludeEditor
          :model-value="spec"
          :source-root="sourceRoot"
          @update:model-value="onSpecUpdate"
        />

        <n-text depth="3" class="sources-picker__hint">{{ t('wizard.first_slow') }}</n-text>
      </n-card>
    </section>
  </div>
</template>

<style scoped>
/* 左树右栏（Duplicati source-data：.left / .right 双列；窄屏回落到单栏） */
.sources-picker {
  display: flex;
  align-items: flex-start;
  gap: 16px;
}

.sources-picker__left {
  display: flex;
  flex: 1 1 55%;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}

.sources-picker__right {
  display: flex;
  flex: 1 1 45%;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
}

.sources-picker__title {
  font-size: 14px;
}

.sources-picker__card {
  min-width: 0;
}

.sources-picker__desc,
.sources-picker__hint {
  display: block;
  margin-bottom: 8px;
  font-size: 12px;
}

/* 挂载点快跳 chips（批次③ §3.4：根 + 各挂载点） */
.sources-picker__mounts {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
}

.sources-picker__rows {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

/* 可编辑行：只读输入框 + 铅笔 + 垃圾桶（图标按钮必须有 aria-label，见 a11y-baseline） */
.sources-picker__row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.sources-picker__row-input {
  flex: 1 1 auto;
  min-width: 0;
}

.sources-picker__rules {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  max-height: 180px;
  overflow-y: auto;
}

@media (max-width: 960px) {
  .sources-picker {
    flex-direction: column;
  }

  .sources-picker__left,
  .sources-picker__right {
    flex: 1 1 auto;
    width: 100%;
  }
}
</style>
