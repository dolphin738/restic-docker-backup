<script setup lang="ts">
/** 恢复目标路径选择器（P16 / UI全量改造方案 §4.9，owner 裁决 D2 树双模式；
 *  P19：快捷路径统一为 D 形态地址栏，见 §4.9.9）。
 *
 * 复刻 Duplicati restore options 的 `app-file-tree`：「浏览树 / 手动输入」二模在 D 形态下
 * 合流——地址栏本身就是手输入口（点空白或末段就地变输入框），不再需要分段切换开关；
 * 树根 = `runtime.restoreRoot`（部署形态自动检测的单一事实源，《源数据过滤与树
 * 双模式设计方案》§3.2——Docker/主机两模式的浏览根由后端运行时下发，前端不写死）。
 * 复用既有件（S-4 禁止重复造轮子）：
 *  - `PathBreadcrumb`（P19 升级为 D 形态地址栏：面包屑分段跳转 + 挂载点三角菜单 +
 *    就地编辑，恒定 1 行，取代原先平铺的 mount chips）；
 *  - `VirtualTree`（行结构/caret 手势/挂载徽章，select-dir 单击选目录并下钻）；
 *  - `api/fs.ts` browseFs（E33，容器形态路径——恢复目标本就是容器形态）。
 *
 * 越界路径（restoreRoot 外 / 非绝对 / 含 `..`）与受保护区域（deny）内联拦截：与后端
 * `restore_allowed_prefixes` 同口径的前端第一道（V-6，提交前拦）。
 */

import { NAlert, NText, useMessage } from 'naive-ui'
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { browseFs } from '@/api/fs'
import PathBreadcrumb from '@/components/PathBreadcrumb.vue'
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

/** 树当前浏览层（挂载即根层；点目录下钻）。 */
const currentPath = ref(root.value)
/** 已选目标（父级单一来源；显示用）。 */
const picked = computed(() => props.modelValue || root.value)
/** 挂载点快跳（restoreRoot 子树内；进地址栏的三角菜单）。 */
const mountChips = ref<string[]>([])

/** 运行时根就绪 / 变化时把浏览层收敛到根内（V-6：不带着域外值跑）。 */
watch(root, (next) => {
  if (picked.value !== next && !isUnderRoot(picked.value)) {
    emit('update:modelValue', next)
  }
  if (currentPath.value !== next && !isUnderRoot(currentPath.value)) {
    currentPath.value = next
  }
})

/** 是否落在树根内。host 模式根为 `/`：**不能**用 `startsWith('//')` 判定（任何正常
 *  路径都不匹配 `//`）——那是后端 `PathMapper._is_under` 踩过的同一个坑，此处对齐：
 *  根为 `/` 时「是绝对路径即命中」。 */
function isUnderRoot(path: string): boolean {
  const base = root.value.replace(/\/+$/, '')
  if (!base) {
    return path.startsWith('/')
  }
  return path === base || path.startsWith(`${base}/`)
}

/** host 模式的受保护区域（仓库存储位置 / 应用数据 / 伪文件系统，后端同源下发）。
 *  docker 模式该集合为空——浏览面已被 /sources 白名单界定，无需额外拦截。 */
function isDenied(path: string): boolean {
  return (runtime.denyPrefixes ?? []).some((deny) => {
    const prefix = deny.replace(/\/+$/, '')
    if (!prefix) {
      return false
    }
    return path === prefix || path.startsWith(`${prefix}/`)
  })
}

/** 业务校验（注入地址栏）：越界 + 受保护区域。基础形态校验（绝对路径 / 无 `..`）
 *  由地址栏内置，此处只给恢复链路口径。 */
function validateTarget(value: string): string {
  if (!isUnderRoot(value)) {
    return t('restore.options.picker.errOutside', { root: root.value })
  }
  if (isDenied(value)) {
    return t('restore.options.picker.errDenied')
  }
  return ''
}

/** 树内点选被保护目录时的就地提示（不 emit、不下钻）。 */
const selectError = ref('')

/** 挂载点快跳：E33 仅根请求回 mounts → 一次性取 `/`，过滤 restoreRoot 子树内。
 *  取数失败不阻塞选择器（树与地址栏都可用；挂载菜单只是快跳便利）。 */
onMounted(async () => {
  try {
    const page = await browseFs('/', null, 1)
    mountChips.value = page.mounts.filter((m) => m.trim() && m !== '/' && isUnderRoot(m))
  } catch {
    // 快跳降级：静默（无挂载菜单不影响选路径）
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

/** 点目录名 = 选为目标并下钻（Duplicati FileTree 单击语义；展开由 caret 负责）。
 *  host 模式下受保护区域（deny）不可选：就地报错、不 emit、不下钻。 */
function onTreeSelect(item: TreeItem): void {
  if (item.type !== 'dir') {
    return
  }
  if (isDenied(item.path)) {
    selectError.value = t('restore.options.picker.errDenied')
    return
  }
  selectError.value = ''
  currentPath.value = item.path
  emit('update:modelValue', item.path)
}

/** 地址栏导航（含手输提交）：浏览层跟进**且**即为恢复目标——与 Duplicati
 *  「Manually type path」同语义（路径即目标），故此处直接上送父级。
 *  越界 / 受保护值已被地址栏校验拦下，不会走到这里。 */
function onNavigate(path: string): void {
  currentPath.value = path
  emit('update:modelValue', path)
}

/** 失焦时值非法：地址栏已取消编辑，此处只提示（不劫持焦点、不回写父级）。 */
function onInvalid(reason: string): void {
  message.warning(reason)
}
</script>

<template>
  <div class="restore-picker">
    <PathBreadcrumb
      :path="currentPath"
      :root="root"
      :mounts="mountChips"
      :validate="validateTarget"
      @select="onNavigate"
      @invalid="onInvalid"
    />
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
    <n-alert v-if="selectError" type="error" :show-icon="false">{{ selectError }}</n-alert>
  </div>
</template>

<style scoped>
.restore-picker {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
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
