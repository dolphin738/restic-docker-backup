<script setup lang="ts">
/** 任务向导页（路由 tasks/new 与 tasks/:id/edit 共用；create/edit 双模式）。
 * 模式由路由参数决定：有 id = 编辑（E34b 预填），无 id = 新建。
 */

import { computed } from 'vue'
import { useRoute } from 'vue-router'
import TaskWizard from '@/components/TaskWizard.vue'

const route = useRoute()

const taskId = computed<number | null>(() => {
  const raw = route.params.id
  if (typeof raw !== 'string' || raw === '') {
    return null
  }
  const value = Number(raw)
  return Number.isInteger(value) && value > 0 ? value : null
})
</script>

<template>
  <TaskWizard :task-id="taskId" />
</template>
