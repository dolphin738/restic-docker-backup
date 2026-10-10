<script setup lang="ts">
import { NAlert, NButton, NCard, NInput, NModal, NStep, NSteps, NSpace, NText } from 'naive-ui'
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

/** 危险操作三步门（§5 贯穿性安全交互规范）：预览 → 输入确认文本 → 二次确认。
 * 预览区是可插拔插槽（#preview），无预览数据也能工作（只显示 description）。
 */

const props = defineProps<{
  show: boolean
  title: string
  /** 用户必须原样输入的确认文本（建议用大写动作词，如 UNLOCK / RESTORE）。 */
  keyword: string
  description?: string
  /** 确认动作执行中：禁用确认按钮并显示 loading。 */
  loading?: boolean
}>()

const emit = defineEmits<{
  (e: 'update:show', value: boolean): void
  (e: 'confirm'): void
}>()

const { t } = useI18n()

/** 1=预览 2=输入确认文本 3=二次确认 */
const step = ref(1)
const typed = ref('')

const keywordOk = computed(() => typed.value.trim() === props.keyword)

watch(
  () => props.show,
  (visible) => {
    if (visible) {
      step.value = 1
      typed.value = ''
    }
  },
)

function close(): void {
  emit('update:show', false)
}

function onConfirm(): void {
  emit('confirm')
  close()
}
</script>

<template>
  <n-modal :show="show" @update:show="close">
    <n-card style="width: 560px" :title="title" closable @close="close">
      <n-steps :current="step" size="small">
        <n-step :title="t('confirm.stepPreview')" />
        <n-step :title="t('confirm.stepKeyword')" />
        <n-step :title="t('confirm.stepFinal')" />
      </n-steps>

      <div class="confirm__body">
        <!-- 第 1 步：预览（可插拔） -->
        <template v-if="step === 1">
          <n-alert v-if="description" type="warning" :show-icon="false">{{ description }}</n-alert>
          <slot name="preview" />
          <n-space justify="end">
            <n-button quaternary @click="close">{{ t('common.cancel') }}</n-button>
            <n-button type="primary" @click="step = 2">{{ t('confirm.next') }}</n-button>
          </n-space>
        </template>

        <!-- 第 2 步：输入确认文本 -->
        <template v-else-if="step === 2">
          <n-text depth="3">{{ t('confirm.keywordHint', { keyword }) }}</n-text>
          <n-input v-model:value="typed" :placeholder="keyword" />
          <n-space justify="end">
            <n-button @click="step = 1">{{ t('confirm.back') }}</n-button>
            <n-button type="warning" :disabled="!keywordOk" @click="step = 3">
              {{ t('confirm.next') }}
            </n-button>
          </n-space>
        </template>

        <!-- 第 3 步：二次确认 -->
        <template v-else>
          <n-alert type="warning" :show-icon="false">{{ t('confirm.finalHint') }}</n-alert>
          <n-space justify="end">
            <n-button @click="step = 2">{{ t('confirm.back') }}</n-button>
            <n-button type="error" :loading="loading" @click="onConfirm">
              {{ t('confirm.confirmBtn') }}
            </n-button>
          </n-space>
        </template>
      </div>
    </n-card>
  </n-modal>
</template>

<style scoped>
.confirm__body {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 16px;
}
</style>
