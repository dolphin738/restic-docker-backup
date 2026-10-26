<script setup lang="ts">
/** 容器视角目录选择器（内联双栏）：地址栏 + 目录树 + 路径值输入框。
 *
 * 来源：从仓库页 local 行（`pages/repo/index.vue`）抽出，供 **repo 页**与
 * **rclone crypt 本地目录**共用（S-4 单一实现，禁止再内联复制一份）。
 *
 * 路径语义 = **容器绝对路径**（E33 `fs/browse`，无 display 换算）——与备份源树
 * （E05 `/api/sources`，展示形态 + `/sources` 白名单）不同，勿混用。
 *
 * 与既有实现一致的三条纪律（血泪教训，勿"优化"掉）：
 *   1. **挂载点独立预取**：E33 只在 `path="/"` 的请求里回 `mounts`，而树起点常是
 *      已填路径的父目录 → 靠树的响应顺带永远拿不到，必须自己发一次根请求；
 *   2. **点目录名 = 选中，不改 browsePath**：导航交给地址栏/三角形；VirtualTree 的
 *      selectDir 会自行展开该目录，视觉上仍有下钻反馈；
 *   3. **外部赋值才重定位树**：自己 emit 出去的值回流时若也重定位，树会跳回父目录
 *      （按值来源判定，不 watch 派生布尔）。
 *
 * 手输保留：目录可能尚不存在（rclone 会自建、仓库可新建目录），纯树选无法表达
 * 「尚未创建的目录」，故输入框是唯一可表达入口。
 */
import { NInput, NText } from 'naive-ui'
import { onMounted, ref, watch } from 'vue'
import { browseFs } from '@/api/fs'
import PathBreadcrumb from '@/components/PathBreadcrumb.vue'
import VirtualTree, { type TreeItem, type TreePage } from '@/components/VirtualTree.vue'

const props = withDefaults(
  defineProps<{
    /** 容器绝对路径（v-model:path）。 */
    path: string
    /** 树下方提示（调用方给 i18n 文案）。 */
    hint?: string
    /** 右栏值标签（如「已选路径」）。 */
    valueLabel?: string
    /** 右栏输入框提示。 */
    valueHint?: string
    /** 输入框 placeholder。 */
    placeholder?: string
    /** 树视口高度。 */
    height?: number
    /** 只读态（env 覆盖时地址不可改，树隐去——给了就是假开关）。 */
    disabled?: boolean
  }>(),
  { hint: '', valueLabel: '', valueHint: '', placeholder: '', height: 260, disabled: false },
)

const emit = defineEmits<{ (e: 'update:path', value: string): void }>()

const ROOT = '/'

/** 父目录：直接停在所填目录里会摊开其内部结构，定位到上一层更利于选择。 */
function parentOf(path: string): string {
  const trimmed = (path || '').replace(/\/+$/, '')
  if (!trimmed) {
    return ROOT
  }
  const idx = trimmed.lastIndexOf('/')
  return idx <= 0 ? ROOT : trimmed.slice(0, idx) || ROOT
}

const browsePath = ref(parentOf(props.path))
const mountChips = ref<string[]>([])
/** 最近一次由本组件 emit 出去的值：回流时不重定位树（防「选完树跳回父目录」）。 */
let lastEmitted: string | null = null

async function ensureMounts(): Promise<void> {
  if (mountChips.value.length > 0) {
    return
  }
  try {
    // 只取一页、只为拿 mounts（E33 仅在根请求返回挂载点）
    const page = await browseFs(ROOT, null, 1)
    mountChips.value = (page.mounts ?? []).filter((m) => m.trim() && m !== ROOT)
  } catch {
    // 快跳降级：静默（挂载菜单只是便利，不影响选路径）
  }
}

function fsLoader(path: string, cursor?: string): Promise<TreePage> {
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

function onTreeSelect(item: TreeItem): void {
  if (item.type !== 'dir') {
    return
  }
  lastEmitted = item.path
  emit('update:path', item.path)
}

function jumpTo(path: string): void {
  browsePath.value = path
}

watch(
  () => props.path,
  (value) => {
    if (value === lastEmitted) {
      // 自己发出去的：树已下钻，不回退
      lastEmitted = null
      return
    }
    browsePath.value = parentOf(value ?? '')
  },
)

onMounted(() => {
  void ensureMounts()
})
</script>

<template>
  <!-- naive 的 .n-form-item-blank 是 `display:flex; flex-direction:row`，两个裸子节点会被
       平分成左右各 50%（曾因此把树挤成半宽），故此处自带布局容器并显式声明比例与窄屏回落。 -->
  <div class="dir-picker" :class="{ 'dir-picker--disabled': disabled }">
    <div v-if="!disabled" class="dir-picker__tree">
      <PathBreadcrumb :path="browsePath" :mounts="mountChips" @select="jumpTo" />
      <VirtualTree
        :root-path="browsePath"
        :load-children="fsLoader"
        :height="height"
        select-dir
        :selected-path="path"
        @select="onTreeSelect"
      />
      <NText v-if="hint" depth="3" class="dir-picker__hint">{{ hint }}</NText>
    </div>
    <div class="dir-picker__value">
      <NText v-if="valueLabel" strong class="dir-picker__label">{{ valueLabel }}</NText>
      <NInput
        :value="path"
        :placeholder="placeholder"
        :disabled="disabled"
        class="dir-picker__input"
        @update:value="(value: string) => emit('update:path', value)"
      />
      <NText v-if="valueHint" depth="3" class="dir-picker__hint">{{ valueHint }}</NText>
    </div>
  </div>
</template>

<style scoped>
/* 视觉对齐仪表盘「编辑任务」第②步：树 + 路径值双栏；align-items:flex-start 让右栏
   不被左栏树高拉伸，min-width:0 防窄屏横向溢出。 */
.dir-picker {
  display: flex;
  align-items: flex-start;
  gap: 16px;
  width: 100%;
  min-width: 0;
}

.dir-picker__tree {
  display: flex;
  flex: 1 1 55%;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}

.dir-picker__value {
  display: flex;
  flex: 1 1 45%;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}

.dir-picker__label {
  font-size: 14px;
}

.dir-picker__hint {
  display: block;
  font-size: 12px;
}

.dir-picker__input {
  width: 100%;
}

/* 窄屏回落单栏（项目断点惯例 ≤960） */
@media (max-width: 960px) {
  .dir-picker {
    flex-direction: column;
  }

  .dir-picker__tree,
  .dir-picker__value {
    flex: 1 1 auto;
    width: 100%;
  }
}
</style>
