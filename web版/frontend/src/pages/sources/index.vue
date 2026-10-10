<script setup lang="ts">
import { useQuery } from '@tanstack/vue-query'
import {
  NAlert,
  NButton,
  NCard,
  NCollapse,
  NCollapseItem,
  NDrawer,
  NDrawerContent,
  NEmpty,
  NInput,
  NSpace,
  NTag,
  NText,
  useMessage,
} from 'naive-ui'
import { computed, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { listPolicies, updatePolicy } from '@/api/policies'
import { browseSources, dryRunSources, getExclusions } from '@/api/sources'
import type { DryRunResponse } from '@/api/types'
import PathBreadcrumb from '@/components/PathBreadcrumb.vue'
import VirtualTree, { type TreePage } from '@/components/VirtualTree.vue'
import { useUiStore } from '@/stores/ui'

const { t } = useI18n()
const ui = useUiStore()

const message = useMessage()
ui.setMessageApi(message)
onUnmounted(() => ui.setMessageApi(null))

// ---------------------------------------------------------------- 目录浏览
const currentPath = ref('/')
const drawerShow = ref(false)

function treeLoader(path: string, cursor?: string): Promise<TreePage> {
  return browseSources(path, cursor).then((page) => ({
    items: page.items.map((item) => ({
      key: item.display_path,
      name: item.name,
      type: item.type,
      path: item.display_path,
    })),
    nextCursor: page.next_cursor,
  }))
}

// ---------------------------------------------------------------- 内置排除集（E32，只读）
const exclusionsQuery = useQuery({ queryKey: ['exclusions'], queryFn: () => getExclusions() })

/** E32 直接回剥好前缀的展示形态（display_core ⊆ display_items），前端不再做前缀转换。 */
const coreRules = computed(() => exclusionsQuery.data.value?.display_core ?? [])
const extraRules = computed(() => {
  const data = exclusionsQuery.data.value
  if (!data) {
    return []
  }
  const coreSet = new Set(data.display_core)
  return data.display_items.filter((rule) => !coreSet.has(rule))
})

// ---------------------------------------------------------------- 备份源（默认策略）
const policiesQuery = useQuery({ queryKey: ['policies'], queryFn: () => listPolicies() })

const defaultPolicy = computed(() =>
  (policiesQuery.data.value?.items ?? []).find((policy) => policy.is_default) ?? null,
)

const selectedSources = ref<string[]>([])
const appendedExcludes = ref<string[]>([])
const excludeDraft = ref('')
const saving = ref(false)

function addExclude(): void {
  const value = excludeDraft.value.trim()
  if (!value) {
    return
  }
  if (appendedExcludes.value.includes(value)) {
    excludeDraft.value = ''
    return
  }
  appendedExcludes.value = appendedExcludes.value.concat(value)
  excludeDraft.value = ''
}

function removeSource(path: string): void {
  selectedSources.value = selectedSources.value.filter((item) => item !== path)
}

function removeExclude(rule: string): void {
  appendedExcludes.value = appendedExcludes.value.filter((item) => item !== rule)
}

// ---------------------------------------------------------------- dry-run 门（E31 → 确认）
const dryRunShow = ref(false)
const dryRunLoading = ref(false)
const dryRunResult = ref<DryRunResponse | null>(null)

async function onPreview(): Promise<void> {
  if (selectedSources.value.length === 0) {
    message.warning(t('sources.needSource'))
    return
  }
  dryRunShow.value = true
  dryRunLoading.value = true
  dryRunResult.value = null
  try {
    dryRunResult.value = await dryRunSources(selectedSources.value, appendedExcludes.value)
  } finally {
    dryRunLoading.value = false
  }
}

async function onConfirmSources(): Promise<void> {
  const policy = defaultPolicy.value
  if (!policy) {
    return
  }
  saving.value = true
  try {
    await updatePolicy(policy.id, {
      name: policy.name,
      enabled: policy.enabled,
      tag: policy.tag,
      sources: selectedSources.value,
      excludes: appendedExcludes.value,
      keep_last: policy.keep_last,
      keep_hourly: policy.keep_hourly,
      keep_daily: policy.keep_daily,
      keep_weekly: policy.keep_weekly,
      keep_monthly: policy.keep_monthly,
      keep_yearly: policy.keep_yearly,
      group_by: policy.group_by,
      cron: policy.cron,
      one_file_system: null,
      compression: policy.compression,
      check_sample_ratio: policy.check_sample_ratio,
      monthly_full_day: policy.monthly_full_day,
      prune_interval_days: policy.prune_interval_days,
      limit_up: policy.limit_up,
      limit_down: policy.limit_down,
    })
    message.success(t('sources.saved'))
    dryRunShow.value = false
    await policiesQuery.refetch()
  } finally {
    saving.value = false
  }
}

// 策略与内置集都就绪后回填（E32 内置集之外的都是用户追加项）
function hydrateFromPolicy(): void {
  const policy = defaultPolicy.value
  if (!policy) {
    return
  }
  selectedSources.value = [...policy.sources]
  const builtin = new Set(exclusionsQuery.data.value?.display_items ?? [])
  appendedExcludes.value = policy.excludes.filter((rule) => !builtin.has(rule))
}

watch(
  () => [defaultPolicy.value?.id, exclusionsQuery.data.value?.total] as const,
  () => hydrateFromPolicy(),
  { immediate: true },
)

/** 把当前浏览目录加入备份源（原路径语义）。 */
function addCurrentDir(): void {
  const path = currentPath.value
  if (!path || selectedSources.value.includes(path)) {
    return
  }
  selectedSources.value = selectedSources.value.concat(path)
}
</script>

<template>
  <n-space vertical :size="16">
    <n-grid cols="1 1100:2" :x-gap="16" :y-gap="16" responsive="screen">
      <!-- 目录树浏览 -->
      <n-gi>
        <n-card :title="t('sources.treeTitle')" size="small">
          <n-space vertical :size="10">
            <PathBreadcrumb :path="currentPath" @select="currentPath = $event" />
            <VirtualTree :root-path="currentPath" :load-children="treeLoader" :height="420" />
            <n-space>
              <n-button size="small" type="primary" @click="addCurrentDir">
                {{ t('sources.addCurrentDir', { path: currentPath }) }}
              </n-button>
              <n-button size="small" quaternary @click="drawerShow = true">
                {{ t('sources.treeDrawer') }}
              </n-button>
            </n-space>
          </n-space>
        </n-card>
      </n-gi>

      <!-- 源与排除集 -->
      <n-gi>
        <n-card :title="t('sources.sourcesTitle')" size="small">
          <n-space vertical :size="12">
            <n-text strong>{{ t('sources.selected') }}</n-text>
            <n-empty v-if="selectedSources.length === 0" :description="t('sources.selectedEmpty')" size="small" />
            <n-space v-else :size="6">
              <n-tag
                v-for="path in selectedSources"
                :key="path"
                size="small"
                closable
                @close="removeSource(path)"
              >
                {{ path }}
              </n-tag>
            </n-space>

            <n-text strong>{{ t('sources.exclusionsTitle') }}</n-text>
            <n-text depth="3">{{ t('sources.forcedNote') }}</n-text>
            <n-collapse size="small">
              <n-collapse-item :title="t('sources.coreGroup', { n: coreRules.length })" name="core">
                <n-tag v-for="rule in coreRules" :key="rule" size="small" class="sources__rule" round>
                  {{ rule }}
                </n-tag>
              </n-collapse-item>
              <n-collapse-item :title="t('sources.extraGroup', { n: extraRules.length })" name="extra">
                <n-tag v-for="rule in extraRules" :key="rule" size="small" class="sources__rule" round>
                  {{ rule }}
                </n-tag>
              </n-collapse-item>
            </n-collapse>

            <n-text strong>{{ t('sources.appendTitle') }}</n-text>
            <n-space :size="6">
              <n-input
                v-model:value="excludeDraft"
                size="small"
                :placeholder="t('sources.appendPlaceholder')"
                @keydown.enter.prevent="addExclude"
              />
              <n-button size="small" @click="addExclude">{{ t('sources.appendAdd') }}</n-button>
            </n-space>
            <n-space v-if="appendedExcludes.length" :size="6">
              <n-tag
                v-for="rule in appendedExcludes"
                :key="rule"
                size="small"
                closable
                @close="removeExclude(rule)"
              >
                {{ rule }}
              </n-tag>
            </n-space>

            <n-space>
              <n-button type="primary" :loading="dryRunLoading" @click="onPreview">
                {{ t('sources.dryRunBtn') }}
              </n-button>
            </n-space>
          </n-space>
        </n-card>
      </n-gi>
    </n-grid>

    <!-- dry-run 预览 → 确认门禁 -->
    <n-drawer v-model:show="dryRunShow" :width="680" placement="right">
      <n-drawer-content :title="t('sources.dryRunTitle')" closable>
        <n-space vertical :size="12">
          <n-alert v-if="dryRunLoading" type="default" :show-icon="false">{{ t('common.loading') }}</n-alert>
          <template v-else-if="dryRunResult">
            <n-alert v-if="dryRunResult.error" type="error" :show-icon="false">
              {{ dryRunResult.error }}
            </n-alert>
            <n-alert v-if="dryRunResult.truncated" type="warning" :show-icon="false">
              {{ t('sources.dryRunTruncated') }}
            </n-alert>
            <template v-if="dryRunResult.warnings.length">
              <n-alert type="warning" :title="t('sources.pseudoWarningTitle')" :show-icon="false">
                <n-space vertical :size="2">
                  <n-text v-for="warning in dryRunResult.warnings" :key="warning.path" depth="3">
                    {{ warning.path }}
                  </n-text>
                </n-space>
              </n-alert>
            </template>
            <n-text depth="3">{{ t('sources.dryRunCount', { n: dryRunResult.items.length }) }}</n-text>
            <n-space :size="6" class="sources__preview">
              <n-tag v-for="item in dryRunResult.items.slice(0, 200)" :key="item.display_path" size="small">
                {{ item.display_path }}
              </n-tag>
            </n-space>
            <n-space justify="end">
              <n-button quaternary @click="dryRunShow = false">{{ t('common.cancel') }}</n-button>
              <n-button
                type="primary"
                :loading="saving"
                :disabled="dryRunResult.error !== null"
                @click="onConfirmSources"
              >
                {{ t('sources.confirmSave') }}
              </n-button>
            </n-space>
          </template>
        </n-space>
      </n-drawer-content>
    </n-drawer>

    <!-- 文件级浏览抽屉（与树同数据源，供窄屏使用） -->
    <n-drawer v-model:show="drawerShow" :width="640" placement="right">
      <n-drawer-content :title="t('sources.treeDrawer')" closable>
        <n-space vertical :size="10">
          <PathBreadcrumb :path="currentPath" @select="currentPath = $event" />
          <VirtualTree :root-path="currentPath" :load-children="treeLoader" :height="480" />
        </n-space>
      </n-drawer-content>
    </n-drawer>
  </n-space>
</template>

<style scoped>
.sources__rule {
  margin: 0 4px 4px 0;
}

.sources__preview {
  max-height: 240px;
  overflow-y: auto;
}
</style>
