import { useCallback, useState, useEffect, useRef } from 'react'
import { fetchWithRetry, isAbortError, isServiceNotReadyError, readJsonWithTimeout } from '@/lib/api'
import { derivePlatformLoginStates, mergePlatformLoginStates } from '@/lib/platformLogin'

interface FunnelData {
  [key: string]: number
}

interface ActivityData {
  day: string
  action: string
  cnt: number
}

interface Job {
  employment_type?: string
  employment_review?: string
  id: string
  source_platform?: 'boss' | 'zhilian' | string
  source_job_id?: string | null
  source_keyword?: string | null
  source_city_code?: string | null
  title: string
  company: string
  salary: string
  city: string
  experience: string
  education?: string
  recruitment_type?: 'campus' | 'experienced' | 'unknown' | string
  jd: string
  score: number
  score_reason: string
  greeting: string
  /** 当前生效文案（v38）：后端按开关+固定文案实时算好，展示与发送共用同一份判定。 */
  effective_greeting?: string
  greeting_original?: string | null
  greeting_optimized?: string | null
  greeting_style_issues?: string[]
  greeting_selection?: 'legacy' | 'generated' | 'pending' | 'auto_optimized' | 'original' | 'optimized' | 'edited' | string
  greeting_activity?: 'generating' | 'sending' | 'editing' | null
  greeting_reviewed_at?: string | null
  status: string
  hr_name: string
  hr_title: string
  hr_active: string
  company_size: string
  company_industry: string
  url: string
  created_at: string
  updated_at?: string
  /** 最近一次动态（投递/回复/跟进）的时间，后端从 history 里算；没有就退回 updated_at。 */
  last_activity_at?: string | null
  deleted_at?: string | null
  deleted_reason?: string | null
  resume_path?: string
  last_error?: string
  outsourcing_level?: 'clean' | 'suspected' | 'confirmed'
  outsourcing_confirmed?: boolean
  outsourcing_matches?: string[] | null
  outsourcing_updated_at?: string | null
}

interface TopCompany {
  company: string
  avg_score: number
  job_count: number
}

/** 打分进行中正在打的岗位：index 是提交顺序（第 X/N 份中的 X）。 */
export interface ScoringCurrentJob {
  index: number
  company: string
  title: string
  attempt?: number | null
  max_attempts?: number | null
  note?: string
}

export interface WorkbenchTask {
  id: string
  mode: 'full' | 'collect' | 'score' | 'rescore' | 'greet' | 'monitor' | 'deliver'
  label: string
  status: string
  logs: string[]
  error?: string
  created_at?: string
  updated_at?: string
  deadline_at?: string
  stop_reason?: string
  stop_requested: boolean
  metrics?: Record<string, number | string>
  progress?: CollectionProgress & { conflict_ids?: string[]; scoring_current?: ScoringCurrentJob[] }
}

export interface CollectionPlatformProgress {
  error_records?: { kind: 'parse' | 'save'; reason: string; title: string; company: string; city?: string; keyword?: string; page?: number }[]
  filter_records?: { title: string; company: string; source_job_id: string; reason: string }[]
  status: string
  new: number
  target: number | null
  percent?: number | null
  seen?: number
  duplicate?: number
  filtered?: number
  parse_failed?: number
  save_failed?: number
  keyword?: string
  city?: string
  page?: number
  max_pages?: number
  phase?: string
  reason_code?: string
  message?: string
  city_results?: Record<string, { status: string; new_total: number; reason_code?: string }>
}

export interface CollectionProgress {
  /** v54 图8：当前阶段——主进度条按它切换（找岗位/打分/投递/等回复）。 */
  phase?: 'collecting' | 'scoring' | 'delivering' | 'monitoring'
  /** v54 图8：投递实时进度（每投完一个岗位更新一次）。 */
  deliver?: { sent?: number; failed?: number; total?: number }
  /** v54 图10：当前等待的倒计时（风控等待/岗位间随机等待/安全冷却）。
   * 后端等完/跳过/停止时清掉，前端就不再画倒计时。 */
  wait?: { kind: string; label?: string; ends_at: number; total_seconds: number }
  session_id?: string
  session?: { id?: string; phase: string; round: number; deadline_at: string; next_wake_at?: string | null; reason_code?: string; manual_pending_reason?: string; new?: number }
  run_id?: string
  outcome?: string
  current_platform?: string
  platform_index?: number
  platform_total?: number
  platforms?: Record<string, CollectionPlatformProgress>
  collected_job_ids?: string[]
}

