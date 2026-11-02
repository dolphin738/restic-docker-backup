<script setup lang="ts">
/**
 * 任务向导 · 「新建仓库」步骤（方案 v1.1 §4.3 F14）。
 *
 * 设计要点：
 * - **不新写表单**：地址/凭据/类型全部复用 `pages/repo/RepoEditForm.vue`（纯展示组件，
 *   31 props / 9 emits / 零 API 调用），本壳只负责把派生 props 喂进去并监听 5 个必要事件。
 * - **建仓编排不写在这里**：走 `composables/useRepoProvisioning.ts`（与仓库页同源，
 *   消除 §6.1 R6 的表单/流程漂移面）。
 * - 建仓成功后把 `creatingNew` 翻成 false（复刻仓库页 onCreateAndTest 的既有做法）：
 *   RepoEditForm.vue:549 的 `v-if="repoForm.type && !creatingNew"` 把测试区、init 入口、
 *   密码入口整块排除在新建态之外，不翻则链路在第一步就断。
 *   翻之后用户看到的是「测试失败 → 未初始化 → 初始化按钮」这条现成闭环。
 *   ⚠️ 方案 §4.3 F14 原写「creatingNew 传 true、repo 传 null」，**该取值已被证伪**
 *   （repo=null 还会打掉 :657 的密码状态行与 :171 的本地判定），更正见方案 v1.2 F14 注。
 *
 * 完成态判定（validateStep(KEY_NEW_REPO) 读的就是它）：仓库已创建 + 密码文件已配 +
 * init 作业成功 + 测试通过，四项齐备才置 provisionedRepoId。
 */

import { computed, reactive, ref, watch } from 'vue'
import { NButton, NSpace, NText, useMessage } from 'naive-ui'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { useI18n } from 'vue-i18n'
import { rebuildRepo } from '@/api/repo'
import { getConfig } from '@/api/config'
import { isAppError } from '@/api/http'
import { getRepo, getRepoStatus, listRepos, listRcloneConfigs, listRcloneRemotes, suggestRepoId, testRepo } from '@/api/repo'
import type { JobDonePayload, RepoTestResponse, RcloneConfigsOut } from '@/api/types'
import RepoEditForm from '@/pages/repo/RepoEditForm.vue'
import { useRepoProvisioning } from '@/composables/useRepoProvisioning'
import { CRED_FIELDS } from '@/utils/repoCredentials'
import { composeRepoUrl, parseRepoUrl, resolveRcloneTarget, type RepoTypeKey } from '@/utils/repoUrl'
import { useUiStore } from '@/stores/ui'
import PasswordEntryModal from '@/components/PasswordEntryModal.vue'
import ConfirmGate from '@/components/ConfirmGate.vue'

const props = defineProps<{
  /** 已创建成功的仓库 id（父组件的 provisionedRepoId）；null = 尚未建仓。 */
  provisionedRepoId: string | null
}>()

const emit = defineEmits<{
  /** 建仓完成：父组件写入 provisionedRepoId 并据此放行本步。 */
  (e: 'provisioned', id: string): void
  /** 即将切仓：把切之前的仓库 id 交给父组件（回滚 / 撤销都靠它）。 */
  (e: 'switching', fromRepoId: string): void
}>()

const { t } = useI18n()
const ui = useUiStore()
const message = useMessage()
const queryClient = useQueryClient()

const INIT_FAILURE_KINDS = new Set(['not_initialized', 'unknown'])

// 一次性明文弹窗（生成态 = SecretReveal：展示 + 复制 + 确认关闭，与仓库页重建结果同款）。
// 明文只在内存里交给用户复制备份，绝不进 localStorage / 草稿（红线 S3）。
const generatedPassword = ref<string | null>(null)

