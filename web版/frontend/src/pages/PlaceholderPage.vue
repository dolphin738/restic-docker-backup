<script setup lang="ts">
import { NCard, NResult } from 'naive-ui'
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'

const { t } = useI18n()
const route = useRoute()

const pageName = computed(() => (route.meta.titleKey ? t(route.meta.titleKey) : t('common.appName')))
</script>

<template>
  <n-card :title="pageName">
    <n-result status="info" :title="t('placeholder.title')">
      <template #description>
        {{ t('placeholder.description', { name: pageName }) }}
      </template>
    </n-result>
  </n-card>
</template>