export interface VerificationHold {
  id: number
  platform: string
  url: string
  evidence: string
  created_at: string
}

/** 最近走过投递流程的岗位（GET /api/workbench 的 delivery_jobs 字段）。
 * 状态仍以 jobs.status 为准；step_* 是最近一次投递动作（确认 / 投出 /
 * 手动标记已投 / 失败），只用来补“走到哪一步、什么时候”，不另起状态。 */
export interface DeliveryJob {
  id: string
  company: string
  title: string
  source_platform?: 'boss' | 'zhilian' | '51job' | 'liepin' | string
  status: string
  last_error?: string | null
  last_error_code?: string | null
  updated_at?: string
  url?: string
  step_action?: 'approved' | 'sent' | 'manual_sent' | 'error' | string | null
  step_detail?: string | null
  step_created_at?: string | null
}

export interface VerificationState {
  open_hold: VerificationHold | null
  suspended_platforms: string[]
}

export interface PlatformLoginInfo {
  status: 'logged_in' | 'logged_out' | 'unknown'
  checked_at?: string | null
  detail?: string | null
}

export interface LastPreflight {
  checks: Array<{
    id: string
    title: string
    status: 'pass' | 'warning' | 'error'
    message: string
    detail: string
    action?: 'config' | 'browser' | ''
  }>
  ran_at?: string | null
}

interface WorkbenchData {
  funnel: FunnelData
  funnel_today: FunnelData
  pending_confirmation: Job[]
  pending_greetings: Job[]
  send_errors: Job[]
  needs_resume: Job[]
  send_quota: {
    daily_limit: number
    sent: number
    remaining: number
    exhausted: boolean
    per_platform?: Record<string, { limit: number; sent: number; remaining: number }>
    suspended_platforms?: string[]
  }
  open_hold?: VerificationHold | null
  task: WorkbenchTask | null
  last_task: WorkbenchTask | null
  /** 后端 GET /api/workbench 新增：最近的任务快照（新的在前），投递跟进页的任务列表读它。 */
  tasks?: WorkbenchTask[]
  /** 后端 GET /api/workbench 新增：最近走过投递流程的岗位 + 最近一次投递动作。 */
  delivery_jobs?: DeliveryJob[]
  /** 后端 GET /api/workbench 新增：四平台登录状态（后端未提供时为空）。 */
  platform_login?: Partial<Record<string, PlatformLoginInfo>> | null
  /** 后端 GET /api/workbench 新增：最近一次 preflight 结果（后端未提供时为空）。 */
  last_preflight?: LastPreflight | null
}

interface HistoryDetailPayload {
  schema: string
  hr_question?: string
  ai_reply?: string
  pending_hr_question?: string
  pending_history_id?: number
  manual_reply?: string
  system_reason?: string
  conversation_tail?: Array<{
    sender: string
    text: string
    time?: string
    kind?: string
  }>
}

interface HistoryItem {
  id: number
  job_id: string
  action: string
  detail: string
  detail_payload?: HistoryDetailPayload
  created_at: string
  company: string
  title: string
  url?: string
  source_platform?: string
  resume_path?: string
  resolved?: boolean
}

const emptyWorkbench: WorkbenchData = {
  funnel: {},
  funnel_today: {},
  pending_confirmation: [],
  pending_greetings: [],
  send_errors: [],
  needs_resume: [],
  send_quota: { daily_limit: 30, sent: 0, remaining: 30, exhausted: false },
  open_hold: null,
  task: null,
  last_task: null,
  tasks: [],
  delivery_jobs: [],
}

type DashboardDataScope = 'workbench' | 'jobs' | 'monitor' | 'all'

