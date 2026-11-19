import { useCallback, useState, useEffect, useRef } from 'react'

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

export interface WorkbenchTask {
  id: string
  mode: 'full' | 'collect' | 'score' | 'rescore' | 'greet' | 'monitor' | 'deliver'
  label: string
  status: string
  logs: string[]
  error?: string
  deadline_at?: string
  stop_reason?: string
  stop_requested: boolean
  metrics?: Record<string, number | string>
  progress?: CollectionProgress & { conflict_ids?: string[] }
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
  city_elapsed_ms?: number
  next_check_ms?: number
  last_window_new?: number | null
  city_new_total?: number
  city_results?: Record<string, { status: string; new_total: number; reason_code?: string }>
}

export interface CollectionProgress {
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

export interface VerificationState {
  open_hold: VerificationHold | null
  suspended_platforms: string[]
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
}

type DashboardDataScope = 'workbench' | 'jobs' | 'monitor' | 'all'

export function useDashboard(scope: DashboardDataScope = 'all') {
  const [workbench, setWorkbench] = useState<WorkbenchData>(emptyWorkbench)
  const [history, setHistory] = useState<HistoryItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [refreshing, setRefreshing] = useState(false)
  const [lastRefreshedAt, setLastRefreshedAt] = useState<Date | null>(null)
  const refreshingRef = useRef<AbortController | null>(null)
  const greetingRevisionRef = useRef(0)

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
      const needsWorkbench = scope === 'all' || scope === 'workbench'
      const needsHistory = scope === 'all' || scope === 'monitor'
      const fetchOptions = { cache: 'no-store' as const, signal: controller.signal }
      const [workbenchRes, historyRes] = await Promise.all([
        needsWorkbench ? fetch('/api/workbench', fetchOptions) : Promise.resolve(null),
        needsHistory ? fetch('/api/history?limit=50&include_unresolved=1&include_monitor_conversations=1', fetchOptions) : Promise.resolve(null),
      ])
      if ((workbenchRes && !workbenchRes.ok) || (historyRes && !historyRes.ok)) {
        throw new Error('没读到数据')
      }
      const [workbenchData, historyData] = await Promise.all([
        workbenchRes ? workbenchRes.json() : Promise.resolve(undefined),
        historyRes ? historyRes.json() : Promise.resolve(undefined),
      ])

      if (controller.signal.aborted || refreshingRef.current !== controller) return

      // An older poll must not replace the greeting returned by a successful save.
      if (workbenchData !== undefined && greetingRevision === greetingRevisionRef.current) setWorkbench(workbenchData)
      if (historyData !== undefined) setHistory(historyData)
      setLastRefreshedAt(new Date())
      setError('')
    } catch (err) {
      if (refreshingRef.current !== controller) return
      console.error('Failed to fetch dashboard data:', err)
      setError(controller.signal.aborted ? '刷新超时了，会自动再试。' : '数据没读出来，会自动再试。')
    } finally {
      window.clearTimeout(timeout)
      if (refreshingRef.current === controller) {
        refreshingRef.current = null
        setRefreshing(false)
        setLoading(false)
      }
    }
  }, [scope])

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
    const res = await fetch('/api/workbench/task', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode, ...(options ? { options } : {}) }),
    })
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      const details = Array.isArray(data.messages) ? data.messages.map(String).filter(Boolean).join('；') : ''
      throw new Error([data.error || '没启动', details].filter(Boolean).join('：'))
    }
    const task = await res.json() as WorkbenchTask
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
    refreshing,
    lastRefreshedAt,
    refresh: fetchAll,
    updateGreetingJob,
    updateJobStatus,
    startTask,
    stopTask,
  }
}

export type { FunnelData, ActivityData, Job, TopCompany, WorkbenchData, HistoryDetailPayload, HistoryItem }
