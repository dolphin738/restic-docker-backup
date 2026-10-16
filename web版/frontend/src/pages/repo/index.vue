<script setup lang="ts">
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import {
  NAlert,
  NButton,
  NCard,
  NDataTable,
  NForm,
  NFormItem,
  NGrid,
  NGi,
  NInput,
  NModal,
  NSpace,
  NStatistic,
  NTabPane,
  NTabs,
  NTag,
  NText,
  NTooltip,
  useMessage,
  type DataTableColumns,
} from 'naive-ui'
import { computed, h, onUnmounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { browseFs } from '@/api/fs'
import { getConfig, putConfig } from '@/api/config'
import { createJob } from '@/api/jobs'
import { createRepo, deleteRepo, getRepo, getRepoStatus, listRepos, testRepo, updateRepo } from '@/api/repo'
import { isAppError } from '@/api/http'
import type { ConfigItem, RepoOut, RepoTestResponse, RepoUpdateRequest } from '@/api/types'
import ConfirmGate from '@/components/ConfirmGate.vue'
import JobProgress from '@/components/JobProgress.vue'
import PathBreadcrumb from '@/components/PathBreadcrumb.vue'
import VirtualTree, { type TreeItem, type TreePage } from '@/components/VirtualTree.vue'
import { useUiStore } from '@/stores/ui'

const { t } = useI18n()
const ui = useUiStore()
const queryClient = useQueryClient()

const message = useMessage()
ui.setMessageApi(message)
onUnmounted(() => ui.setMessageApi(null))

// ---------------------------------------------------------------- 仓库列表（R-023）
const reposQuery = useQuery({ queryKey: ['repos'], queryFn: listRepos })

function onSetCurrent(id: string): void {
  ui.setCurrentRepoId(id)
  // 切换仓库：所有仓库域查询（快照/任务/状态/仪表盘）带新 X-Repo-Id 重取
  void queryClient.invalidateQueries()
}

// ---------------------------------------------------------------- 新建仓库（R-023）
const createShow = ref(false)
const creating = ref(false)
const createForm = reactive({ id: '', name: '', url: '', hostTag: '' })

function openCreate(): void {
  createForm.id = ''
  createForm.name = ''
  createForm.url = ''
  createForm.hostTag = ''
  createShow.value = true
}

async function onCreate(): Promise<void> {
  if (!createForm.id.trim()) {
    message.warning(t('repo.idRequired'))
    return
  }
  if (!createForm.name.trim()) {
    message.warning(t('repo.nameRequired'))
    return
  }
  creating.value = true
  try {
    const created = await createRepo({
      id: createForm.id.trim(),
      name: createForm.name.trim(),
      url: createForm.url.trim() || null,
      host_tag: createForm.hostTag.trim() || null,
    })
    message.success(t('repo.created'))
    createShow.value = false
    await reposQuery.refetch()
    onSetCurrent(created.id)
  } catch (error) {
    // AppError（如 id 重复 → 409 / 422）已由全局提示展示
    if (!isAppError(error)) {
      throw error
    }
  } finally {
    creating.value = false
  }
}

// ---------------------------------------------------------------- 删除仓库（R-023）
const deleting = ref(false)
const deleteShow = ref(false)
const deleteTarget = ref<string | null>(null)

function onDeleteClick(id: string): void {
  if (id === 'default') {
    message.warning(t('repo.deleteDefaultForbidden'))
    return
  }
  deleteTarget.value = id
  deleteShow.value = true
}

async function confirmDelete(): Promise<void> {
  const id = deleteTarget.value
  if (!id) {
    return
  }
  deleting.value = true
  try {
    await deleteRepo(id)
    message.success(t('repo.deleted'))
    if (ui.currentRepoId === id) {
      onSetCurrent('default')
    }
    await reposQuery.refetch()
  } catch (error) {
    if (!isAppError(error)) {
      throw error
    }
  } finally {
    deleting.value = false
    deleteTarget.value = null
    deleteShow.value = false
  }
}

function typeLabel(ty: string): string {
  return ty === 's3' ? t('repo.typeS3') : ty === 'b2' ? t('repo.typeB2') : t('repo.typeLocal')
}

const columns = computed<DataTableColumns<RepoOut>>(() => [
  {
    title: t('repo.colId'),
    key: 'id',
    render: (row) =>
      h(NSpace, { align: 'center', size: 6 }, () => [
        row.id,
        row.id === 'default'
          ? h(NTag, { size: 'tiny', type: 'warning', round: true }, () => t('repo.defaultTag'))
          : null,
        row.id === ui.currentRepoId
          ? h(NTag, { size: 'tiny', type: 'success', round: true }, () => t('repo.currentTag'))
          : null,
      ]),
  },
  { title: t('repo.colName'), key: 'name' },
  { title: t('repo.colType'), key: 'type', render: (row) => typeLabel(row.type) },
  { title: t('repo.colUrl'), key: 'url', render: (row) => row.url ?? '—' },
  { title: t('repo.colHostTag'), key: 'host_tag', render: (row) => row.host_tag ?? '—' },
  {
    title: '',
    key: 'actions',
    width: 170,
    render: (row) =>
      h(NSpace, { size: 6 }, () => [
        h(
          NButton,
          {
            size: 'tiny',
            secondary: true,
            type: row.id === ui.currentRepoId ? 'primary' : 'default',
            disabled: row.id === ui.currentRepoId,
            onClick: () => onSetCurrent(row.id),
          },
          () => t('repo.setCurrent'),
        ),
        h(
          NButton,
          {
            size: 'tiny',
            secondary: true,
            type: 'error',
            disabled: row.id === 'default',
            onClick: () => onDeleteClick(row.id),
          },
          () => t('repo.delete'),
        ),
      ]),
  },
])

// ---------------------------------------------------------------- 当前仓库配置（R-023）
const currentRepoId = computed(() => ui.currentRepoId)

const repoQuery = useQuery({
  queryKey: ['repo', currentRepoId],
  queryFn: () => getRepo(currentRepoId.value),
})

const statusQuery = useQuery({
  queryKey: ['repo', 'status', currentRepoId],
  queryFn: () => getRepoStatus(currentRepoId.value),
  refetchInterval: 60_000,
})

const repo = computed(() => repoQuery.data.value ?? null)
const repoType = computed<RepoTypeKey>(() => {
  const ty = repo.value?.type
  return ty === 's3' || ty === 'b2' ? ty : 'local'
})

const form = reactive({ url: '', hostTag: '' })
const initial = reactive({ url: '', hostTag: '' })
watch(
  () => repo.value,
  (r) => {
    if (!r) {
      return
    }
    form.url = r.url ?? ''
    form.hostTag = r.host_tag ?? ''
    initial.url = form.url
    initial.hostTag = form.hostTag
  },
  { immediate: true },
)

// 切换仓库时清空凭据输入与浏览态，避免残留
watch(currentRepoId, () => {
  clearCredInput()
  urlMode.value = 'manual'
})

// ---------------------------------------------------------------- 凭据（按仓库命名空间）
const configQuery = useQuery({ queryKey: ['config'], queryFn: getConfig })

type RepoTypeKey = 'local' | 's3' | 'b2'
interface CredField {
  suffix: string
  labelKey: string
}

/** 凭据键表（§4.3）：基础后缀；完整键 = cred.{repo_id}.{suffix}（R-023 命名空间化）。 */
const CRED_FIELDS: Record<RepoTypeKey, CredField[]> = {
  local: [],
  s3: [
    { suffix: 'aws_access_key_id', labelKey: 'repo.credAwsAccessKeyId' },
    { suffix: 'aws_secret_access_key', labelKey: 'repo.credAwsSecretAccessKey' },
    { suffix: 'aws_default_region', labelKey: 'repo.credAwsDefaultRegion' },
    { suffix: 'aws_endpoint_url', labelKey: 'repo.credAwsEndpointUrl' },
  ],
  b2: [
    { suffix: 'b2_account_id', labelKey: 'repo.credB2AccountId' },
    { suffix: 'b2_account_key', labelKey: 'repo.credB2AccountKey' },
  ],
}

const credPrefix = computed(() => `cred.${currentRepoId.value}.`)
const credInput = reactive<Record<string, string>>({})

/** 只取当前仓库命名空间内的凭据项（隔离其它仓库的 cred.*）。 */
const credItems = computed(() =>
  (configQuery.data.value?.items ?? []).filter((item) => item.key.startsWith(credPrefix.value)),
)

function itemOf(suffix: string): ConfigItem | undefined {
  return credItems.value.find((item) => item.key === credPrefix.value + suffix)
}

const credFields = computed<CredField[]>(() => CRED_FIELDS[repoType.value])

const saving = ref(false)
const testing = ref(false)
const testResult = ref<RepoTestResponse | null>(null)
const unlockShow = ref(false)
const unlocking = ref(false)
const unlockJobId = ref('')
const initShow = ref(false)
const initializing = ref(false)
const initJobId = ref('')

// ---------------------------------------------------------------- 本地路径浏览器（E33）
const CONTAINER_ROOT = '/'
const browsePath = ref(CONTAINER_ROOT)
const mountChips = ref<string[]>([])
const urlMode = ref<'browse' | 'manual'>('manual')

function fsLoader(path: string, cursor?: string): Promise<TreePage> {
  return browseFs(path, cursor).then((page) => {
    if (page.path === CONTAINER_ROOT) {
      mountChips.value = page.mounts
    }
    return {
      items: page.items.map((item) => ({
        key: item.path,
        name: item.name,
        type: item.type,
        path: item.path,
      })),
      nextCursor: page.next_cursor,
    }
  })
}

function onTreeSelect(item: TreeItem): void {
  form.url = item.path
  if (item.type === 'dir') {
    browsePath.value = item.path
  }
}

function useCurrentDir(): void {
  form.url = browsePath.value
}

function jumpTo(path: string): void {
  browsePath.value = path
}

// ---------------------------------------------------------------- 保存 / 测试 / 解锁 / 初始化
async function onSave(): Promise<void> {
  const repoPatch: RepoUpdateRequest = {}
  if (form.url !== initial.url) {
    repoPatch.url = form.url || null
  }
  if (form.hostTag !== initial.hostTag) {
    repoPatch.host_tag = form.hostTag || null
  }
  const credPatch: Record<string, string | null> = {}
  for (const field of credFields.value) {
    const value = credInput[field.suffix]
    if (value) {
      credPatch[credPrefix.value + field.suffix] = value
    }
  }
  if (
    repoPatch.url === undefined &&
    repoPatch.host_tag === undefined &&
    Object.keys(credPatch).length === 0
  ) {
    message.info(t('repo.noChanges'))
    return
  }
  saving.value = true
  try {
    if (repoPatch.url !== undefined || repoPatch.host_tag !== undefined) {
      await updateRepo(currentRepoId.value, repoPatch)
    }
    if (Object.keys(credPatch).length > 0) {
      await putConfig(credPatch)
    }
    message.success(t('repo.saved'))
    clearCredInput()
    await Promise.all([repoQuery.refetch(), configQuery.refetch()])
    void statusQuery.refetch()
  } finally {
    saving.value = false
  }
}

function clearCredInput(): void {
  for (const key of Object.keys(credInput)) {
    credInput[key] = ''
  }
}

async function onTest(): Promise<void> {
  testing.value = true
  testResult.value = null
  try {
    testResult.value = await testRepo(currentRepoId.value)
  } finally {
    testing.value = false
  }
}

async function onUnlock(): Promise<void> {
  unlocking.value = true
  try {
    const created = await createJob('unlock')
    unlockJobId.value = created.id
    message.success(t('repo.unlockSubmitted'))
    void statusQuery.refetch()
  } catch (error) {
    if (!isAppError(error)) {
      throw error
    }
  } finally {
    unlocking.value = false
  }
}

async function onInit(): Promise<void> {
  initializing.value = true
  try {
    const created = await createJob('init')
    initJobId.value = created.id
    message.success(t('repo.initSubmitted'))
    void statusQuery.refetch()
  } catch (error) {
    if (!isAppError(error)) {
      throw error
    }
  } finally {
    initializing.value = false
  }
}

// ---------------------------------------------------------------- 展示辅助
const repoStatus = computed(() => statusQuery.data.value ?? null)

const capacityText = computed(() => {
  const size = repoStatus.value?.capacity.size
  return size ? formatBytes(size) : t('common.none')
})

function formatBytes(value: number): string {
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  let size = value
  let index = 0
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024
    index += 1
  }
  return `${size.toFixed(index === 0 ? 0 : 1)} ${units[index]}`
}
</script>

