<script setup lang="ts">
/** 批次② 过滤条件编辑器（设计方案 §2.1–§2.3）：
 *   ① 快捷排除卡（扩展名 chips / 名称包含 / 文件大于 / 标记文件 / 跳过缓存）
 *   ② 有序规则列表（负向开关 / glob 输入 / 生效预览 / 忽略大小写 / 上下移 / 删除）
 *   ③ 固定提示条：排除不作用于已选源根目录本身
 *
 * 边界（按设计诚实原则，不做假开关）：
 *   - negate 与 icase 互斥：开启负向即关闭该行 icase（探针证明 --exclude 负向无法取消 --iexclude）；
 *   - glob 语法实时校验，非法即红框 + 原因（后端仍 422 兜底）；
 *   - 「生效预览」直接展示锚定后的容器形态，静默失效类坑在保存前暴露。
 */

import { NButton, NCard, NIcon, NInput, NSwitch, NTag, NText } from 'naive-ui'
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { IconPlusCircle, IconTrash } from '@/components/icons/icons'
import {
  anchorPattern,
  flattenExcludes,
  isValidSize,
  validateGlob,
  type ExcludeRule,
  type ExcludeSpec,
  type GlobError,
} from '@/utils/excludeSpec'

const props = withDefaults(
  defineProps<{
    modelValue: ExcludeSpec
    /** 源根（锚定预览用；由 runtime store 的 browseRoot 注入）。 */
    sourceRoot?: string
  }>(),
  { sourceRoot: '/sources' },
)

const emit = defineEmits<{
  (e: 'update:modelValue', value: ExcludeSpec): void
}>()

const { t } = useI18n()

const extDraft = ref('')
const presentDraft = ref('')
const ruleDraft = ref('')

const quick = computed(() => props.modelValue.quick)
const rules = computed(() => props.modelValue.rules)

const flat = computed(() => flattenExcludes(props.modelValue, props.sourceRoot))
const sizeError = computed(
  () => Boolean(quick.value.largerThan) && !isValidSize(quick.value.largerThan ?? ''),
)

function update(patch: Partial<ExcludeSpec>): void {
  emit('update:modelValue', { ...props.modelValue, ...patch })
}

function updateQuick(patch: Partial<ExcludeSpec['quick']>): void {
  update({ quick: { ...quick.value, ...patch } })
}

function addExtension(): void {
  const value = extDraft.value.trim().replace(/^\./, '')
  extDraft.value = ''
  if (!value || quick.value.extensions.includes(value)) {
    return
  }
  updateQuick({ extensions: [...quick.value.extensions, value] })
}

function removeExtension(value: string): void {
  updateQuick({ extensions: quick.value.extensions.filter((item) => item !== value) })
}

function addPresent(): void {
  const value = presentDraft.value.trim()
  presentDraft.value = ''
  if (!value || quick.value.ifPresent.includes(value)) {
    return
  }
  updateQuick({ ifPresent: [...quick.value.ifPresent, value] })
}

function removePresent(value: string): void {
  updateQuick({ ifPresent: quick.value.ifPresent.filter((item) => item !== value) })
}

function addRule(): void {
  const value = ruleDraft.value.trim()
  if (!value) {
    return
  }
  ruleDraft.value = ''
  update({ rules: [...rules.value, { pattern: value, negate: false, icase: false }] })
}

function patchRule(index: number, patch: Partial<ExcludeRule>): void {
  update({ rules: rules.value.map((rule, i) => (i === index ? { ...rule, ...patch } : rule)) })
}

function removeRule(index: number): void {
  update({ rules: rules.value.filter((_, i) => i !== index) })
}

/** 负向与 icase 互斥：开启负向时强制关闭该行 icase。 */
function setNegate(index: number, negate: boolean): void {
  patchRule(index, negate ? { negate, icase: false } : { negate })
}

function moveRule(index: number, delta: number): void {
  const target = index + delta
  if (target < 0 || target >= rules.value.length) {
    return
  }
  const next = [...rules.value]
  ;[next[index], next[target]] = [next[target], next[index]]
  update({ rules: next })
}

function ruleError(rule: ExcludeRule): GlobError {
  const code = validateGlob(rule.pattern)
  return rule.pattern.trim() ? code : null
}