// ---------------------------------------------------------------- 重建仓库（2026-11-02 接通）
// RepoEditForm 在「本地 FS + 密码错误」测试结论旁出「重建仓库」按钮；此前本步把 rebuild
// 事件吞成空函数，点击无任何反应。与仓库页 useRepoGates.onRebuild 同语义：ConfirmGate
// 关键词 REBUILD 确认 → rebuildRepo → 生成密码走既有一次性弹窗 → 刷新查询 → 重测。
// 仅本地 FS 可用（远程后端 422），与按钮的 v-if="isLocalRepo" 一致（方案 R7）。
const rebuildShow = ref(false)
const rebuildSaving = ref(false)
function onRebuildRequested(): void {
  rebuildShow.value = true
}
async function onRebuildConfirmed(): Promise<void> {
  const id = props.provisionedRepoId
  if (!id) return
  rebuildSaving.value = true
  try {
    const res = await rebuildRepo(id, {})
    if (res.generated && res.password) {
      generatedPassword.value = res.password
    }
    await Promise.all([repoQuery.refetch(), configQuery.refetch(), statusQuery.refetch(), reposQuery.refetch()])
    // 旧测试结论描述的是重建前的旧仓库，作废后立即重测（与仓库页 resetTestResult 同语义）
    testResult.value = null
    repoTestState.value = 'idle'
    await onTest()
  } catch (error) {
    if (!isAppError(error)) throw error
  } finally {
    rebuildSaving.value = false
  }
}
// PasswordEntryModal 的 v-model:show：明文非空即开；确认/关闭写回 null（清内存，S3）
const generatedPasswordShow = computed({
  get: () => generatedPassword.value !== null,
  set: (show: boolean) => {
    if (!show) generatedPassword.value = null
  },
})
const NOT_INITIALIZED_MARKERS = ['未初始化', '不存在', 'not initialized', 'unable to open config file', 'no such bucket', 'does not exist']

// ---------------------------------------------------------------- 表单状态（与仓库页同形状）
const repoForm = reactive({
  type: '' as RepoTypeKey | '',
  name: '',
  id: '',
  hostTag: '',
  path: '',
  bucket: '',
  endpoint: '',
  server: '',
  user: '',
  url: '',
  remote: '',
  raw: '',
})
const credInput = reactive<Record<string, string>>({})
/** 建仓后翻成 false（见文件头说明）——决定 RepoEditForm 是否渲染测试区/init 入口。 */
const creatingNew = ref(true)
const saving = ref(false)
const testing = ref(false)
const generatingId = ref(false)
const repoTestState = ref<'idle' | 'success' | 'error'>('idle')
const testResult = ref<RepoTestResponse | null>(null)
const verifyRequired = ref(false)
/** init 作业 id（本步内就地展示进度；终态写回后清空）。 */
const initJobId = ref<string | null>(null)
const initializing = ref(false)

const formType = computed<RepoTypeKey>(() => (repoForm.type || 'local') as RepoTypeKey)
const credFields = computed(() => CRED_FIELDS[formType.value])
const typeSelectOptions = computed(() => [
  { label: t('repo.typeLocal'), value: 'local' },
  { label: t('repo.typeRclone'), value: 'rclone' },
  { label: t('repo.typeS3'), value: 's3' },
  { label: t('repo.typeB2'), value: 'b2' },
  { label: t('repo.typeSftp'), value: 'sftp' },
  { label: t('repo.typeAzure'), value: 'azure' },
  { label: t('repo.typeGcs'), value: 'gcs' },
  { label: t('repo.typeRest'), value: 'rest' },
])

/** 已建仓库的详情（建仓后才有；驱动密码状态行与 passfileMissing）。 */
const repoQuery = useQuery({
  queryKey: ['repo', () => props.provisionedRepoId ?? ''],
  queryFn: () => getRepo(props.provisionedRepoId as string),
  enabled: computed(() => props.provisionedRepoId !== null),
})
const statusQuery = useQuery({
  queryKey: ['repo', 'status', () => props.provisionedRepoId ?? ''],
  queryFn: () => getRepoStatus(props.provisionedRepoId as string),
  enabled: computed(() => props.provisionedRepoId !== null),
})
const configQuery = useQuery({ queryKey: ['config'], queryFn: getConfig })
const reposQuery = useQuery({ queryKey: ['repos'], queryFn: listRepos })

const repo = computed(() => repoQuery.data.value ?? null)
const passfileMissing = computed(() => (repo.value?.passfile_status ?? 'missing') !== 'configured')

