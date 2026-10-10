<script setup lang="ts">
import { NCard, NGi, NGrid, NTag, NText } from 'naive-ui'
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

interface Cell {
  key: string
  userKey: 'root' | 'nonRoot'
  mount: HostMountMode
  current: boolean
}

const isRoot = computed(() => props.user === 'root')

const cells = computed<Cell[]>(() => {
  const users: Array<'root' | 'nonRoot'> = ['root', 'nonRoot']
  const mounts: HostMountMode[] = ['ro', 'rw']
  const list: Cell[] = []
  for (const userKey of users) {
    for (const mount of mounts) {
      list.push({
        key: `${userKey}:${mount}`,
        userKey,
        mount,
        current: (userKey === 'root') === isRoot.value && mount === props.hostMount,
      })
    }
  }
  return list
})
</script>

<template>
  <n-card :title="t('quadrant.title')" size="small">
    <n-grid :cols="2" :x-gap="12" :y-gap="12">
      <n-gi v-for="cell in cells" :key="cell.key">
        <div class="quadrant__cell" :class="{ 'quadrant__cell--current': cell.current }">
          <n-space align="center" :size="6">
            <n-text :depth="cell.current ? 1 : 3" strong>{{ t(`quadrant.${cell.userKey}`) }}</n-text>
            <n-text depth="3">×</n-text>
            <n-text :depth="cell.current ? 1 : 3" strong>{{ t(`quadrant.${cell.mount}`) }}</n-text>
            <template v-if="cell.current">
              <n-tag size="tiny" round type="success">{{ t('quadrant.currentTag') }}</n-tag>
            </template>
          </n-space>
          <n-text class="quadrant__desc" :depth="cell.current ? 2 : 3">
            {{ t(`quadrant.combos.${cell.userKey}.${cell.mount}`) }}
          </n-text>
        </div>
      </n-gi>
    </n-grid>
  </n-card>
</template>

<style scoped>
.quadrant__cell {
  height: 100%;
  padding: 12px;
  border: 1px solid rgba(128, 128, 128, 0.28);
  border-radius: 6px;
}

.quadrant__cell--current {
  border-color: rgba(99, 226, 183, 0.75);
  background: rgba(99, 226, 183, 0.08);
}

.quadrant__desc {
  display: block;
  margin-top: 8px;
  font-size: 12px;
  line-height: 1.6;
}
</style>