function errorText(code: GlobError): string {
  return code === 'brace' ? t('excludeEditor.errBrace') : t('excludeEditor.errGlob')
}

/** 生效预览：负向加 ! 前缀，锚定路径补源根。 */
function preview(rule: ExcludeRule): string {
  const anchored = anchorPattern(rule.pattern, props.sourceRoot)
  return rule.negate ? `!${anchored}` : anchored
}
</script>

<template>
  <div class="exclude-editor">
    <!-- ① 快捷排除卡 -->
    <n-card size="small" class="exclude-editor__card">
      <template #header>{{ t('excludeEditor.quickTitle') }}</template>
      <n-text depth="3" class="exclude-editor__desc">{{ t('excludeEditor.quickDesc') }}</n-text>

      <div class="exclude-editor__field">
        <n-text class="exclude-editor__label">{{ t('excludeEditor.extLabel') }}</n-text>
        <div class="exclude-editor__chips">
          <n-tag
            v-for="ext in quick.extensions"
            :key="ext"
            size="small"
            closable
            @close="removeExtension(ext)"
          >
            .{{ ext }}
          </n-tag>
          <n-input
            v-model:value="extDraft"
            size="small"
            class="exclude-editor__inline-input"
            :placeholder="t('excludeEditor.extPlaceholder')"
            @keydown.enter.prevent="addExtension"
          />
          <n-button size="small" quaternary :aria-label="t('excludeEditor.extLabel')" @click="addExtension">
            <template #icon>
              <n-icon :size="16"><IconPlusCircle /></n-icon>
            </template>
          </n-button>
        </div>
      </div>

      <div class="exclude-editor__field">
        <n-text class="exclude-editor__label">{{ t('excludeEditor.nameLabel') }}</n-text>
        <n-input
          :value="quick.nameContains"
          size="small"
          :placeholder="t('excludeEditor.namePlaceholder')"
          @update:value="(v: string) => updateQuick({ nameContains: v })"
        />
      </div>

      <div class="exclude-editor__field">
        <n-text class="exclude-editor__label">{{ t('excludeEditor.sizeLabel') }}</n-text>
        <n-input
          :value="quick.largerThan ?? ''"
          size="small"
          :status="sizeError ? 'error' : undefined"
          :placeholder="t('excludeEditor.sizePlaceholder')"
          @update:value="(v: string) => updateQuick({ largerThan: v.trim() ? v.trim() : null })"
        />
        <n-text v-if="sizeError" type="error" class="exclude-editor__error">
          {{ t('excludeEditor.errSize') }}
        </n-text>
      </div>

      <div class="exclude-editor__field">
        <n-text class="exclude-editor__label">{{ t('excludeEditor.presentLabel') }}</n-text>
        <div class="exclude-editor__chips">
          <n-tag
            v-for="name in quick.ifPresent"
            :key="name"
            size="small"
            closable
            @close="removePresent(name)"
          >
            {{ name }}
          </n-tag>
          <n-input
            v-model:value="presentDraft"
            size="small"
            class="exclude-editor__inline-input"
            :placeholder="t('excludeEditor.presentPlaceholder')"
            @keydown.enter.prevent="addPresent"
          />
          <n-button
            size="small"
            quaternary
            :aria-label="t('excludeEditor.presentLabel')"
            @click="addPresent"
          >
            <template #icon>
              <n-icon :size="16"><IconPlusCircle /></n-icon>
            </template>
          </n-button>
        </div>
      </div>

      <div class="exclude-editor__field exclude-editor__field--row">
        <n-text class="exclude-editor__label">{{ t('excludeEditor.cachesLabel') }}</n-text>
        <n-switch
          :value="quick.caches"
          size="small"
          @update:value="(v: boolean) => updateQuick({ caches: v })"
        />
      </div>
    </n-card>

    <!-- ② 排除规则列表（有序） -->
    <n-card size="small" class="exclude-editor__card">
      <template #header>{{ t('excludeEditor.rulesTitle') }}</template>
      <n-text depth="3" class="exclude-editor__desc">{{ t('excludeEditor.rulesDesc') }}</n-text>

      <n-text v-if="rules.length === 0" depth="3" class="exclude-editor__empty">
        {{ t('excludeEditor.empty') }}
      </n-text>

      <div v-for="(rule, index) in rules" :key="index" class="exclude-editor__rule">
        <div class="exclude-editor__rule-line">
          <n-switch
            :value="rule.negate"
            size="small"
            :aria-label="t('excludeEditor.negateLabel')"
            @update:value="(v: boolean) => setNegate(index, v)"
          />
          <n-input
            :value="rule.pattern"
            size="small"
            class="exclude-editor__rule-input"
            :status="ruleError(rule) ? 'error' : undefined"
            :placeholder="t('excludeEditor.rulePlaceholder')"
            @update:value="(v: string) => patchRule(index, { pattern: v })"
          />
          <n-switch
            v-if="!rule.negate"
            :value="rule.icase"
            size="small"
            :aria-label="t('excludeEditor.icaseLabel')"
            @update:value="(v: boolean) => patchRule(index, { icase: v })"
          />
          <n-button
            size="tiny"
            quaternary
            :aria-label="t('excludeEditor.moveUp')"
            :disabled="index === 0"
            @click="moveRule(index, -1)"
          >
            ↑
          </n-button>
          <n-button
            size="tiny"
            quaternary
            :aria-label="t('excludeEditor.moveDown')"
            :disabled="index === rules.length - 1"
            @click="moveRule(index, 1)"
          >
            ↓
          </n-button>
          <n-button
            size="tiny"
            quaternary
            :aria-label="t('excludeEditor.remove')"
            @click="removeRule(index)"
          >
            <template #icon>
              <n-icon :size="16"><IconTrash /></n-icon>
            </template>
          </n-button>
        </div>
        <n-text v-if="ruleError(rule)" type="error" class="exclude-editor__error">
          {{ errorText(ruleError(rule)) }}
        </n-text>
        <n-text v-else depth="3" class="exclude-editor__preview">
          {{ t('excludeEditor.previewLabel') }}: {{ preview(rule) }}
        </n-text>
      </div>

      <div class="exclude-editor__rule-line exclude-editor__rule-line--add">
        <n-input
          v-model:value="ruleDraft"
          size="small"
          class="exclude-editor__rule-input"
          :placeholder="t('excludeEditor.rulePlaceholder')"
          @keydown.enter.prevent="addRule"
        />
        <n-button size="small" type="primary" :aria-label="t('excludeEditor.addRule')" @click="addRule">
          <template #icon>
            <n-icon :size="16"><IconPlusCircle /></n-icon>
          </template>
        </n-button>
      </div>
    </n-card>

    <!-- ③ 固定提示条 -->
    <n-text depth="3" class="exclude-editor__hint">{{ t('excludeEditor.hintRoot') }}</n-text>

    <!-- 生效预览汇总（调试/自检用，实际下发由父组件取 flat） -->
    <n-text v-if="flat.excludes.length || flat.iexcludes.length" depth="3" class="exclude-editor__summary">
      {{ t('excludeEditor.summary', { n: flat.excludes.length + flat.iexcludes.length }) }}
    </n-text>
  </div>
</template>

<style scoped>
.exclude-editor {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.exclude-editor__desc,
.exclude-editor__hint,
.exclude-editor__empty,
.exclude-editor__summary {
  display: block;
  font-size: 12px;
  margin-bottom: 6px;
}

.exclude-editor__field {
  margin-bottom: 8px;
}

.exclude-editor__field--row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.exclude-editor__label {
  font-size: 12px;
  display: block;
  margin-bottom: 4px;
}

.exclude-editor__chips {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}

.exclude-editor__inline-input {
  max-width: 130px;
}

.exclude-editor__rule {
  margin-bottom: 8px;
}

.exclude-editor__rule-line {
  display: flex;
  align-items: center;
  gap: 6px;
}

.exclude-editor__rule-line--add {
  margin-top: 4px;
}

.exclude-editor__rule-input {
  flex: 1 1 auto;
  min-width: 0;
}

.exclude-editor__preview {
  display: block;
  font-size: 12px;
  word-break: break-all;
}

.exclude-editor__error {
  display: block;
  font-size: 12px;
}
</style>
