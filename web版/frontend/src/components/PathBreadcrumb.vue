<script setup lang="ts">
/** 路径地址栏（D 形态，《UI全量改造方案》§4.9.9 / P19 owner 裁决「统一成 D」）。
 *
 * 一条控件承载全部「快捷路径」语义，取代原先散落三处的挂载点 chips（A 形态：
 * 13 个挂载点平铺 7 行、长路径顶满行 → 恒定 1 行）：
 *   ① 面包屑：点段名 = 跳到该层（末段除外）；
 *   ② 根段旁的三角 = 展开挂载点菜单（**文字与三角是两段独立热区**，与 VirtualTree
 *      同款手势纪律：点根段只跳转、点三角只开菜单）；
 *   ③ 整条地址 = 一个编辑热区：hover 出覆盖完整地址的虚线框（outline 绘制，
 *      不占布局、无抖动），点空白或末段 = 就地变地址栏；
 *   ④ 地址栏：Enter 提交 / Esc 取消 / 失焦「合法即提交、非法即取消并上报」——
 *      失焦**不保留编辑态**（焦点劫持会让用户 Tab 不走，属反模式）。
 *
 * 复用纪律（S-4）：原地升级既有件，**不新增第二个面包屑组件**；图标取 icons.ts
 * 现有 IconChevronDown；颜色/间距/圆角/缓动一律走 theme/skin.css 的 --rw-* 令牌
 * （亮暗双套，零硬编码色值）。
 *
 * 向后兼容：老调用方（仓库浏览弹窗）只给 `path` + `@select` 即原行为，新增能力
 * 全部可选开启。
 */

import { NIcon, NInput } from 'naive-ui'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { IconChevronDown } from '@/components/icons/icons'

const props = withDefaults(
  defineProps<{
    /** 当前路径（展示形态或容器形态，与调用方同源即可；本组件不做形态换算）。 */
    path: string
    /** 面包屑根段（树根；单一事实源，如 /restore、/sources、/）。 */
    root?: string
    /** 挂载点快跳菜单项（空 = 不渲染三角，退化为纯面包屑）。 */
    mounts?: string[]
    /** 业务校验（越界 / 受保护区域等）：返回 '' 合法，否则为错误文案。
     *  基础形态校验（绝对路径 / 无 `..`）由本组件内置，调用方只需给业务口径。 */
    validate?: (value: string) => string
    /** false = 禁用地址栏（纯面包屑，不可编辑）。 */
    editable?: boolean
  }>(),
  { root: '/', mounts: () => [], validate: undefined, editable: true },
)

const emit = defineEmits<{
  /** 跳转（段名 / 挂载点菜单 / 地址栏提交）。 */
  (e: 'select', path: string): void
  /** 失焦时值非法：已取消编辑，错误文案交父级决定是否提示（组件内不劫持焦点）。 */
  (e: 'invalid', message: string): void
}>()

const { t } = useI18n()

const wrapRef = ref<HTMLElement | null>(null)
const inputRef = ref<InstanceType<typeof NInput> | null>(null)
const menuOpen = ref(false)
const editing = ref(false)
const draft = ref('')
const error = ref('')

interface Crumb {
  label: string
  path: string
}

/** 根段 + 其后各段（与旧实现同算法；root 非 `/` 时根段显示 root 本身）。
 *  路径落在 root 之外时按绝对路径切分（保持可读——越界由 validate 拦，不靠显示隐藏）。 */
const crumbs = computed<Crumb[]>(() => {
  const base = (props.root || '/').replace(/\/+$/, '') || '/'
  const current = (props.path || '/').replace(/\/+$/, '') || '/'
  const out: Crumb[] = [{ label: base, path: base }]
  const rest = base === '/' ? current : current === base ? '' : current.startsWith(`${base}/`) ? current.slice(base.length) : null
  // rest === null：不在 root 下 → 按绝对路径切（首段会是 `/xxx`，与根段并列显示）
  const tail = rest === null ? current : rest
  // 根为 `/` 时累加自空串起（否则拼成 `//mnt`——与后端 `//` 前缀判定同款坑）
  let acc = rest === null || base === '/' ? '' : base
  for (const part of tail.split('/').filter(Boolean)) {
    acc += `/${part}`
    out.push({ label: part, path: acc })
  }
  return out
})

const lastIndex = computed(() => crumbs.value.length - 1)