const credPrefix = computed(() => `cred.${props.provisionedRepoId ?? ''}.`)
const credItems = computed(() =>
  (configQuery.data.value?.items ?? []).filter((item) => item.key.startsWith(credPrefix.value)),
)
function itemOf(suffix: string): { has_value?: boolean; source?: string } | undefined {
  return credItems.value.find((item) => item.key === credPrefix.value + suffix)
}

// rclone 只读探测（与仓库页同口径；仅表单所选类型为 rclone 时拉取）
const rcloneRemotesQuery = useQuery({
  queryKey: ['rclone', 'remotes'],
  queryFn: listRcloneRemotes,
  enabled: computed(() => formType.value === 'rclone'),
  staleTime: 60_000,
})
const rcloneConfigsQuery = useQuery({
  queryKey: ['rclone', 'configs'],
  queryFn: listRcloneConfigs,
  enabled: computed(() => formType.value === 'rclone'),
  staleTime: 60_000,
})
const rcloneRemotes = computed(() => rcloneRemotesQuery.data.value ?? null)
const rcloneConfigs = computed<RcloneConfigsOut['configs']>(() => rcloneConfigsQuery.data.value?.configs ?? [])
const rcloneRemoteMenu = computed(() =>
  (rcloneRemotes.value?.remotes ?? []).map((name) => ({ label: name, key: name })),
)
const rcloneResolved = computed(() =>
  resolveRcloneTarget(
    repoForm.remote,
    repoForm.path,
    rcloneConfigs.value.find((c) => c.remote === repoForm.remote.trim()) ?? null,
  ),
)
const rcloneRemoteUsers = computed(() => {
  const remote = repoForm.remote.trim()
  if (!remote) {
    return []
  }
  const path = repoForm.path.trim().replace(/^\/+/, '')
  const out: Array<{ id: string; name: string; path: string; samePath: boolean }> = []
  for (const r of reposQuery.data.value ?? []) {
    if (r.type !== 'rclone' || !r.url || r.id === props.provisionedRepoId) {
      continue
    }
    const parsed = parseRepoUrl('rclone', r.url)
    if (!parsed || parsed.remote !== remote) {
      continue
    }
    out.push({ id: r.id, name: r.name, path: parsed.path ?? '', samePath: (parsed.path ?? '').replace(/^\/+/, '') === path })
  }
  return out
})

/** 建议值：从既有仓库提取（同一用户建同类仓库大概率同桶/同端点）。 */
const bucketSuggestions = computed(() => {
  const out: string[] = []
  for (const r of reposQuery.data.value ?? []) {
    if (!r.url) continue
    const parsed = r.type === 'rclone' ? null : parseRepoUrl(r.type as RepoTypeKey, r.url)
    const bucket = (parsed as { bucket?: string } | null)?.bucket
    if (bucket && !out.includes(bucket)) out.push(bucket)
  }
  return out
})
const endpointSuggestions = computed(() => {
  const out: string[] = []
  for (const r of reposQuery.data.value ?? []) {
    if (r.type !== 's3' || !r.url) continue
    const endpoint = (parseRepoUrl('s3', r.url) as { endpoint?: string } | null)?.endpoint
    if (endpoint && !out.includes(endpoint)) out.push(endpoint)
  }
  return out
})

const testNotInitialized = computed(() => {
  if (repoTestState.value !== 'error') return false
  const kind = testResult.value?.failure_kind
  if (kind) return INIT_FAILURE_KINDS.has(kind)
  const reason = (testResult.value?.reason ?? '').toLowerCase()
  return NOT_INITIALIZED_MARKERS.some((m) => reason.includes(m.toLowerCase()))
})
const testPasswordWrong = computed(
  () => repoTestState.value === 'error' && testResult.value?.failure_kind === 'password',
)

// ---------------------------------------------------------------- 建仓编排
const provisioning = useRepoProvisioning({
  setCurrentRepoId: (id: string) => {
    // 切仓前先告知父组件「原仓库是谁」——切完就取不到了
    emit('switching', ui.currentRepoId)
    ui.setCurrentRepoId(id)
  },
  onRepoCreated: async (id: string) => {
    emit('provisioned', id)
    await Promise.all([reposQuery.refetch(), queryClient.invalidateQueries({ queryKey: ['repos'] })])
  },
})