export function useDashboard(scope: DashboardDataScope = 'all') {
  const [workbench, setWorkbench] = useState<WorkbenchData>(emptyWorkbench)
  const [history, setHistory] = useState<HistoryItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  /** 本地服务没起来、正在等它：首次打开时骨架屏没有文字，用这条状态
   * 告诉页面“正在等本地服务启动”，而不是一直干等或卡死在报错上。 */
  const [waitingForService, setWaitingForService] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [lastRefreshedAt, setLastRefreshedAt] = useState<Date | null>(null)
  const refreshingRef = useRef<AbortController | null>(null)
  /** 手动刷登录态（只刷这一格，不走整页 refresh）：正在跑的请求、以及
   * 最近一次刷出的新值——轮询回来的旧库值不能把它盖回去，要等巡检
   * 把同样结果写库（checked_at 更新）后才由库值接管。 */
  const [platformLoginRefreshing, setPlatformLoginRefreshing] = useState(false)
  const [platformLoginRefreshedAt, setPlatformLoginRefreshedAt] = useState<Date | null>(null)
  const [platformLoginRefreshError, setPlatformLoginRefreshError] = useState('')
  const loginRefreshRef = useRef<AbortController | null>(null)
  const platformLoginOverrideRef = useRef<Partial<Record<string, PlatformLoginInfo>> | null>(null)
  const greetingRevisionRef = useRef(0)
  const firstLoadRef = useRef(true)
  /** 最近一次“没连上”报错是几点置上的（v36）：只有比它更新的检测
   * 才有资格清掉它——旧库行、旧的手动刷新结果都不算。 */
  const loginRefreshErrorAtRef = useRef<number | null>(null)

  /** 新鲜检测到了，清掉“没连上”的旧错误（v36）。以前错误一旦置上就
   * 再也不清：之后检测成功、卡片变绿了，红字还留在上面，互相打架。
   * 只有比报错更新的检测才清；浏览器没连上时推导不出东西（states
   * 为 null 或全是旧时间戳），旧值不动、错误也不动。 */
  const clearLoginRefreshErrorOnFreshStates = useCallback((states: Partial<Record<string, PlatformLoginInfo>> | null | undefined) => {
    const errorAt = loginRefreshErrorAtRef.current
    if (errorAt === null || !states) return
    const hasNewer = Object.values(states).some(info => {
      if (!info?.checked_at) return false
      const checkedMs = Date.parse(info.checked_at)
      // >= 而不是 >：失败和紧接着的成功可能落在同一毫秒，那次检测
      // 仍然是失败之后新跑出来的，不是旧库行。
      return !Number.isNaN(checkedMs) && checkedMs >= errorAt
    })
    if (hasNewer) {
      loginRefreshErrorAtRef.current = null
      setPlatformLoginRefreshError('')
    }
  }, [])

  /** 还在重试等本地服务：首次打开就把骨架屏撤掉，换成等服务的提示，
   * 页面其余部分照常渲染，服务起来数据一到就自动补上。 */
  const markWaiting = useCallback(() => {
    setWaitingForService(true)
    if (firstLoadRef.current) {
      setLoading(false)
      setError('正在等本地服务启动，好了会自动刷新……')
    }
  }, [])

  const updateGreetingJob = useCallback((updated: Job) => {
    greetingRevisionRef.current += 1
    setWorkbench(previous => ({
      ...previous,
      pending_confirmation: previous.pending_confirmation.map(job => job.id === updated.id ? updated : job),
      pending_greetings: previous.pending_greetings.map(job => job.id === updated.id ? updated : job),
      send_errors: previous.send_errors.map(job => job.id === updated.id ? updated : job),
    }))
  }, [])

  const fetchAll = useCallback(async () => {
    if (refreshingRef.current) return
    const controller = new AbortController()
    refreshingRef.current = controller
    const timeout = window.setTimeout(() => controller.abort(), 15000)
    const greetingRevision = greetingRevisionRef.current
    setRefreshing(true)
    try {
      // 投递跟进页也要看任务和投递结果，所以 monitor 也读 workbench；
      // 轮询间隔仍是 monitor 的 2 秒（下面 pollIntervalMs 不变）。
      const needsWorkbench = scope === 'all' || scope === 'workbench' || scope === 'monitor'
      const needsHistory = scope === 'all' || scope === 'monitor'
      // 读请求走统一重试：本地服务没起来（联不上/5xx）时指数退避再试，
      // 每次尝试单独 8 秒超时；整个 fetchAll 仍有 15 秒的外层上限兜底。
      const retryOptions = { signal: controller.signal, timeoutMs: 8000, retries: 3, onRetry: markWaiting }
      const [workbenchRes, historyRes] = await Promise.all([
        needsWorkbench ? fetchWithRetry('/api/workbench', { cache: 'no-store' as const }, retryOptions) : Promise.resolve(null),
        needsHistory ? fetchWithRetry('/api/history?limit=50&include_unresolved=1&include_monitor_conversations=1', { cache: 'no-store' as const }, retryOptions) : Promise.resolve(null),
      ])
      if ((workbenchRes && !workbenchRes.ok) || (historyRes && !historyRes.ok)) {
        // 5xx 是本地服务那边的问题（重试已耗尽），按没起来处理；
        // 4xx 是业务错误，不当成服务没起来。
        const status = (workbenchRes && !workbenchRes.ok ? workbenchRes.status : historyRes?.status) ?? 0
        if (status >= 500) throw new Error('SERVICE_NOT_READY')
        throw new Error('没读到数据')
      }
      const [workbenchData, historyData] = await Promise.all([
        workbenchRes ? readJsonWithTimeout(workbenchRes) : Promise.resolve(undefined),
        historyRes ? readJsonWithTimeout(historyRes) : Promise.resolve(undefined),
      ])

      if (controller.signal.aborted || refreshingRef.current !== controller) return

      // An older poll must not replace the greeting returned by a successful save.
      if (workbenchData !== undefined && greetingRevision === greetingRevisionRef.current) {
        const incoming = workbenchData as WorkbenchData
        // 登录态那一格单独按 checked_at 取新：刚手动刷出 / 挂载诊断算出
        // 的新值，在巡检写库之前不被轮询的旧库值盖回去。
        const platformLogin = platformLoginOverrideRef.current
          ? mergePlatformLoginStates(incoming.platform_login, platformLoginOverrideRef.current)
          : incoming.platform_login
        // v36：巡检/预检写库的新检测经轮询回来时，比报错更新的同样清掉旧错误。
        clearLoginRefreshErrorOnFreshStates(incoming.platform_login)
        setWorkbench({ ...incoming, platform_login: platformLogin })
      }
      if (historyData !== undefined) setHistory(historyData)
      setLastRefreshedAt(new Date())
      setWaitingForService(false)
      setError('')
    } catch (err) {
      if (refreshingRef.current !== controller) return
      console.error('Failed to fetch dashboard data:', err)
      // 重试耗尽后才定稿报错：服务没起来就继续等（5 秒一轮还会再试），
      // 只是文案上不再说“自动再试”，改成手动也能刷。
      const notReady = err instanceof Error && (err.message === 'SERVICE_NOT_READY' || isServiceNotReadyError(err))
      setWaitingForService(notReady)
      setError(
        controller.signal.aborted
          ? '刷新超时了，会自动再试。'
          : notReady
            ? '本地服务还没起来，正在等它启动，好了会自动刷新，点“刷新一下”也能马上再试。'
            : '数据没读出来，会自动再试。',
      )
    } finally {
      window.clearTimeout(timeout)
      if (refreshingRef.current === controller) {
        refreshingRef.current = null
        setRefreshing(false)
        setLoading(false)
        firstLoadRef.current = false
      }
    }
  }, [scope, markWaiting, clearLoginRefreshErrorOnFreshStates])

  /** 把一次新鲜诊断算出的登录态写进 workbench（只动 platform_login
   * 这一格），并记住它，供上面轮询合并时按 checked_at 取新。 */
  const applyPlatformLoginStates = useCallback((states: Partial<Record<string, PlatformLoginInfo>> | null) => {
    if (!states) return
    clearLoginRefreshErrorOnFreshStates(states)
    platformLoginOverrideRef.current = mergePlatformLoginStates(platformLoginOverrideRef.current, states)
    setWorkbench(previous => ({
      ...previous,
      platform_login: mergePlatformLoginStates(previous.platform_login, states),
    }))
  }, [clearLoginRefreshErrorOnFreshStates])

  /** 只刷新各平台登录状态：调现成的 POST /api/browser/diagnostics
   * （只做检测，不拉起浏览器、不跑任务、不碰预检），按后端的同一份
   * 口径在前端推导四平台状态，只更新 platform_login。
   * 注意：这个端点本身不写库——写库是预检接口和后台巡检的事；这里
   * 刷出的新值先存在前端，巡检（4 分钟一轮）会把同样结果写库，之
   * 前轮询按 checked_at 取新，不会盖回旧值。返回诊断原样（供首页
   * 同步浏览器诊断面板），请求失败返回 null；浏览器没连上时返回
   * 诊断原样但不更新登录态（沿用旧值，不把“连不上”误标成掉线）。 */
  const refreshPlatformLogin = useCallback(async (): Promise<unknown> => {
    if (loginRefreshRef.current) return null
    const controller = new AbortController()
    loginRefreshRef.current = controller
    const timer = window.setTimeout(() => controller.abort(), 15000)
    setPlatformLoginRefreshing(true)
    setPlatformLoginRefreshError('')
    loginRefreshErrorAtRef.current = null
    try {
      const res = await fetch('/api/browser/diagnostics', {
        method: 'POST',
        cache: 'no-store' as const,
        signal: controller.signal,
      })
      if (!res.ok) throw new Error(`HTTP_${res.status}`)
      const data: unknown = await res.json().catch(() => null)
      if (controller.signal.aborted) return null
      const states = derivePlatformLoginStates(data)
      if (!states) {
        setPlatformLoginRefreshError('浏览器没连上，登录态查不出来。先点任意一个平台的“去登录”，再点刷新试试。')
        loginRefreshErrorAtRef.current = Date.now()
        return data
      }
      applyPlatformLoginStates(states)
      setPlatformLoginRefreshedAt(new Date())
      return data
    } catch (err) {
      if (controller.signal.aborted) {
        setPlatformLoginRefreshError('刷新超时了，稍后再点刷新试试。')
      } else {
        console.error('Failed to refresh platform login states:', err)
        setPlatformLoginRefreshError('没刷出来，稍后再点刷新试试。')
      }
      loginRefreshErrorAtRef.current = Date.now()
      return null
    } finally {
      window.clearTimeout(timer)
      if (loginRefreshRef.current === controller) {
        loginRefreshRef.current = null
        setPlatformLoginRefreshing(false)
      }
    }
  }, [applyPlatformLoginStates])

  const updateJobStatus = useCallback(async (jobId: string, status: string) => {
    const res = await fetch('/api/jobs/status', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ job_ids: [jobId], status }),
    })
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      throw new Error(data.error || '状态没改成')
    }
    await fetchAll()
  }, [fetchAll])

  const startTask = async (mode: 'full' | 'collect' | 'score' | 'rescore' | 'greet' | 'monitor' | 'deliver', options?: Record<string, unknown>) => {
    // v36：以前是裸 fetch，没有任何超时——启动接口一旦卡住（比如别的
    // 请求正占着任务锁做浏览器操作），点“重新采集”就永远转圈。现在
    // 单次 30 秒超时（接口本身是立即返回、任务在后台线程跑，30 秒绰绰有余），
    // 超时抛可读错误，弹窗恢复可点并显示原因。
    let res: Response
    try {
      res = await fetchWithRetry('/api/workbench/task', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode, ...(options ? { options } : {}) }),
      }, { timeoutMs: 30_000, retries: 0 })
    } catch (err) {
      if (isAbortError(err)) throw new Error('启动任务超时了，稍后再点一次。')
      throw err
    }
    if (!res.ok) {
      const data = await readJsonWithTimeout(res).catch(() => ({})) as { error?: string; messages?: unknown }
      const details = Array.isArray(data.messages) ? (data.messages as unknown[]).map(String).filter(Boolean).join('；') : ''
      throw new Error([data.error || '没启动', details].filter(Boolean).join('：'))
    }
    const task = await readJsonWithTimeout(res) as WorkbenchTask
    setWorkbench(prev => ({ ...prev, task, last_task: task }))
    void fetchAll()
    return task
  }

  const stopTask = async (taskId: string) => {
    const res = await fetch(`/api/workbench/task/${taskId}/stop`, { method: 'POST' })
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      throw new Error(data.error || '没停下来')
    }
    const task = await res.json() as WorkbenchTask
    setWorkbench(prev => ({ ...prev, task, last_task: task }))
    void fetchAll()
    return task
  }

  const pauseTask = async (taskId: string) => {
    const res = await fetch(`/api/workbench/task/${taskId}/pause`, { method: 'POST' })
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      throw new Error(data.error || '没暂停')
    }
    const task = await res.json() as WorkbenchTask
    setWorkbench(prev => ({ ...prev, task, last_task: task }))
    void fetchAll()
    return task
  }

  const resumeTask = async (taskId: string) => {
    const res = await fetch(`/api/workbench/task/${taskId}/resume`, { method: 'POST' })
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      throw new Error(data.error || '没继续')
    }
    const task = await res.json() as WorkbenchTask
    setWorkbench(prev => ({ ...prev, task, last_task: task }))
    void fetchAll()
    return task
  }

  const resolveVerification = useCallback(async (holdId: number) => {
    const res = await fetch(`/api/delivery/verification/${holdId}/resolve`, { method: 'POST' })
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      throw new Error(data.error || '没能继续投递')
    }
    await fetchAll()
  }, [fetchAll])

  useEffect(() => {
    void fetchAll()
    // 骨架屏兜底：首屏数据 20 秒还没回来（请求挂起、定时器被节流等），
    // 不再一直晾着骨架，改成可重试的提示；数据到了会自动补上。
    const watchdog = window.setTimeout(() => {
      if (firstLoadRef.current) {
        setLoading(false)
        setError('数据一直没回来，点“刷新一下”再试。')
      }
    }, 20000)
    const pollIntervalMs = scope === 'monitor' ? 2000 : 5000
    const refreshWhenVisible = () => {
      if (document.visibilityState === 'visible') void fetchAll()
    }
    // Embedded browsers can report a hidden document even while its panel is open.
    // Keep polling; browsers may throttle background timers, and focus also refreshes immediately.
    const interval = window.setInterval(() => void fetchAll(), pollIntervalMs)
    window.addEventListener('focus', refreshWhenVisible)
    document.addEventListener('visibilitychange', refreshWhenVisible)
    return () => {
      window.clearTimeout(watchdog)
      window.clearInterval(interval)
      refreshingRef.current?.abort()
      refreshingRef.current = null
      window.removeEventListener('focus', refreshWhenVisible)
      document.removeEventListener('visibilitychange', refreshWhenVisible)
    }
  }, [fetchAll, scope])

  // Verification state rides on the workbench poll: an open hold means a
  // captcha paused delivery; suspended platforms come from the quota payload.
  const verification: VerificationState = {
    open_hold: workbench.open_hold ?? null,
    suspended_platforms: workbench.send_quota?.suspended_platforms ?? [],
  }

  return {
    workbench,
    verification,
    resolveVerification,
    history,
    loading,
    error,
    waitingForService,
    refreshing,
    lastRefreshedAt,
    refresh: fetchAll,
    applyPlatformLoginStates,
    refreshPlatformLogin,
    platformLoginRefreshing,
    platformLoginRefreshedAt,
    platformLoginRefreshError,
    updateGreetingJob,
    updateJobStatus,
    startTask,
    stopTask,
    pauseTask,
    resumeTask,
  }
}

export type { FunnelData, ActivityData, Job, TopCompany, WorkbenchData, HistoryDetailPayload, HistoryItem }