/** 归一化：去空白与尾随斜杠（根本身保留 `/`）。 */
function normalize(value: string): string {
  const trimmed = value.trim()
  if (!trimmed) {
    return '/'
  }
  const collapsed = trimmed.replace(/\/{2,}/g, '/').replace(/\/+$/, '')
  return collapsed || '/'
}

/** 基础形态校验（内置口径：绝对路径 + 无 `..` 穿越段）。 */
function basicError(value: string): string {
  if (!value.startsWith('/')) {
    return t('pathBar.errAbsolute')
  }
  if (value.split('/').includes('..')) {
    return t('pathBar.errTraversal')
  }
  return ''
}

/** 完整校验：基础 + 业务（业务口径由调用方注入，组件不猜）。 */
function errorOf(value: string): string {
  return basicError(value) || (props.validate?.(value) ?? '')
}

function startEdit(): void {
  if (!props.editable || editing.value) {
    return
  }
  draft.value = props.path
  error.value = ''
  editing.value = true
  menuOpen.value = false
  void nextTick(() => inputRef.value?.focus())
}

function cancelEdit(): void {
  editing.value = false
  error.value = ''
  draft.value = ''
}

/** Enter 提交：非法**保留编辑态**并内联报错（Enter 是显式提交，用户可继续改或 Esc）。 */
function commit(): void {
  const value = normalize(draft.value)
  const err = errorOf(value)
  if (err) {
    error.value = err
    return
  }
  editing.value = false
  error.value = ''
  emit('select', value)
}

/** 失焦：合法即提交；非法即取消 + 上报（不保留编辑态 → 不劫持焦点）。 */
function onBlur(): void {
  if (!editing.value) {
    return
  }
  const value = normalize(draft.value)
  const err = errorOf(value)
  if (err) {
    emit('invalid', err)
    cancelEdit()
    return
  }
  editing.value = false
  error.value = ''
  emit('select', value)
}

function toggleMenu(): void {
  menuOpen.value = !menuOpen.value
}

function jump(path: string): void {
  menuOpen.value = false
  emit('select', path)
}

/** 点击组件外关闭挂载点菜单（编辑态不自动退——输入中误点外部会丢输入）。 */
function onDocClick(event: MouseEvent): void {
  if (!menuOpen.value) {
    return
  }
  if (wrapRef.value && !wrapRef.value.contains(event.target as Node)) {
    menuOpen.value = false
  }
}

onMounted(() => document.addEventListener('click', onDocClick))
onBeforeUnmount(() => document.removeEventListener('click', onDocClick))

// 外部换路径（树点击 / 父级重置）时收起菜单与错误提示，避免残留在旧路径上
watch(
  () => props.path,
  () => {
    menuOpen.value = false
    error.value = ''
  },
)
</script>

<template>
  <div ref="wrapRef" class="pcrumb" :class="{ 'pcrumb--static': !editable }">
    <div
      v-if="editing"
      class="pcrumb__edit"
    >
      <n-input
        ref="inputRef"
        v-model:value="draft"
        size="small"
        class="pcrumb__input"
        :aria-label="t('pathBar.inputAria')"
        @keydown.enter.prevent="commit"
        @keydown.esc.prevent="cancelEdit"
        @blur="onBlur"
      />
      <span class="pcrumb__keys">{{ t('pathBar.keysHint') }}</span>
    </div>

    <div
      v-else
      class="pcrumb__bar"
      :class="{ 'pcrumb__bar--addr': editable }"
      :title="editable ? t('pathBar.editHint') : undefined"
      @click="startEdit"
    >
      <button
        type="button"
        class="pcrumb__seg"
        @click.stop="jump(crumbs[0].path)"
      >
        {{ crumbs[0].label }}
      </button>
      <button
        v-if="mounts.length > 0"
        type="button"
        class="pcrumb__caret"
        :class="{ 'pcrumb__caret--open': menuOpen }"
        :aria-expanded="menuOpen"
        :aria-label="t('pathBar.expandMounts')"
        @click.stop="toggleMenu"
        @keydown.esc.prevent="menuOpen = false"
      >
        <n-icon :size="14"><IconChevronDown /></n-icon>
      </button>
      <template v-for="(crumb, index) in crumbs.slice(1)" :key="crumb.path">
        <span class="pcrumb__sep">/</span>
        <button
          v-if="index + 1 < lastIndex"
          type="button"
          class="pcrumb__seg"
          @click.stop="jump(crumb.path)"
        >
          {{ crumb.label }}
        </button>
        <span v-else class="pcrumb__seg pcrumb__seg--last">{{ crumb.label }}</span>
      </template>
      <!-- 末段右侧空白占位：撑满剩余宽度，与整条地址同属一个编辑热区 -->
      <span class="pcrumb__tail" aria-hidden="true"></span>
    </div>

    <div v-if="menuOpen" class="pcrumb__menu">
      <button
        v-for="mount in mounts"
        :key="mount"
        type="button"
        class="pcrumb__menu-item"
        @click.stop="jump(mount)"
      >
        {{ mount }}
      </button>
    </div>

    <div v-if="error" class="pcrumb__error">{{ error }}</div>
  </div>