async function onRegenerateId(): Promise<void> {
  generatingId.value = true
  try {
    repoForm.id = (await suggestRepoId()).id
  } finally {
    generatingId.value = false
  }
}

function onTypeChange(value: string | null): void {
  repoForm.type = (value ?? '') as RepoTypeKey | ''
  // 类型切换后旧的测试结论不再成立
  repoTestState.value = 'idle'
  testResult.value = null
}

/** 主按钮：建仓（createRepo + 密码 + 凭据 + 切仓 + init + 测试）。 */
async function onCreate(): Promise<void> {
  if (!repoForm.type) {
    message.warning(t('repo.formTypePlaceholder'))
    return
  }
  if (!repoForm.name.trim()) {
    message.warning(t('repo.nameRequired'))
    return
  }
  saving.value = true
  try {
    const result = await provisioning.provision({
      id: repoForm.id,
      name: repoForm.name,
      url: composeRepoUrl(repoForm).trim() || null,
      hostTag: repoForm.hostTag,
      type: repoForm.type,
      credInput,
    })
    initJobId.value = result.initJobId
    creatingNew.value = false
    verifyRequired.value = false
    testResult.value = result.test
    repoTestState.value = result.test.ok ? 'success' : 'error'
    // 一次性明文走可复制的弹窗（PasswordEntryModal 生成态），不再用不可复制的 toast
    generatedPassword.value = result.generatedPassword ?? null
    await Promise.all([repoQuery.refetch(), configQuery.refetch(), statusQuery.refetch()])
  } catch (error) {
    if (!isAppError(error)) throw error
  } finally {
    saving.value = false
  }
}

/** 就地测试（建仓后可用；后端读已落库配置）。 */
async function onTest(): Promise<void> {
  const id = props.provisionedRepoId
  if (!id) return
  testing.value = true
  repoTestState.value = 'idle'
  try {
    const result = await testRepo(id)
    testResult.value = result
    repoTestState.value = result.ok ? 'success' : 'error'
  } catch {
    repoTestState.value = 'error'
  } finally {
    testing.value = false
  }
}

/** 初始化（走既有 init 作业通道，远程后端同样可用；**绝不换 rebuild**）。 */
async function onInit(): Promise<void> {
  const id = props.provisionedRepoId
  if (!id) return
  initializing.value = true
  try {
    initJobId.value = await provisioning.submitInit()
    await statusQuery.refetch()
  } catch (error) {
    if (!isAppError(error)) throw error
  } finally {
    initializing.value = false
  }
}

/** init 终态：成功则复测一次——四项齐备才让父组件放行本步。 */
async function onInitDone(payload?: JobDonePayload): Promise<void> {
  initJobId.value = null
  if (!payload || payload.exit_code !== 0) {
    message.error(t('repo.initFailed', { reason: payload?.summary || t('repo.testFail') }))
    return
  }
  message.success(t('repo.initDone'))
  await Promise.all([repoQuery.refetch(), statusQuery.refetch()])
  await onTest()
}

/** 建仓后由「设密码」入口触发：密码由后端生成并一次性回显。
 *  `force` 来自 RepoEditForm 的 password 事件（true = 密码错误时的「重设」档），
 *  必须透传——丢弃它会让「重设」按钮在已存在文件时静默 422。 */
async function onPassword(force: boolean): Promise<void> {
  const id = props.provisionedRepoId
  if (!id) return
  try {
    const password = await provisioning.writePassword(id, undefined, force)
    if (password) {
      generatedPassword.value = password
    }
    await Promise.all([repoQuery.refetch(), configQuery.refetch()])
    if (force) await onTest()
  } catch (error) {
    if (!isAppError(error)) throw error
  }
}

/** 四项齐备 → 通知父组件写 provisionedRepoId 之外的完成态。
 *  这里只做「测试通过且已建仓」的终判：密码文件与 init 成功由后端状态体现。 */
watch(
  [() => repoTestState.value, () => repo.value?.passfile_status, () => initJobId.value],
  () => {
    if (props.provisionedRepoId === null) return
    if (repoTestState.value !== 'success') return
    if ((repo.value?.passfile_status ?? 'missing') !== 'configured') return
    if (initJobId.value !== null) return
    emit('provisioned', props.provisionedRepoId)
  },
  { immediate: true },
)
</script>

