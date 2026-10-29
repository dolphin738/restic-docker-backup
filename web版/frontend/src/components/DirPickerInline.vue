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
 *   2. **点目录名 = 选中（emit + 地址栏跟随），不改 browsePath**：导航交给地址栏/三角形；
 *      VirtualTree 的 selectDir 会自行展开该目录，视觉上仍有下钻反馈。
 *      ⚠️ owner 2026-10-29 裁决：**地址栏须反映选中项**，故地址栏与树根自此**分离**——
 *      地址栏 = `focusPath`（关注/选中），树根 = `browsePath`（浏览）。勿合并回单一状态
 *      （合并会让「选中即下钻」打断连续选目录的流，或让「选中后地址栏不变」再次出现）；
 *   3. **外部赋值才重定位树**：自己 emit 出去的值回流时若也重定位，树会跳回父目录
 *      （按值来源判定，不 watch 派生布尔）。
 *
 * 手输保留：目录可能尚不存在（rclone 会自建、仓库可新建目录），纯树选无法表达
 * 「尚未创建的目录」，故输入框是唯一可表达入口。
 */
import { NButton, NInput, NModal, NSpace, NText, useMessage } from 'naive-ui'
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { browseFs, mkdirFs } from '@/api/fs'
import PathBreadcrumb from '@/components/PathBreadcrumb.vue'
import VirtualTree, { type TreeItem, type TreePage } from '@/components/VirtualTree.vue'

const { t } = useI18n()
const message = useMessage()

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
/** 地址栏显示值 = **关注路径**（选中项 / 导航目标），与树根 `browsePath` 分离
 *  （owner 2026-10-29：选中目录后地址栏须跟着变成选中路径，而树不动）。 */
const focusPath = ref<string>(props.path || browsePath.value)
const mountChips = ref<string[]>([])
/** 最近一次由本组件 emit 出去的值：回流时不重定位树（防「选完树跳回父目录」）。 */
let lastEmitted: string | null = null

const treeRef = ref<InstanceType<typeof VirtualTree> | null>(null)

// ---------------------------------------------------------------- 新建文件夹

const createOpen = ref(false)
const creating = ref(false)
const newFolderName = ref('')
/** 仅承载**客户端校验**文案（名称为空 / 含 /）。API 失败走 http 层统一提示，
 *  不在这里二次弹提示（全站一个提示出口，避免同源错误弹两遍）。 */
const createError = ref('')

/** 建在谁的下面：地址栏当前路径（选中项）；空则退回树根。 */
const createParent = computed(() => (focusPath.value || browsePath.value || ROOT).replace(/\/+$/, '') || ROOT)

function openCreate(): void {
  newFolderName.value = ''
  createError.value = ''
  createOpen.value = true
}

/** 拼接绝对目标路径（`parent` 为根时不再补一个前导斜杠）。 */
function joinPath(parent: string, name: string): string {
  return parent === ROOT ? `${ROOT}${name}` : `${parent}/${name}`
}

async function onCreateFolder(): Promise<void> {
  const name = newFolderName.value.trim()
  if (!name) {
    createError.value = t('fs.nameRequired')
    return
  }
  // 单级语义：父目录必须已存在，故名称里不许再带层级（否则等于要求递归创建）
  if (name.includes('/')) {
    createError.value = t('fs.nameNoSlash')
    return
  }
  const parent = createParent.value
  creating.value = true
  createError.value = ''
  try {
    const result = await mkdirFs(joinPath(parent, name))
    // 建完直接选中它：用户建文件夹的目的就是用它（否则还要回树里再点一次）。
    // 先置 lastEmitted 再 emit —— 回流时不重定位树，树不会跳回父目录。
    lastEmitted = result.path
    focusPath.value = result.path
    emit('update:path', result.path)
    createOpen.value = false
    // 刷新父层 + 展开祖先链：新条目立刻出现在树里（「即时刷新展示」）
    await treeRef.value?.refreshDir(parent)
    message.success(
      result.created
        ? t('fs.created', { name })
        : t('fs.alreadyExists', { name }),
    )
  } catch {
    // API 错误由 http 层统一提示；弹窗保持打开，用户可直接改名重试
  } finally {
    creating.value = false
  }
}

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
  // 地址栏跟随选中（owner 2026-10-29）；树不动——浏览层只由地址栏负责
  focusPath.value = item.path
  lastEmitted = item.path
  emit('update:path', item.path)
}

function jumpTo(path: string): void {
  browsePath.value = path
  focusPath.value = path
}

watch(
  () => props.path,
  (value) => {
    // 地址栏恒反映当前值（无论来自树选中回流还是外部赋值）
    focusPath.value = value ?? ''
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
      <div class="dir-picker__toolbar">
        <PathBreadcrumb :path="focusPath || browsePath" :mounts="mountChips" @select="jumpTo" />
        <!-- tiny 档按 P15 口径保持纯文字（不配图标），与同栏按钮视觉齐平 -->
        <NButton size="tiny" secondary @click="openCreate">{{ t('fs.newFolder') }}</NButton>
      </div>
      <VirtualTree
        ref="treeRef"
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

  <!-- 新建文件夹（容器视角单级创建：父目录必须已存在，不递归） -->
  <NModal
    v-model:show="createOpen"
    preset="card"
    :title="t('fs.newFolderTitle')"
    style="width: min(480px, calc(100vw - 32px))"
  >
    <div class="dir-picker__create">
      <NText depth="3" class="dir-picker__hint">{{ t('fs.newFolderIn', { path: createParent }) }}</NText>
      <NInput
        v-model:value="newFolderName"
        :placeholder="t('fs.newFolderNamePlaceholder')"
        @keydown.enter="onCreateFolder"
      />
      <NText v-if="createError" type="error" class="dir-picker__hint">{{ createError }}</NText>
    </div>
    <template #footer>
      <NSpace justify="end">
        <NButton size="small" @click="createOpen = false">{{ t('common.cancel') }}</NButton>
        <NButton size="small" type="primary" :loading="creating" @click="onCreateFolder">
          {{ t('fs.newFolderCreate') }}
        </NButton>
      </NSpace>
    </template>
  </NModal>
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

/* 地址栏 + 新建按钮同一行：按钮不参与换行（地址栏可挤压），窄屏由外层回落单栏 */
.dir-picker__toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.dir-picker__toolbar > :first-child {
  flex: 1 1 auto;
  min-width: 0;
}

.dir-picker__toolbar > .n-button {
  flex: none;
}

.dir-picker__create {
  display: flex;
  flex-direction: column;
  gap: 8px;
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
