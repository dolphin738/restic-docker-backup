/**
 * 仓库页 9 个危险动作确认门的聚合（候选 4 批 1）。
 *
 * 为什么需要它：每个门的「开关 ref + loading ref + 确认动作」原先散在 2824 行里，
 * 状态定义与确认门渲染相隔约 1700 行（deleteShow 定义在 547 行、它的门在 2389 行），
 * 改一处要跨半个文件确认另一半。
 *
 * 探针实测（2026-10-31）：9 个门之间**几乎零耦合**——9 个确认动作里只有
 * confirmDelete 一处需要回调父页 onSetCurrent；对页面共享状态的依赖也只有
 * currentRepoId / statusQuery / reposQuery / repoQuery / configQuery / queryClient。
 * 故整组聚合是安全的。
 *
 * 边界：只管「门的开关与确认动作」，**不含**门的渲染（仍在页面 template）。
 * 依赖参数注入而非模块级 import，便于测试与复用。
 */

import { computed, reactive, ref, type ComputedRef, type Ref } from 'vue'
import type { QueryClient } from '@tanstack/vue-query'

import { hasCode, isAppError } from '@/api/http'
import { createJob } from '@/api/jobs'
import { deleteRepo, getRepoUsage, manualCopy, rebuildRepo, rotateRepoKey } from '@/api/repo'
import { clearCache } from '@/api/settings'
import type { JobDonePayload, RepoUsageOut } from '@/api/types'

/** copy 结果（手动跨仓复制，无对应作业）。 */
export interface CopyResult {
  ok: boolean
  exit_code: number
  message: string
  copied: number | null
  target_total: number | null
  log_name: string | null
}

type Refetch = { refetch: () => Promise<unknown> }

export interface RepoGatesDeps {
  /** 当前仓库 id。 */
  currentRepoId: ComputedRef<string | undefined> | Ref<string | undefined>
  /** i18n 取词。 */
  t: (key: string, params?: Record<string, unknown>) => string
  /** 全局消息出口。 */
  message: {
    success: (text: string) => void
    warning: (text: string) => void
    error: (text: string) => void
    info: (text: string) => void
  };
  queryClient: QueryClient
  reposQuery: Refetch
  repoQuery: Refetch
  statusQuery: Refetch
  configQuery: Refetch
  cacheQuery: Refetch
  /** 仓库行（删除门「本地 FS」判定 + copy 候选派生）。 */
  repos: ComputedRef<Array<{ id: string; name: string; type: string; url?: string | null; passfile_status?: string }>>
  /** 删除成功后切回默认仓库（唯一一处跨门回调）。 */
  onDeleted: (id: string) => void
  /** 测试结论作废（init / rebuild 后旧结论已不成立）。 */
  resetTestResult: () => void
}

