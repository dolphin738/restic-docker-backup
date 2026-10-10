<script setup lang="ts">
import { NBreadcrumb, NBreadcrumbItem } from 'naive-ui'
import { computed } from 'vue'

/** 原路径面包屑：只展示/上报原路径语义（/etc/nginx），容器前缀由后端处理。 */

const props = defineProps<{
  /** 当前目录（展示形态，如 /etc/nginx）。 */
  path: string
}>()

const emit = defineEmits<{
  (e: 'select', path: string): void
}>()

interface Crumb {
  label: string
  path: string
}

const crumbs = computed<Crumb[]>(() => {
  const clean = (props.path || '/').replace(/\/+$/, '') || '/'
  const parts = clean.split('/').filter(Boolean)
  const list: Crumb[] = [{ label: '/', path: '/' }]
  let acc = ''
  for (const part of parts) {
    acc += `/${part}`
    list.push({ label: part, path: acc })
  }
  return list
})
</script>

<template>
  <n-breadcrumb>
    <n-breadcrumb-item v-for="crumb in crumbs" :key="crumb.path">
      <a @click.prevent="emit('select', crumb.path)">{{ crumb.label }}</a>
    </n-breadcrumb-item>
  </n-breadcrumb>
</template>