</template>

<style scoped>
/* 样式纪律（§4.6.4 彻底档 / P5）：颜色/间距/圆角/缓动一律走 skin.css 的 --rw-* 令牌，
   本文件零硬编码色值；混色用 color-mix 派生。 */

.pcrumb {
  position: relative;
  min-width: 0;
  font-size: 12px;
}

/* 整条地址 = 一个热区：hover 时虚线框覆盖**完整地址范围**（outline 不占布局 →
   不引起行高变化或换行抖动；演示页实测 A 190px/8 行 → D 恒定 25px/1 行）。 */
.pcrumb__bar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--rw-space-1);
  min-width: 0;
  font-family: var(--font-family-mono);
  border-radius: var(--rw-radius-control);
}

.pcrumb__bar--addr {
  cursor: text;
  outline: 1px dashed transparent;
  outline-offset: 3px;
  transition: outline-color 0.15s var(--rw-ease), background-color 0.15s var(--rw-ease);
}

.pcrumb__bar--addr:hover {
  outline-color: color-mix(in srgb, var(--rw-focus) 40%, var(--rw-border));
  background: color-mix(in srgb, var(--rw-surface-hover) 70%, transparent);
}

/* 段名：hover 用主色 + 浅底，与整框虚线不打架（不再叠一层边框） */
.pcrumb__seg {
  max-width: 100%;
  padding: 2px 6px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: inherit;
  font: inherit;
  cursor: pointer;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pcrumb__bar--addr:hover .pcrumb__seg:hover {
  color: var(--rw-focus);
  background: color-mix(in srgb, var(--rw-focus) 12%, transparent);
}

.pcrumb__seg--last {
  font-weight: 600;
  cursor: text;
}

.pcrumb__sep {
  color: color-mix(in srgb, var(--rw-border) 60%, var(--rw-focus));
}

/* 三角热区 ≥24px（owner 反馈 20px 太难点；与 VirtualTree caret 同款放大纪律） */
.pcrumb__caret {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: color-mix(in srgb, var(--rw-focus) 35%, var(--rw-border));
  cursor: pointer;
  transition: transform 0.15s var(--rw-ease);
}

.pcrumb__caret:hover {
  border-color: var(--rw-border);
  color: var(--rw-focus);
}

.pcrumb__caret--open {
  transform: rotate(180deg);
  color: var(--rw-focus);
}

.pcrumb__tail {
  flex: 1 1 auto;
  min-width: 24px;
  align-self: stretch;
}

/* 挂载点菜单：就地展开（不用 teleport 下拉——VTU 中可查、暗色继承不依赖 portal） */
.pcrumb__menu {
  position: absolute;
  z-index: 2;
  top: calc(100% + var(--rw-space-1));
  left: 0;
  min-width: 180px;
  max-width: 100%;
  max-height: 240px;
  overflow-y: auto;
  padding: var(--rw-space-1);
  border: 1px solid var(--rw-border);
  border-radius: var(--rw-radius-control);
  background: var(--rw-surface);
  box-shadow: var(--rw-shadow-hover);
}

.pcrumb__menu-item {
  display: block;
  width: 100%;
  padding: 3px 6px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.pcrumb__menu-item:hover {
  color: var(--rw-focus);
  background: var(--rw-surface-hover);
}

.pcrumb__edit {
  display: flex;
  align-items: center;
  gap: var(--rw-space-2);
}

.pcrumb__input {
  flex: 1 1 auto;
  min-width: 0;
  font-family: var(--font-family-mono);
}

.pcrumb__keys {
  flex: none;
  font-size: 11px;
  color: color-mix(in srgb, var(--rw-border) 40%, currentColor);
  white-space: nowrap;
}

.pcrumb__error {
  margin-top: var(--rw-space-1);
  font-size: 12px;
  color: var(--rw-danger);
}
</style>