export function useRepoGates(deps: RepoGatesDeps) {
  const { t, message, queryClient } = deps

  // ---------------------------------------------------------------- 1. 解锁
  const unlockShow = ref(false)
  const unlocking = ref(false)
  const unlockJobId = ref('')

  async function onUnlock(): Promise<void> {
    unlocking.value = true
    try {
      const created = await createJob('unlock')
      unlockJobId.value = created.id
      message.success(t('repo.unlockSubmitted'))
      void deps.statusQuery.refetch()
    } catch (error) {
      if (!isAppError(error)) throw error
    } finally {
      unlocking.value = false
    }
  }

  /** 解锁作业终态：清 jobId 让浮卡消失并刷新状态。 */
  function onUnlockDone(): void {
    unlockJobId.value = ''
    void deps.statusQuery.refetch()
  }

  // ---------------------------------------------------------------- 2. 初始化
  const initShow = ref(false)
  const initializing = ref(false)
  const initTargetId = ref<string | null>(null)
  const initJobMap = reactive<Record<string, string>>({})

  /** 关门清理：纯取消才清目标（提交中/已有作业时清会让按钮提前解锁，诱发重复 init）。
   *  顺序安全：ConfirmGate 先 emit(confirm)（同步置 initializing）再 close。 */
  function onInitGateToggle(visible: boolean): void {
    initShow.value = visible
    if (visible) return
    const target = initTargetId.value
    const hasJob = target ? Boolean(initJobMap[target]) : false
    if (!initializing.value && !hasJob) {
      initTargetId.value = null
    }
  }

  async function onInit(): Promise<void> {
    const targetId = deps.currentRepoId.value as string
    initializing.value = true
    try {
      const created = await createJob('init')
      initJobMap[targetId] = created.id
      message.success(t('repo.initSubmitted'))
      void deps.statusQuery.refetch()
    } catch (error) {
      if (!isAppError(error)) throw error
      // 提交失败也要清理，避免留下空 jobId 卡
      delete initJobMap[targetId]
      if (initTargetId.value === targetId) {
        initTargetId.value = null
      }
    } finally {
      initializing.value = false
    }
  }

  /** init 终态：清行内进度卡 + 全量失效。
   *  只刷状态卡不够——行级查询还持 30s 缓存，表现为「初始化完了按钮还在」。 */
  async function onInitDone(repoId: string, payload?: JobDonePayload): Promise<void> {
    delete initJobMap[repoId]
    if (initTargetId.value === repoId) {
      initTargetId.value = null
    }
    // init 是秒级短任务，进度卡一闪即逝，必须有明确终态结论
    if (payload) {
      if (payload.exit_code === 0) {
        message.success(t('repo.initDone'))
      } else {
        message.error(t('repo.initFailed', { reason: payload.summary || t('repo.testFail') }))
      }
    }
    deps.resetTestResult()
    await queryClient.invalidateQueries({ queryKey: ['repo'] })
    await Promise.all([deps.reposQuery.refetch(), deps.statusQuery.refetch()])
    await queryClient.invalidateQueries({ queryKey: ['repo', 'status', 'row', repoId] })
  }

  // ---------------------------------------------------------------- 3. 修复
  const repairShow = ref(false)
  const repairing = ref(false)
  const repairJobId = ref('')
  const repairTarget = ref<'index' | 'packs'>('index')

  async function onRepair(): Promise<void> {
    repairing.value = true
    try {
      const created = await createJob('repair', { params: { repair_target: repairTarget.value } })
      repairJobId.value = created.id
      message.success(t('repo.repairSubmitted'))
      void deps.statusQuery.refetch()
    } catch (error) {
      if (!isAppError(error)) throw error
    } finally {
      repairing.value = false
    }
  }

  function onRepairDone(): void {
    repairJobId.value = ''
    void deps.statusQuery.refetch()
  }

  // ---------------------------------------------------------------- 4. 找回丢失快照
  const recoverShow = ref(false)
  const recovering = ref(false)
  const recoverJobId = ref('')

  async function onRecover(): Promise<void> {
    recovering.value = true
    try {
      const created = await createJob('recover')
      recoverJobId.value = created.id
      message.success(t('repo.recoverSubmitted'))
      void deps.statusQuery.refetch()
    } catch (error) {
      if (!isAppError(error)) throw error
    } finally {
      recovering.value = false
    }
  }

  function onRecoverDone(): void {
    recoverJobId.value = ''
    void deps.statusQuery.refetch()
  }

  // ---------------------------------------------------------------- 5. 清空本地缓存
  const cacheShow = ref(false)
  const cacheClearing = ref(false)

  async function onClearCache(): Promise<void> {
    cacheClearing.value = true
    try {
      await clearCache()
      message.success(t('settings.cache.cleared'))
      cacheShow.value = false
      await deps.cacheQuery.refetch()
    } catch (error) {
      // 409 = 有任务在跑不能清；其余交给全局提示（clearCache 是 silent 请求）
      if (hasCode(error, 'JOB_CONFLICT')) {
        message.warning(t('settings.cache.conflict'))
      } else if (!isAppError(error)) {
        throw error
      }
    } finally {
      cacheClearing.value = false
    }
  }

  // ---------------------------------------------------------------- 6. 删除仓库（两档门）
  const deleteShow = ref(false)
  const deleting = ref(false)
  const deleteTarget = ref<string | null>(null)
  /** 勾选「同时删除磁盘数据」（默认不勾）：勾选后 purge_disk=true，rmtree + 删密码文件。 */
  const deletePurge = ref(false)
  const deleteUsage = ref<RepoUsageOut | null>(null)
  const deleteUsageLoading = ref(false)

  /** 删除目标仓库行（勾选框仅本地 FS 显示）。 */
  const deleteTargetRepo = computed(() => deps.repos.value.find((x) => x.id === deleteTarget.value));

  /** 门描述按是否销毁分两档（口径见删除保护方案 §3.1/§4 L3）。 */
  const deleteDescription = computed(() => (deletePurge.value ? t('repo.deletePurgeConfirm') : t('repo.deleteConfirm')));

  /** 快照数文案：null/加载中 → 不可知（绝不写 0，否则用户误判「从没备过」）。 */
  const deleteSnapshotText = computed(() => {
    if (deleteUsageLoading.value) return t('repo.usageLoading');
    const total = deleteUsage.value?.snapshot_total;
    return typeof total === 'number' ? t('repo.usageSnapshots', { n: total }) : t('repo.usageUnknown');
  });

  /** 镜像引用（非空 → 后端必然 409，门里提前说清去哪解）。 */
  const deleteMirrorRefs = computed(() => deleteUsage.value?.mirror_refs ?? []);

  /** 占用明细只用于「让后果可见」，取不到不阻断删除门（失败即显示不可知）。 */
  async function loadDeleteUsage(id: string): Promise<void> {
    deleteUsage.value = null
    deleteUsageLoading.value = true
    try {
      deleteUsage.value = await getRepoUsage(id)
    } catch {
      deleteUsage.value = null
    } finally {
      deleteUsageLoading.value = false
    }
  }

  function onDeleteClick(id: string): void {
    if (id === 'default') {
      message.warning(t('repo.deleteDefaultForbidden'))
      return
    }
    deleteTarget.value = id
    deletePurge.value = false
    deleteUsage.value = null
    deleteShow.value = true
    void loadDeleteUsage(id)
  }

  async function confirmDelete(): Promise<void> {
    const id = deleteTarget.value
    if (!id) return
    const purging = deletePurge.value
    deleting.value = true
    try {
      await deleteRepo(id, { purgeDisk: purging });
      message.success(purging ? t('repo.destroyed') : t('repo.archived'));
      if (deps.currentRepoId.value === id) {
        deps.onDeleted(id)
      }
      // 归档页数据是独立查询（['archive','repos']），不失效则跳过去看不到新条目
      await queryClient.invalidateQueries({ queryKey: ['archive', 'repos'] });
      await deps.reposQuery.refetch();
    } catch (error) {
      if (!isAppError(error)) throw error
    } finally {
      deleting.value = false
      deleteTarget.value = null
      deleteShow.value = false
    }
  }

  // ---------------------------------------------------------------- 7. 重建仓库
  const rebuildShow = ref(false)
  const rebuildResultShow = ref(false)
  const rebuildTargetId = ref<string | null>(null)
  const rebuildPassword = ref<string | null>(null)
  const rebuildSaving = ref(false)

  /** 打开重建门（列表行内与编辑态测试区共用；目标缺省当前仓库）。 */
  function openRebuild(id?: string): void {
    rebuildTargetId.value = id ?? deps.currentRepoId.value ?? null
    rebuildShow.value = true
  }

  async function onRebuild(id: string): Promise<void> {
    rebuildSaving.value = true
    try {
      const res = await rebuildRepo(id, {})
      rebuildPassword.value = res.generated ? res.password : null
      rebuildResultShow.value = true
      message.success(t('repo.rebuildOk'))
      // 旧测试结论（典型：密码错误）描述的是重建前的旧仓库，必须作废
      deps.resetTestResult()
      await Promise.all([
        deps.reposQuery.refetch(),
        deps.repoQuery.refetch(),
        deps.statusQuery.refetch(),
        deps.configQuery.refetch(),
      ])
      await queryClient.invalidateQueries({ queryKey: ['repo', 'status'] })
    } catch (error) {
      if (!isAppError(error)) throw error
    } finally {
      rebuildSaving.value = false
    }
  }

  function closeRebuild(): void {
    rebuildResultShow.value = false
    rebuildPassword.value = null
  }

  // ---------------------------------------------------------------- 8. 密钥轮换
  const rotateShow = ref(false)
  const rotating = ref(false)
  /** 生成态的一次性明文（前端本地生成）；null = 自填态。确认前仅存内存。 */
  const rotateGenerated = ref<string | null>(null)

  function openRotate(): void {
    rotateGenerated.value = null
    rotateShow.value = true
  }

  /** 轮换提交：新密码后端不回显（S3），前端本地生成、确认后才随请求提交。 */
  async function onRotate(newPassword: string): Promise<void> {
    const id = deps.currentRepoId.value
    if (!id || newPassword.length < 10) return
    rotating.value = true
    try {
      await rotateRepoKey({ repo_id: id, new_password: newPassword });
      message.success(t('repo.rotateOk'));
      rotateShow.value = false
      rotateGenerated.value = null
    } catch (error) {
      if (!isAppError(error)) throw error
    } finally {
      rotating.value = false
    }
  }

  // ---------------------------------------------------------------- 9. 手动 copy
  const copyShow = ref(false)
  const copying = ref(false)
  const copyTarget = ref<string | null>(null)
  const copyResult = ref<CopyResult | null>(null)

  /** copy 源仓固定为「当前查看的仓库」，弹窗显式标出——否则极易把方向看反。 */
  const copySourceLabel = computed(() => {
    const hit = deps.repos.value.find((repo) => repo.id === deps.currentRepoId.value);
    return hit ? `${hit.name} (${hit.id})` : (deps.currentRepoId.value ?? '—');
  });

  /** copy 目标候选：其它仓库中已配地址与密码的（否则后端 422）。 */
  const copyOptions = computed(() =>
    deps.repos.value
      .filter((repo) => repo.id !== deps.currentRepoId.value)
      .map((repo) => ({
        label: `${repo.name} (${repo.id})`,
        value: repo.id,
        disabled: !(repo.url ?? '').trim() || repo.passfile_status !== 'configured',
      })),
  );

  function openCopy(): void {
    copyTarget.value = null
    copyResult.value = null
    copyShow.value = true
  }

  async function onCopy(): Promise<void> {
    const id = deps.currentRepoId.value
    if (!id || !copyTarget.value) return
    copying.value = true
    copyResult.value = null
    try {
      const resp = await manualCopy({ source_repo_id: id, target_repo_id: copyTarget.value });
      const copied = resp.copied ?? null;
      copyResult.value = {
        ok: resp.ok,
        exit_code: resp.exit_code,
        message: resp.message,
        copied,
        target_total: resp.target_total ?? null,
        log_name: resp.log_name ?? null,
      };
      if (resp.ok) {
        // exit 0 ≠ 复制了东西：copied=0 说明目标已含全部快照，必须提示「未新增」
        if (copied === 0) {
          message.warning(t('repo.copyNoNew'));
        } else if (copied !== null && copied > 0) {
          message.success(t('repo.copyOkN', { n: copied }));
        } else {
          message.success(t('repo.copyOk'));
        }
      } else {
        message.error(resp.message || t('repo.copyFail'));
      }
    } catch (error) {
      if (!isAppError(error)) throw error
    } finally {
      copying.value = false
    }
  }

  // 扁平返回：模板里顶层 ref 会自动解包（嵌套对象里的 ref 不会），故不嵌套。
  return {
    // 解锁
    unlockShow, unlocking, unlockJobId, onUnlock, onUnlockDone,
    // 初始化
    initShow, initializing, initTargetId, initJobMap, onInitGateToggle, onInit, onInitDone,
    // 修复
    repairShow, repairing, repairJobId, repairTarget, onRepair, onRepairDone,
    // 找回
    recoverShow, recovering, recoverJobId, onRecover, onRecoverDone,
    // 缓存
    cacheShow, cacheClearing, onClearCache,
    // 删除
    deleteShow, deleting, deleteTarget, deletePurge, deleteTargetRepo,
    deleteDescription, deleteSnapshotText, deleteMirrorRefs, onDeleteClick, confirmDelete,
    // 重建
    rebuildShow, rebuildResultShow, rebuildTargetId, rebuildPassword, rebuildSaving,
    openRebuild, onRebuild, closeRebuild,
    // 轮换
    rotateShow, rotating, rotateGenerated, openRotate, onRotate,
    // copy
    copyShow, copying, copyTarget, copyResult, copySourceLabel, copyOptions, openCopy, onCopy,
  };
}