<template>
  <n-space vertical :size="8">
    <n-text strong>{{ t('wizard.stepNewRepo') }}</n-text>
    <n-text depth="3" class="new-repo__note">{{ t('wizard.newRepoStepDesc') }}</n-text>

    <RepoEditForm
      :repo-form="repoForm"
      :cred-input="credInput"
      :creating-new="creatingNew"
      :current-repo-id="provisionedRepoId ?? ''"
      :repo="repo"
      :repo-type="formType"
      :type-select-options="typeSelectOptions"
      :bucket-suggestions="bucketSuggestions"
      :endpoint-suggestions="endpointSuggestions"
      :cred-fields="credFields"
      :item-of="creatingNew ? null : itemOf"
      :env-locked="false"
      :rclone-remotes="rcloneRemotes"
      :rclone-probe-error="rcloneRemotesQuery.error.value"
      :rclone-remote-menu="rcloneRemoteMenu"
      :rclone-resolved="rcloneResolved"
      :rclone-remote-users="rcloneRemoteUsers"
      :repo-test-state="repoTestState"
      :test-result="testResult"
      :testing="testing"
      :saving="saving"
      :creating="saving"
      :generating-id="generatingId"
      :test-not-initialized="testNotInitialized"
      :test-password-wrong="testPasswordWrong"
      :passfile-missing="passfileMissing"
      :verify-required="verifyRequired"
      :initializing="initializing"
      :init-target-id="initJobId ? (provisionedRepoId ?? null) : null"
      :init-job-map="initJobId && provisionedRepoId ? { [provisionedRepoId]: initJobId } : {}"
      :save-button="{ label: t('wizard.newRepoCreate'), type: 'primary', run: onCreate }"
      embedded
      @cancel="() => {}"
      @type-change="onTypeChange"
      @regenerate-id="onRegenerateId"
      @rclone-drawer="() => {}"
      @test="onTest"
      @password="onPassword"
      @init="onInit"
      @rebuild="onRebuildRequested"
      @init-done="onInitDone"
    />

    <!-- 建仓主按钮：RepoEditForm 的底栏在 embedded 下隐藏，故由本壳提供。
         建仓后的「测试 / 初始化 / 密码状态 / init 进度」全部由 RepoEditForm 内部渲染
         （它的测试区在 creatingNew 翻 false 后自动出现）——壳里再画一套会出现
         重复的「初始化仓库任务」进度卡与两套按钮（真机截图已证）。 -->
    <n-space v-if="!provisionedRepoId" align="center" :size="8">
      <n-button type="primary" :loading="saving" @click="onCreate">
        {{ saving ? t('wizard.newRepoCreating') : t('wizard.newRepoCreate') }}
      </n-button>
    </n-space>

    <!-- 重建仓库确认门（2026-11-02 接通；与仓库页同款：关键词 REBUILD，仅本地 FS 出口） -->
    <ConfirmGate
      v-model:show="rebuildShow"
      :title="t('repo.rebuildTitle')"
      :keyword="'REBUILD'"
      :description="t('repo.rebuildConfirm')"
      :loading="rebuildSaving"
      @confirm="onRebuildConfirmed"
    >
      <template #preview>
        <n-text depth="3">{{ t('repo.rebuildPreview') }}</n-text>
      </template>
    </ConfirmGate>

    <!-- 一次性明文展示（与仓库页「设置密码 / 重建结果」同款：SecretReveal 生成态，
         可复制 + 确认关闭；确认只关弹窗，不动四项齐备的放行判定） -->
    <PasswordEntryModal
      v-model:show="generatedPasswordShow"
      :title="t('wizard.newRepoPasswordTitle')"
      :generated-hint="t('wizard.newRepoPasswordHint')"
      :confirm-label="t('wizard.newRepoPasswordConfirm')"
      :generated="generatedPassword"
      :loading="saving"
      :show-generated-cancel="false"
      :show-generate="false"
      @confirm-generated="generatedPassword = null"
    />
  </n-space>
</template>

<style scoped>
.new-repo__note {
  display: block;
  font-size: 12px;
}
</style>
