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
  NIcon,
  NInput,
  NText,
  useMessage,
} from 'naive-ui'
import { computed, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { browseSources } from '@/api/backupTasks'
import { getExclusions } from '@/api/exclusions'
import { IconCheck, IconEdit, IconPlusCircle, IconTrash, IconX } from '@/components/icons/icons'
import PathBreadcrumb from '@/components/PathBreadcrumb.vue'
import ExcludeEditor from '@/components/task/ExcludeEditor.vue'
import VirtualTree, { type TreeItem, type TreePage } from '@/components/VirtualTree.vue'
import { useRuntimeStore } from '@/stores/runtime'
import { useUiStore } from '@/stores/ui'
import { sourceTreeViewMaxHeight, sourceTreeViewMinHeight } from '@/theme/tokens'
import {
  anchorPattern,
  flattenExcludes,
  specFromFlatExcludes,
  type ExcludeSpec,
} from '@/utils/excludeSpec'

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
/** 地址栏显示值 = **关注路径**（最近选中的目录），与树根 `currentPath` 分离
 *  （owner 2026-10-29：选中目录后地址栏须跟着变成选中路径，而树不下钻）。 */
const focusPath = ref<string | null>(null)
/** 挂载点快跳（展示形态；仅根目录响应带）→ 收进地址栏（D 形态）的三角菜单，
 *  不再平铺成 chips（P19：13 个挂载点会顶满 7 行，且长路径换行难看）。 */
const mountChips = ref<string[]>([])

function treeLoader(path: string, cursor?: string): Promise<TreePage> {
  return browseSources(path, cursor).then((page) => {
    // 快跳 chips **只从模式根响应更新**（owner 2026-10-24 bug 修复）：旧实现在每次响应
    // 都覆盖 mountChips，而非根请求回 mounts=[] → 点击 chip 跳转后导航条瞬间清空，
    // 视觉上等于「点了没反应」。空展示路径（mount == 源根本身 → to_display 产出 ''）
    // 也过滤掉——那种 chip 点击是 no-op，本身就是「跳不了」的另一处来源。
    if (!path || path === '/') {
      mountChips.value = (page.mounts ?? []).filter((m) => m.trim() && m !== '/')
    }
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

/** 地址栏跳转（面包屑段 / 挂载点菜单 / 手输路径）：**只改浏览层**——备份源成员资格仍由
 *  点目录名切换（语义不变，P19 只换快跳形态）。
 *  跳转后地址栏回到浏览根（清 focusPath），否则「跳到 A 又选中 B」时地址栏会停在 B，
 *  与树当前所在目录脱节。 */
function onJump(path: string): void {
  currentPath.value = path
  focusPath.value = null
}

/** 名称点击 = 统一切换备份成员资格（owner 2026-10-24，Duplicati 无复选框/行末按钮，
 *  用户裁决「在已选中的目录下点取消 = 加入排除」）：
 *   ① 显式加入的源          → 再点 = 取消（移出路径卡）
 *   ② 被源覆盖（自身/祖先）  → 点它 = 排除（加锚定规则 → 排除规则列表 +1，行置灰删除线）；
 *                              再点 = 恢复包含（移除该规则）
 *   ③ 未被任何源覆盖         → 点它 = 加入（进路径卡，蓝底高亮，子孙行跟随高亮）
 *  守卫：祖先已被排除的后代不可再包含（restic gitignore 限制，探针 Q2/Q3）→ no-op，不做假开关。
 */
function onTreeSelect(item: TreeItem): void {
  const path = item.path
  // 地址栏跟随选中（owner 2026-10-29）：点谁地址栏显示谁，与是否加入/取消无关。
  // 树根 `currentPath` **不动**——备份源是勾选型多选，选中即下钻会打断连续选兄弟目录。
  if (item.type === 'dir') {
    focusPath.value = path
  }

  // ① 显式加入的源 → 取消：同步清掉**该源名下（含自身）的排除规则**——
  // 否则父源移除后，子目录的排除规则变成孤儿（父都没了，排除毫无意义）。
  // owner 2026-10-24 实证场景：加入 /home → 排除 /home/init → 取消 /home，排除规则残留。
  if (props.sources.includes(path)) {
    const anchored = anchorPattern(path, sourceRoot.value)
    const kept = spec.value.rules.filter((rule) => {
      const ruleAnchored = anchorPattern(rule.pattern, sourceRoot.value)
      return !(ruleAnchored === anchored || ruleAnchored.startsWith(`${anchored}/`))
    })
    if (kept.length !== spec.value.rules.length) {
      onSpecUpdate({ ...spec.value, rules: kept })
    }
    emit(
      'update:sources',
      props.sources.filter((item) => item !== path),
    )
    return
  }

  const covered = props.sources.some((source) => path === source || path.startsWith(`${source}/`))
  if (!covered) {
    // ③ 未覆盖 → 加入为源（无消息打扰：行高亮即反馈）
    emit('update:sources', [...props.sources, path])
    return
  }

  // ② 被覆盖 → 切换排除
  const anchored = anchorPattern(path, sourceRoot.value)
  const ownIndex = spec.value.rules.findIndex(
    (rule) => !rule.negate && anchorPattern(rule.pattern, sourceRoot.value) === anchored,
  )
  if (ownIndex >= 0) {
    // 自身是规则归属者 → 恢复包含
    onSpecUpdate({ ...spec.value, rules: spec.value.rules.filter((_, i) => i !== ownIndex) })
    return
  }
  // 祖先已被排除 → restic 捞不回，no-op。
  // 注意：rule.pattern 与 path 同为**展示形态**，直接前缀比对（别再套 anchorPattern——
  // 那会把规则变成 /sources/... 而与展示路径 /data/sub 永不匹配，本守卫曾因此失效）。
  const ancestorExcluded = spec.value.rules.some(
    (rule) => !rule.negate && path.startsWith(`${rule.pattern.trim()}/`),
  )
  if (ancestorExcluded) {
    return
  }
  // 未排除 → 加排除规则（精确锚定，无尾随斜杠——绕开 restic -/dir/ 静默失效坑）
  onSpecUpdate({
    ...spec.value,
    rules: [...spec.value.rules, { pattern: path, negate: false, icase: false }],
  })
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

/** 已排除的展示路径（非负向规则的 pattern 本身，与树行 path 同为展示形态）。
 *  旧实现用 anchorPattern 锚定后（/sources/...）与树行（/data）比对——docker 模式下
 *  永不相等，⊘ 激活态从未亮过（潜伏 bug，随三态复选框重构一并修复）。 */
const excludedPaths = computed(() => {
  const out = new Set<string>()
  for (const rule of spec.value.rules) {
    if (rule.negate) {
      continue
    }
    out.add(rule.pattern)
  }
  return out
})

/** 高亮路径 = 备份源集合（前缀匹配由 VirtualTree 负责：选目录作源 → 整棵子树高亮）。 */
const highlightPaths = computed(() => new Set(props.sources))
</script>

<template>
  <div class="sources-picker">
    <!-- 左栏：源数据（目录树） -->
    <section class="sources-picker__left">
      <n-text strong class="sources-picker__title">{{ t('wizard.sources') }}</n-text>
      <!-- 快捷路径统一 D 形态（P19）：面包屑分段跳转 + 挂载点收进三角菜单 + 就地地址栏，
           恒定 1 行；跳转只改浏览层，备份源仍由点目录名切换（语义不变）。 -->
      <PathBreadcrumb :path="focusPath ?? currentPath" :mounts="mountChips" @select="onJump" />
      <!-- 等高填充：左栏 flex 纵向，树 wrapper flex:1 随右栏内容拉伸，树内部滚动 -->
      <div class="sources-picker__tree-wrap">
        <VirtualTree
          :root-path="currentPath"
          :load-children="treeLoader"
          :min-height="sourceTreeViewMinHeight"
          :max-height="sourceTreeViewMaxHeight"
          :highlight-paths="highlightPaths"
          :excluded-paths="excludedPaths"
          select-dir
          @select="onTreeSelect"
        />
      </div>
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

        <!-- 排除条件三区块（按优先级）：①匹配规则 → ②独立开关 → ③内置集（强制）。
             内置集由本组件从 E32 取展示形态后传入，只读区在 ExcludeEditor 内统一呈现。 -->
        <ExcludeEditor
          :model-value="spec"
          :source-root="sourceRoot"
          :builtin-rules="builtinRules"
          @update:model-value="onSpecUpdate"
        />

        <n-text depth="3" class="sources-picker__hint">{{ t('wizard.first_slow') }}</n-text>
      </n-card>
    </section>
  </div>
</template>

<style scoped>
/* 左树右栏（Duplicati source-data：.left / .right 双列；窄屏回落到单栏）。
   align-items: stretch（默认拉伸改显式声明）→ 左栏高度跟随右栏内容，树在内部滚动。 */
.sources-picker {
  display: flex;
  align-items: stretch;
  gap: 16px;
}

.sources-picker__left {
  display: flex;
  flex: 1 1 55%;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}

/* 树 wrapper：内容驱动（owner 2026-10-24 三次迭代终态）。
   高度由 VirtualTree 按「行数 × 行高」JS 算好（min/max 见 theme/tokens.ts），
   这里只做不拉伸、不撑行（flex:none）——树多高=内容多高，零死空白；
   右栏该多长多长互不绑架（Duplicati 左树同样不追右栏高度）。
   曾用的 stretch / height:100% 全部废弃：前者盒内死空白，后者百分比链不可靠。 */
.sources-picker__tree-wrap {
  flex: none;
  display: flex;
  flex-direction: column;
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