<template>
  <n-space vertical :size="16">
    <!-- 仓库列表（R-023） -->
    <n-card :title="t('repo.listTitle')" size="small">
      <template #header-extra>
        <n-button size="small" type="primary" secondary @click="openCreate">{{ t('repo.newRepo') }}</n-button>
      </template>
      <n-data-table
        size="small"
        :columns="columns"
        :data="reposQuery.data.value ?? []"
        :loading="reposQuery.isFetching.value"
        :row-key="(row: RepoOut) => row.id"
      />
    </n-card>

    <!-- 当前仓库配置（R-023） -->
    <n-grid cols="1 1100:2" :x-gap="16" :y-gap="16" responsive="screen">
      <n-gi>
        <n-card :title="t('repo.currentConfigTitle')" size="small">
          <n-space vertical :size="12">
            <n-form label-placement="top" size="small">
              <n-form-item :label="t('repo.type')">
                <n-space vertical :size="4" class="repo__cred">
                  <n-space align="center" :size="6">
                    <n-tag size="small" round :type="repoType === 'local' ? 'default' : 'info'">
                      {{ repoType === 'local' ? t('repo.typeLocal') : repoType === 's3' ? t('repo.typeS3') : t('repo.typeB2') }}
                    </n-tag>
                    <n-tag v-if="repo?.url_source === 'env'" size="tiny" round type="warning">
                      {{ t('repo.credSourceEnv') }}
                    </n-tag>
                  </n-space>
                  <n-text depth="3">{{ t('repo.typeReadonlyNote') }}</n-text>
                </n-space>
              </n-form-item>
              <n-form-item :label="t('repo.url')">
                <n-space vertical :size="6" class="repo__cred">
                  <n-tabs
                    v-if="repoType === 'local'"
                    v-model:value="urlMode"
                    type="segment"
                    size="small"
                  >
                    <n-tab-pane name="browse" :tab="t('repo.urlBrowseTab')" />
                    <n-tab-pane name="manual" :tab="t('repo.urlManualTab')" />
                  </n-tabs>

                  <template v-if="repoType === 'local' && urlMode === 'browse'">
                    <n-space :size="6" class="repo__mounts">
                      <n-tag size="small" round @click="jumpTo(CONTAINER_ROOT)">
                        {{ t('repo.mountRoot') }}
                      </n-tag>
                      <n-tag
                        v-for="mount in mountChips"
                        :key="mount"
                        size="small"
                        round
                        type="info"
                        class="repo__mount"
                        @click="jumpTo(mount)"
                      >
                        {{ mount }}
                      </n-tag>
                    </n-space>
                    <PathBreadcrumb :path="browsePath" @select="browsePath = $event" />
                    <VirtualTree
                      :root-path="browsePath"
                      :load-children="fsLoader"
                      :height="280"
                      select-dir
                      :selected-path="form.url"
                      @select="onTreeSelect"
                    />
                    <n-space align="center" :size="8">
                      <n-button size="small" secondary @click="useCurrentDir">
                        {{ t('repo.useThisDir', { path: browsePath }) }}
                      </n-button>
                      <n-input v-model:value="form.url" size="small" class="repo__picked" />
                    </n-space>
                    <n-text depth="3" class="repo__note">{{ t('repo.browseHint') }}</n-text>
                  </template>

                  <n-input
                    v-else
                    v-model:value="form.url"
                    :placeholder="t('repo.urlPlaceholder')"
                  />
                </n-space>
              </n-form-item>
              <n-form-item :label="t('repo.hostTag')">
                <n-input v-model:value="form.hostTag" :placeholder="t('repo.hostTagPlaceholder')" />
              </n-form-item>

              <!-- 凭据（脱敏：只回 has_value + source；env 覆盖的键置灰并给原因） -->
              <template v-if="credFields.length">
                <n-text strong>{{ t('repo.credentials') }}</n-text>
                <n-form-item
                  v-for="field in credFields"
                  :key="field.suffix"
                  :label="t(field.labelKey)"
                >
                  <n-space vertical :size="4" class="repo__cred">
                    <n-space align="center" :size="6">
                      <n-tag
                        size="tiny"
                        round
                        :type="itemOf(field.suffix)?.has_value ? 'success' : 'default'"
                      >
                        {{ itemOf(field.suffix)?.has_value ? t('repo.credSet') : t('repo.credUnset') }}
                      </n-tag>
                      <n-tag
                        v-if="itemOf(field.suffix)?.source === 'env'"
                        size="tiny"
                        round
                        type="warning"
                      >
                        {{ t('repo.credSourceEnv') }}
                      </n-tag>
                    </n-space>
                    <n-tooltip v-if="itemOf(field.suffix)?.source === 'env'" trigger="hover">
                      <template #trigger>
                        <n-input
                          :value="''"
                          :placeholder="t('repo.credEnvNote')"
                          type="password"
                          show-password-on="click"
                          disabled
                        />
                      </template>
                      {{ t('repo.credEnvNote') }}
                    </n-tooltip>
                    <n-input
                      v-else
                      v-model:value="credInput[field.suffix]"
                      :placeholder="t('repo.credNewPlaceholder')"
                      type="password"
                      show-password-on="click"
                    />
                  </n-space>
                </n-form-item>
              </template>
            </n-form>

            <n-space>
              <n-button type="primary" :loading="saving" @click="onSave">{{ t('repo.save') }}</n-button>
              <n-button secondary :loading="testing" @click="onTest">{{ t('repo.testBtn') }}</n-button>
            </n-space>

            <n-alert v-if="testResult?.ok" type="success" :show-icon="false">
              {{ t('repo.testOk', { n: testResult.latency_ms }) }}
            </n-alert>
            <n-alert v-else-if="testResult && !testResult.ok" type="error" :show-icon="false">
              {{ testResult.reason || t('repo.testFail') }}
            </n-alert>

            <n-text depth="3">{{ t('repo.passfileStatus') }}：
              {{ repo?.passfile_status === 'configured'
                ? t('repo.passfileConfigured')
                : t('repo.passfileMissing') }}
            </n-text>
          </n-space>
        </n-card>
      </n-gi>

      <!-- 仓库状态 -->
      <n-gi>
        <n-card :title="t('dashboard.repoCard')" size="small">
          <template #header-extra>
            <n-button size="tiny" secondary :loading="statusQuery.isFetching.value" @click="statusQuery.refetch()">
              {{ t('common.refresh') }}
            </n-button>
          </template>
          <n-space vertical :size="10">
            <n-space align="center" :size="8">
              <n-tag size="small" round :type="repoStatus?.connected ? 'success' : 'error'">
                {{ repoStatus?.connected ? t('dashboard.repoConnected') : t('dashboard.repoDisconnected') }}
              </n-tag>
              <n-tag size="small" round :type="repoStatus?.locked ? 'warning' : 'default'">
                {{ repoStatus?.locked ? t('dashboard.repoLocked') : t('dashboard.repoUnlocked') }}
              </n-tag>
            </n-space>
            <n-space :size="24">
              <n-statistic :label="t('dashboard.repoCapacity')" :value="capacityText" />
            </n-space>
            <n-alert v-if="repoStatus?.last_error" type="error" :show-icon="false">
              {{ t('dashboard.repoLastError') }}：{{ repoStatus.last_error }}
            </n-alert>
            <template v-if="repoStatus && !repoStatus.connected">
              <n-button size="small" type="warning" :loading="initializing" @click="initShow = true">
                {{ t('repo.initBtn') }}
              </n-button>
              <n-text depth="3" class="repo__note">{{ t('repo.initHint') }}</n-text>
            </template>
            <template v-if="repoStatus?.locked">
              <n-alert type="warning" :show-icon="false">{{ t('repo.lockedNote') }}</n-alert>
              <n-button size="small" type="warning" @click="unlockShow = true">
                {{ t('repo.unlockBtn') }}
              </n-button>
            </template>
            <n-text depth="3" class="repo__note">{{ t('dashboard.repoCacheNote') }}</n-text>

            <template v-if="initJobId">
              <JobProgress :job-id="initJobId" :title="t('repo.initJobTitle')" />
            </template>
            <template v-if="unlockJobId">
              <JobProgress :job-id="unlockJobId" :title="t('repo.unlockJobTitle')" />
            </template>
          </n-space>
        </n-card>
      </n-gi>
    </n-grid>

    <!-- 新建仓库 -->
    <n-modal
      v-model:show="createShow"
      :title="t('repo.newRepoTitle')"
      preset="card"
      style="width: 480px"
    >
      <n-form label-placement="top" size="small">
        <n-form-item :label="t('repo.colId')">
          <n-input v-model:value="createForm.id" :placeholder="t('repo.idRequired')" />
        </n-form-item>
        <n-form-item :label="t('repo.colName')">
          <n-input v-model:value="createForm.name" :placeholder="t('repo.nameRequired')" />
        </n-form-item>
        <n-form-item :label="t('repo.url')">
          <n-input v-model:value="createForm.url" :placeholder="t('repo.urlPlaceholder')" />
        </n-form-item>
        <n-form-item :label="t('repo.hostTag')">
          <n-input v-model:value="createForm.hostTag" :placeholder="t('repo.hostTagPlaceholder')" />
        </n-form-item>
      </n-form>
      <n-space justify="end">
        <n-button secondary @click="createShow = false">{{ t('common.cancel') }}</n-button>
        <n-button type="primary" :loading="creating" @click="onCreate">{{ t('common.confirm') }}</n-button>
      </n-space>
    </n-modal>

    <!-- 手动解锁：三步确认门 -->
    <ConfirmGate
      v-model:show="unlockShow"
      :title="t('repo.unlockBtn')"
      :keyword="'UNLOCK'"
      :description="t('repo.unlockDesc')"
      :loading="unlocking"
      @confirm="onUnlock"
    >
      <template #preview>
        <n-text depth="3">{{ t('repo.unlockPreview') }}</n-text>
      </template>
    </ConfirmGate>

    <!-- 初始化仓库：三步确认门（重复 init 会在错误地址上另建新仓库，须慎重） -->
    <ConfirmGate
      v-model:show="initShow"
      :title="t('repo.initBtn')"
      :keyword="'INIT'"
      :description="t('repo.initDesc')"
      :loading="initializing"
      @confirm="onInit"
    >
      <template #preview>
        <n-text depth="3">{{ t('repo.initPreview') }}</n-text>
      </template>
    </ConfirmGate>

    <!-- 删除仓库：三步确认门 -->
    <ConfirmGate
      v-model:show="deleteShow"
      :title="t('repo.delete')"
      :keyword="'DELETE'"
      :description="t('repo.deleteConfirm')"
      :loading="deleting"
      @confirm="confirmDelete"
    >
      <template #preview>
        <n-text depth="3">{{ t('repo.deleteConfirm') }}</n-text>
      </template>
    </ConfirmGate>
  </n-space>
</template>

<style scoped>
.repo__note {
  display: block;
  font-size: 12px;
}

.repo__cred {
  width: 100%;
}

.repo__mounts {
  flex-wrap: wrap;
  gap: 6px;
}

.repo__mount {
  cursor: pointer;
}

.repo__picked {
  flex: 1 1 200px;
}
</style>
