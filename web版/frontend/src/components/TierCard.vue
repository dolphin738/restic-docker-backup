<script setup lang="ts">
import { NCard, NSpace, NTag, NText } from 'naive-ui'
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps<{
  /** 容器内运行用户（/api/auth/me 的 user）。 */
  user: string
}>()

const { t } = useI18n()

/**
 * 运行模式卡（A0 模式收敛后）：只按**运行用户**展示当前部署形态。
 * 模式名与说明见 i18n `tier.*`；早先的「用户 × /host 挂载」两轴与四象限布局均已收敛。
 */
const userKey = computed<'root' | 'nonRoot'>(() => (props.user === 'root' ? 'root' : 'nonRoot'))
</script>

<template>
  <n-card :title="t('tier.title')" size="small">
    <div class="tier__cell">
      <n-space align="center" :size="8">
        <n-tag size="small" round type="success">
          {{ t(`tier.name.${userKey}`) }}
        </n-tag>
        <n-text depth="3">
          {{ t(`tier.user.${userKey}`) }}
        </n-text>
      </n-space>
      <n-text class="tier__desc" depth="2">
        {{ t(`tier.desc.${userKey}`) }}
      </n-text>
      <n-text depth="3" class="tier__note">{{ t('tier.note') }}</n-text>
    </div>
  </n-card>
</template>

<style scoped>
.tier__cell {
  padding: 12px;
  border: 1px solid rgba(128, 128, 128, 0.28);
  border-radius: 6px;
}

.tier__desc {
  display: block;
  margin-top: 8px;
  font-size: 12px;
  line-height: 1.6;
}

.tier__note {
  display: block;
  margin-top: 10px;
  font-size: 12px;
}
</style>
