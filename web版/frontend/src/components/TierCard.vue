<script setup lang="ts">
import { NCard, NTag, NText } from 'naive-ui'
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { HostMountMode } from '@/api/types'

const props = defineProps<{
  /** 容器内运行用户（/api/auth/me 的 user）。 */
  user: string
  /** /host 挂载模式（/api/auth/me 的 host_mount）。 */
  hostMount: HostMountMode
}>()

const { t } = useI18n()

/**
 * 运行模式卡：只展示**当前部署形态**对应的模式（运行用户 × /host 挂载模式）。
 * 模式名与说明见 i18n `tier.*`；早先的四象限布局已收敛为单模式展示。
 */
const userKey = computed<'root' | 'nonRoot'>(() => (props.user === 'root' ? 'root' : 'nonRoot'))
const mountKey = computed<HostMountMode>(() => props.hostMount)
</script>

<template>
  <n-card :title="t('tier.title')" size="small">
    <div class="tier__cell">
      <n-space align="center" :size="8">
        <n-tag size="small" round type="success">
          {{ t(`tier.name.${userKey}.${mountKey}`) }}
        </n-tag>
        <n-text depth="3">
          {{ t(`tier.user.${userKey}`) }} × {{ t(`tier.mount.${mountKey}`) }}
        </n-text>
      </n-space>
      <n-text class="tier__desc" depth="2">
        {{ t(`tier.desc.${userKey}.${mountKey}`) }}
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
