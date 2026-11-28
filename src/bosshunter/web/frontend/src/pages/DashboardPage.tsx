import { Link } from 'react-router-dom'
import { OutsourcingBadge } from '@/components/jobs/OutsourcingBadge'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useDashboard, type CollectionProgress, type DeliveryJob, type HistoryItem, type Job, type WorkbenchTask } from '@/hooks/useDashboard'
import { useConfig } from '@/hooks/useConfig'
import { useUpdateContext } from '@/hooks/useUpdate'
import { UpdateBanner } from '@/components/dashboard/UpdateBanner'
import { UpdateModal } from '@/components/dashboard/UpdateModal'
import { CHECK_UPDATE_EVENT } from '@/lib/updateEvents'
import { useAuth } from '@/hooks/useAuth'
import { AuthStatusBar } from '@/components/dashboard/AuthStatusBar'
import { useJobSearch, buildJobSearchParams, type JobSortKey, type JobSortOrder } from '@/hooks/useJobSearch'
import { Button } from '@/components/ui/button'
import { ModalPortal } from '@/components/ui/modal-portal'
import { START_ONBOARDING_EVENT } from '@/components/onboarding/OnboardingTour'
import { AnimatedNumber } from '@/components/AnimatedNumber'
import { JobsTable } from '@/components/dashboard/JobsTable'
import { RecycleBinPanel } from '@/components/dashboard/RecycleBinPanel'
import { TaskPhaseProgress, formatScoringCurrentJob } from '@/components/dashboard/ScoringProgress'
import { WaitCountdown } from '@/components/dashboard/WaitCountdown'
import { DeliveryTaskList } from '@/components/dashboard/DeliveryTaskList'
import { ScoreJobsDialog } from '@/components/dashboard/ScoreJobsDialog'
import { CollectJobsDialog } from '@/components/dashboard/CollectJobsDialog'
import { VerificationModal } from '@/components/dashboard/VerificationModal'
import { AppliedJobsPanel } from '@/components/dashboard/AppliedJobsPanel'
import { CollectionSessionsPanel } from '@/components/dashboard/CollectionSessionsPanel'
import { PlatformStatusCard } from '@/components/dashboard/PlatformStatusCard'
import { BrowserDiagnosticsPanel, type BrowserDiagnostics } from '@/components/dashboard/BrowserDiagnosticsPanel'
import { TrendChart } from '@/components/dashboard/TrendChart'
import { TopCompanies } from '@/components/dashboard/TopCompanies'
import { PipelineFlow } from '@/components/dashboard/PipelineFlow'
import { RecentActivity } from '@/components/dashboard/RecentActivity'
import type { ActivityData, TopCompany } from '@/hooks/useDashboard'
import { JobFilterBar } from '@/components/jobs/JobFilterBar'
import { InterviewPreparation } from '@/components/jobs/InterviewPreparation'
import { parseHistoryDetail } from '@/lib/historyDetail'
import { fetchWithRetry, isAbortError, withTimeout } from '@/lib/api'
import { PLATFORM_LABELS, PLATFORM_IDS, openPlatformLogin, openPlatformPage, portMismatchNote, type PlatformId } from '@/lib/platforms'
import { derivePlatformLoginStates } from '@/lib/platformLogin'
import {
  EMPTY_JOB_FILTERS,
  filterJobs,
  hasInvalidSalaryRange,
  jobCityBaseName,
  useDebouncedValue,
  type JobFilters,
} from '@/lib/jobFilters'
import { getActionLabel, getStatusLabel, isAppliedJobStatus, STATUS_LABELS } from '@/lib/status'
import { describeGreetTaskOutcome, greetPauseReasonLabel } from '@/lib/greetTask'
import { cn } from '@/lib/utils'
import {
  AlertTriangle,
  BriefcaseBusiness,
  ChevronDown,
  Download,
  ExternalLink,
  Eye,
  MessageCircle,
  Pencil,
  Play,
  RefreshCw,
  ShieldCheck,
  Send,
  Sparkles,
  Square,
  Trash2,
  XCircle,
} from 'lucide-react'

type WorkbenchMode = 'full' | 'collect' | 'rescore' | 'monitor'
type DashboardView = 'workbench' | 'jobs' | 'monitor'
type StatsScope = 'today' | 'total'

const TASK_STAGE_LABELS: Array<{ match: string; label: string }> = [
  { match: '开始采集岗位', label: '开始找岗位' },
  { match: '开始 AI 评分', label: '开始打分' },
  { match: '开始重新评分', label: '开始重新打分' },
  { match: 'AI 评分进度', label: '打分进度' },
  { match: '发送失败待处理', label: '有几个没投出去' },
  { match: '执行一轮监测', label: '看一轮 HR 回复' },
  { match: '本轮监测完成，30 分钟后再次检查', label: '这轮看完了，30 分钟后再看' },
]

function currentTaskStage(task: WorkbenchTask) {
  const session = task.progress?.session
  if (session) {
    const phase = ({ collecting: '正在找', waiting_round: '歇一会儿', waiting_quota: '等明天额度', waiting_risk: '先冷静一下', waiting_retry: '等它恢复', waiting_user: '等你处理', finished: '这轮结束了' } as Record<string, string>)[session.phase] || session.phase
    return `第 ${session.round} 轮 · ${phase}${session.reason_code ? ` · ${session.reason_code}` : ''}${session.next_wake_at ? ` · 最早恢复 ${new Date(session.next_wake_at).toLocaleString()}` : ''}`
  }
  const scoringCurrent = task.progress?.scoring_current
  if (scoringCurrent?.length && ['running', 'stopping'].includes(task.status)) {
    const total = task.metrics?.ai_total
    const line = formatScoringCurrentJob(scoringCurrent[0], typeof total === 'number' ? total : 0)
    return scoringCurrent.length > 1 ? `${line}（同时还在打另外 ${scoringCurrent.length - 1} 个）` : line
  }
  if (task.progress?.outcome === 'scoring' && ['running', 'stopping'].includes(task.status)) {
    const completed = task.metrics?.ai_completed
    const total = task.metrics?.ai_total
    return typeof total === 'number'
      ? `打分进度 ${completed || 0}/${total}`
      : '找完了，正在给这轮新找到的岗位打分'
  }
  const logs = task.logs || []
  for (const log of logs.slice().reverse()) {
    if (log.includes('AI 评分进度')) {
      const m = log.match(/(\d+)\/(\d+)/)
      return m ? `打分进度 ${m[1]}/${m[2]}` : '正在打分'
    }
    if (log.includes('招呼语进度')) return log
    if (log.includes('招呼语发送结果')) return log
    if (log.includes('发送招呼语')) return '发这条打招呼'
    if (log.includes('招呼语生成结果')) return log
    if (log.includes('生成招呼语')) return log
    if (log.includes('本轮监测完成')) return log
    const stage = TASK_STAGE_LABELS.find(item => log.includes(item.match))
    if (stage) return stage.label
  }
  if (task.status === 'running') return `${task.label}正在开始`
  if (task.status === 'stopping') return `${task.label}正在停下`
  if (task.status === 'completed') return `${task.label}已经做完了`
  if (task.status === 'stopped') return `${task.label}已经停了`
  if (task.status === 'failed') return `${task.label}这次没跑成`
  return `${task.label}不知道现在什么状态`
}

function taskStatusText(status: string) {
  if (status === 'failed') return '这次没跑成'
  if (status === 'completed') return '已经结束了'
  if (status === 'stopped') return '已经停了'
  if (status === 'stopping') return '正在停下'
  return '正在跑'
}

function taskStatusClass(status: string) {
  if (status === 'failed') return 'border-danger-border bg-danger-soft'
  if (status === 'completed' || status === 'stopped') return 'border-card-border bg-card'
  return 'border-primary/20 bg-secondary'
}

function taskStatusTitle(status: string) {
  if (status === 'completed' || status === 'stopped') return '上次任务怎么样'
  return '现在到哪一步了'
}

function taskStopReasonLabel(reason?: string) {
  if (reason === 'daily_limit') return '今天能投的数量用完了，没投的岗位都留着；明天额度恢复了再投。'
  if (reason === 'stopped') return '已经按你的要求停下了，没处理的岗位还在队列里留着。'
  return reason
}

function taskErrorFeedback(error: string, browserName = '浏览器') {
  const normalized = error.toLowerCase()
  if (
    normalized.includes('api key')
    || normalized.includes('authentication')
    || normalized.includes('unauthorized')
    || normalized.includes('401')
    || normalized.includes('403')
  ) {
    return {
      title: '打分服务没连上',
      detail: '打分服务暂时不可用，请稍后再试；一直不行请联系管理员。',
    }
  }
  if (
    normalized.includes('chrome')
    || normalized.includes('cdp')
    || normalized.includes('websocket')
    || normalized.includes('browser runtime')
    || normalized.includes('not connected')
  ) {
    return {
      title: `${browserName}连接中断了`,
      detail: `请确认${browserName}已经打开并开启了远程调试，再点上面的“检查能不能跑”。`,
    }
  }
  if (normalized.includes('zhipin') || normalized.includes('登录') || normalized.includes('login')) {
    return {
      title: '招聘平台页面或登录状态异常',
      detail: `请在已连接的${browserName}中打开 BOSS 直聘并确认账号仍处于登录状态。`,
    }
  }
  return {
    title: '任务运行失败',
    detail: '请看一下原始错误；修好设置或连接问题后，重新点检查。',
  }
}

interface DashboardPageProps {
  view?: DashboardView
}

interface PreflightCheck {
  id: string
  title: string
  status: 'pass' | 'warning' | 'error'
  message: string
  detail: string
  action?: 'config' | 'browser' | ''
}

/** 各平台“未发现已打开的页面”检查项 id → 平台（v28：已登录却缺页时自动开页）。 */
const PLATFORM_TAB_CHECK_IDS: Record<string, PlatformId> = {
  boss_tab: 'boss',
  zhilian_tab: 'zhilian',
  '51job_tab': '51job',
  liepin_tab: 'liepin',
}

function scrollToElementById(id: string) {
  const target = document.getElementById(id)
  if (target && typeof target.scrollIntoView === 'function') {
    target.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }
}

/** 预检失败提示：列出实际没通过的项，最多列 3 项，多了加“等 N 项”。 */
function preflightFailureNotice(checks: PreflightCheck[]): string {
  const failed = checks.filter(check => check.status === 'error')
  const items = failed.length ? failed : checks.filter(check => check.status !== 'pass')
  if (items.length === 0) return '启动前预检未通过，请按检查提示修复后，再重新启动。'
  const shown = items.slice(0, 3).map(check => check.message)
  const suffix = items.length > 3 ? `等 ${items.length} 项` : ''
  return `启动前预检未通过：${shown.join('；')}${suffix}。`
}

/** 启动失败的提示：刚强制停下、旧任务还没完全退出时后端会报“浏览器正忙”，
 * 那是后端锁的正常保护，如实告诉用户等几秒再点，不要写成错误。 */
function startFailureMessage(err: unknown): string {
  const message = err instanceof Error ? err.message : '操作失败'
  return /忙|busy/i.test(message) ? '上一个任务还在退出，等几秒再点启动。' : message
}

/** 启动前预检单次超时（v36）：后端冷启动诊断实测最慢约 30 秒，留足余量；
 * 超时后调用方恢复按钮、显示可读错误，不一直转圈。POST 只发一次，不重试。 */
const PREFLIGHT_TIMEOUT_MS = 90_000
const PREFLIGHT_TIMEOUT_MESSAGE = '启动检查超时了，浏览器可能还没准备好，稍后再点一次。'
/** 读预检响应体超时：响应头到了、body 卡住时也一样要能恢复。 */
const PREFLIGHT_BODY_TIMEOUT_MS = 10_000

const modes: Array<{ mode: WorkbenchMode; title: string; description: string }> = [
  {
    mode: 'full',
    title: '开始自动找工作',
    description: '找岗、打分、自动投、盯回复，一气呵成',
  },
  {
    mode: 'collect',
    title: '只找新岗位',
    description: '按你的条件去各平台搜新岗位',
  },
  {
    mode: 'monitor',
    title: '只看 HR 回复',
    description: '看看投过的岗位，HR 有没有回你',
  },
]

const statItems = [
  { key: '采集总数', todayLabel: '今天新找到的岗位', totalLabel: '一共找到的岗位' },
  { key: '初筛通过', todayLabel: '今天筛出来的好岗位', totalLabel: '一共筛出来的好岗位', highlight: true },
  { key: 'AI评分', todayLabel: '今天新增且已打分的岗位', totalLabel: '一共打过分的岗位' },
  { key: 'pending', todayLabel: '等着投的岗位', totalLabel: '等着投的岗位', highlight: true, current: true },
  { key: '发送', todayLabel: '今天已投出的简历', totalLabel: '一共已投出的简历', highlight: true },
]

const taskMetricItems = [
  { key: 'collect_seen', label: '这轮看了多少' },
  { key: 'collect_new', label: '这轮新找到' },
  { key: 'collect_duplicate', label: '重复的' },
  { key: 'collect_filtered', label: '筛掉的' },
  { key: 'collect_parse_failed', label: '没看懂的' },
  { key: 'collect_save_failed', label: '没存上的' },
  { key: 'ai_completed', label: '已打分' },
  { key: 'ai_total', label: '要打分的' },
  { key: 'ai_passed', label: '打分通过的' },
  { key: 'ai_filtered', label: '打分筛掉的' },
  { key: 'ai_failed', label: '打分失败的' },
  { key: 'send_success', label: '投出去的' },
  { key: 'send_deferred', label: '下次再投的' },
  { key: 'send_remaining_quota', label: '今天还能投' },
  { key: 'greet_generated', label: '新写的' },
  { key: 'greet_preserved', label: '沿用以前的' },
  { key: 'greet_failed', label: '没写出来的' },
  { key: 'greet_paused', label: '提前停下的' },
]

const TERMINAL_TASK_STATUSES = ['completed', 'failed', 'stopped']

async function waitForGreetTask(taskId: string, timeoutMs = 300000): Promise<WorkbenchTask | null> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const res = await fetch('/api/workbench')
    if (res.ok) {
      const data = await res.json().catch(() => ({}) as { task?: WorkbenchTask | null; last_task?: WorkbenchTask | null })
      const task = data.task?.id === taskId
        ? data.task
        : data.last_task?.id === taskId ? data.last_task : null
      if (task && TERMINAL_TASK_STATUSES.includes(task.status)) return task
    }
    await new Promise(resolve => setTimeout(resolve, 2000))
  }
  return null
}

function jobSubtitle(job: Job) {
  return [job.score ? `匹配 ${job.score}` : '', job.salary, job.hr_active || '不知道 HR 活跃不活跃', getStatusLabel(job.status)].filter(Boolean).join(' · ')
}

function safeExternalUrl(value: string | undefined, platform: string) {
	if (!value) return ''
	try {
		const url = new URL(value)
		if (url.protocol !== 'https:') return ''
		const allowedDomain = platform === 'zhilian'
			? 'zhaopin.com'
			: platform === '51job'
				? '51job.com'
				: platform === 'liepin'
					? 'liepin.com'
					: ''
		if (!allowedDomain) return ''
		return url.hostname === allowedDomain || url.hostname.endsWith(`.${allowedDomain}`) ? url.href : ''
	} catch {
		return ''
	}
}

function monitorChatUrl(item: HistoryItem) {
  const platform = item.source_platform || 'boss'
	if (platform === 'boss') return ''
	return safeExternalUrl(item.url, platform)
}

function monitorLinkLabel(item: HistoryItem) {
  return (item.source_platform || 'boss') === 'boss' ? '打开和 HR 的聊天' : '打开这个页面'
}

async function parsePreflightResponse(res: Response) {
  const rawText = await res.text()
  let data: { ok?: boolean; messages?: unknown; checks?: unknown; error?: string } = {}
  try {
    data = rawText ? JSON.parse(rawText) : {}
  } catch {
    const message = `无法解析预检响应：预检接口返回 ${res.status}`
    return {
      ok: false,
      messages: [message],
      checks: [{ id: 'preflight_api', title: '启动检查', status: 'error', message, detail: '请重启简历猎手后重试。' }] as PreflightCheck[],
    }
  }
  const messages = Array.isArray(data.messages) ? [...new Set(data.messages.map(String).filter(Boolean))] : []
  const checks = Array.isArray(data.checks)
    ? data.checks.filter((item): item is PreflightCheck => Boolean(
      item
      && typeof item === 'object'
      && 'id' in item
      && 'status' in item
      && 'message' in item
    ))
    : []
  if (data.error && !messages.includes(String(data.error))) messages.push(String(data.error))
  const hasActionableChecks = checks.some(check => check.status !== 'pass')
  if (!res.ok && !hasActionableChecks && messages.length === 0) messages.push(`预检接口返回 ${res.status}`)
  if (!data.ok && !hasActionableChecks && messages.length === 0) messages.push('后端未返回具体原因')
  if ((!res.ok || !data.ok) && !hasActionableChecks && messages.length > 0) {
    checks.push(...messages.map((message, index) => ({
      id: `legacy-${index}`,
      title: '启动检查',
      status: 'error' as const,
      message,
      detail: !res.ok ? `接口状态：HTTP ${res.status}。请修复后重新检查。` : '',
    })))
  }
  return { ok: Boolean(res.ok && data.ok), messages, checks }
}

function CompactNotice({ message, danger = false }: { message: string; danger?: boolean }) {
  return (
    <details className={cn('group mt-2 rounded-lg px-2 text-xs', danger ? 'bg-danger-soft text-danger' : 'bg-secondary text-primary')}>
      <summary className="flex h-8 cursor-pointer list-none items-center gap-2 [&::-webkit-details-marker]:hidden">
        <span role="status" className="min-w-0 flex-1 truncate" title={message}>{message}</span>
        <ChevronDown className="h-3 w-3 shrink-0 group-open:rotate-180" />
      </summary>
      <p className="break-words border-t border-current/10 py-2 leading-5">{message}</p>
    </details>
  )
}

/** 每个未通过项的落点：简历去设置页的上传区，浏览器/登录相关去首页的
 * 登录态卡片，配置相关去设置页，都没有明确落点就再检查一次。 */
function preflightCheckAction(check: PreflightCheck, onRetry: () => void): { label: string; onClick: () => void } {
  if (check.id === 'resume') {
    return { label: '去传简历', onClick: () => window.location.assign('/config#resume-upload') }
  }
  if (check.action === 'config') {
    return { label: '去设置', onClick: () => window.location.assign('/config') }
  }
  if (
    check.action === 'browser'
    || ['browser_runtime', 'chrome_connection', 'chrome_product'].includes(check.id)
    || check.id in PLATFORM_TAB_CHECK_IDS
    || check.id.endsWith('_login')
    || check.id.endsWith('_blocked')
    || check.id.endsWith('_page')
  ) {
    return { label: '去登录看看', onClick: () => scrollToElementById('platform-login-card') }
  }
  return { label: '再检查一次', onClick: onRetry }
}

function PreflightPanel({
  checks,
  checking,
  onRetry,
  expandSignal = 0,
}: {
  checks: PreflightCheck[]
  checking: boolean
  onRetry: () => void
  /** 每次预检失败就 +1：失败时自动展开详情，不用用户自己点开看。 */
  expandSignal?: number
}) {
  const [expanded, setExpanded] = useState(false)
  useEffect(() => {
    if (expandSignal > 0) setExpanded(true)
  }, [expandSignal])
  const actionableChecks = checks.filter(check => check.status !== 'pass')
  if (actionableChecks.length === 0) return null

  const errors = actionableChecks.filter(check => check.status === 'error').length
  const needsConfig = actionableChecks.some(check => check.action === 'config')
  const heading = `${actionableChecks.length} 件事${errors ? '要处理' : '提醒你'}`
  const summary = `${heading} · ${actionableChecks[0].message}`

  return (
    <div role="region" aria-label="启动检查结果" className={`mt-2 rounded-lg border px-2 text-xs ${
      errors ? 'border-danger-border bg-danger-soft' : 'border-warning-border bg-warning-soft'
    }`}>
      <div className="flex h-8 min-w-0 items-center gap-2">
        {errors
          ? <XCircle className="h-3.5 w-3.5 shrink-0 text-danger" />
          : <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-warning" />}
        <span role="status" title={summary} className="min-w-0 flex-1 truncate text-foreground">{summary}</span>
        <button type="button" aria-expanded={expanded} aria-controls="preflight-details" onClick={() => setExpanded(value => !value)} className="flex h-7 shrink-0 items-center gap-1 rounded px-1 text-muted hover:text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary">
          {expanded ? '收起来' : '看看详情'}<ChevronDown className={cn('h-3 w-3', expanded && 'rotate-180')} />
        </button>
        {needsConfig && (
          <Button variant="ghost" size="sm" className="h-7 shrink-0 px-1" onClick={() => window.location.assign('/config')}>
            去设置
          </Button>
        )}
        <Button variant="ghost" size="sm" className="h-7 shrink-0 px-1" onClick={onRetry} disabled={checking}>
          <RefreshCw className={`mr-1 h-3 w-3 ${checking ? 'animate-spin' : ''}`} />
          {checking ? '检查中' : '再检查一次'}
        </Button>
      </div>
      <div id="preflight-details" hidden={!expanded} className="space-y-2 border-t border-current/10 py-2 text-xs leading-5">
        {actionableChecks.map(check => {
          const action = preflightCheckAction(check, onRetry)
          return (
            <div key={`${check.id}-${check.title}`} className="break-words">
              <p className="text-foreground"><span className="font-semibold">{check.title}：</span>{check.message}</p>
              {check.detail && <p className="text-muted">{check.detail}</p>}
              <Button variant="ghost" size="sm" className="mt-1 h-7 shrink-0 px-1" onClick={action.onClick} disabled={checking && action.label === '再检查一次'}>
                {checking && action.label === '再检查一次' ? '检查中…' : action.label}
              </Button>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function GettingStartedCard({
  onCheck,
  checking,
  notice,
  noticeDanger = false,
  checks,
  onRetry,
  expandSignal = 0,
  browserName = '浏览器',
  onLoginFeedback,
}: {
  onCheck: () => void
  checking: boolean
  notice: string
  /** 预检失败的提示用红字：notice 本身是失败的具体原因时才为 true。 */
  noticeDanger?: boolean
  checks: PreflightCheck[]
  onRetry: () => void
  expandSignal?: number
  browserName?: string
  /** 登录按钮的启动/跳转反馈（接到首页提示条上）。 */
  onLoginFeedback?: (message: string) => void
}) {
  const [loginBusy, setLoginBusy] = useState<PlatformId | null>(null)

  // v27：登录按钮承担拉起浏览器 + 跳转的职责。点一次就给明确反馈：
  // 正在启动 / 已打开 / 已切到打开的标签页 / 失败原因，不再静默。
  const handleOpenLogin = async (platform: PlatformId) => {
    if (loginBusy) {
      onLoginFeedback?.('正在打开工作浏览器，请稍等一下。')
      return
    }
    setLoginBusy(platform)
    onLoginFeedback?.(`正在打开工作浏览器，准备去${PLATFORM_LABELS[platform]}登录…`)
    try {
      const result = await openPlatformLogin(platform)
      if (result.ok) {
        const base = result.reusedTab
          ? `已经打开了${PLATFORM_LABELS[platform]}的标签页，直接切过去了。`
          : result.browserStarted
            ? `工作浏览器已启动，正在打开${PLATFORM_LABELS[platform]}登录页。`
            : `已在工作浏览器打开${PLATFORM_LABELS[platform]}登录页。`
        onLoginFeedback?.(`${base}${portMismatchNote(result)}`)
      } else {
        onLoginFeedback?.(result.message || '浏览器没连上，再点一次试试。')
      }
    } finally {
      setLoginBusy(null)
    }
  }

  return (
    <section aria-label="开始使用" className="animate-fade-up rounded-2xl border border-primary/30 bg-card p-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="mb-0.5 flex items-center gap-2">
            <span className="rounded-md bg-primary/15 px-2 py-0.5 text-[11px] font-bold text-primary">第一次用</span>
            <h2 className="text-lg font-black text-foreground">先花 2 分钟准备一下</h2>
          </div>
          <p className="text-sm text-muted">按提示填好简历和想找的工作，再点一下检查，确认能正常跑起来。只想先搜搜岗位，可以先不填简历。</p>
          <p className="mt-1 text-sm text-muted">第一次双击打开时，如果 Windows 弹出蓝色提示说“发布者未知”，点「仍要运行」就能继续用——程序还没签名，不是电脑出问题。</p>
        </div>
        <Button size="sm" onClick={() => window.dispatchEvent(new Event(START_ONBOARDING_EVENT))}>带我走一遍</Button>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-card-border pt-3">
        <span data-tour="chrome-guide" className="text-xs font-semibold text-primary">先在{browserName}里登录好招聘网站，再回来点检查。</span>
        <div className="flex flex-wrap gap-1.5" aria-label="去各平台登录">
          {PLATFORM_IDS.map(platform => (
            <button
              key={platform}
              type="button"
              onClick={() => { void handleOpenLogin(platform) }}
              disabled={loginBusy !== null}
              className="rounded-full border border-card-border bg-surface px-2.5 py-1 text-xs font-bold text-primary hover:border-primary/40 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {loginBusy === platform ? '正在打开…' : `去${PLATFORM_LABELS[platform]}登录`}
            </button>
          ))}
        </div>
        <p className="w-full text-xs text-muted">点击后会打开工作浏览器并跳转到对应招聘网站登录页。</p>
        {/* 引导区里的检查入口；漏斗非空后引导区消失，同一入口保留在
            “今天要做的事”标题栏（下面那份，data-tour 也在那一份上），
            新手引导最后一步总能找到它，老用户回首页也能直接点。 */}
        <Button data-tour="check-preflight" variant="secondary" size="sm" onClick={onCheck} disabled={checking}>
          <ShieldCheck className="mr-1.5 h-3.5 w-3.5" />
          {checking ? '检查中' : '检查一下能不能跑'}
        </Button>
      </div>
      {notice && <CompactNotice message={notice} danger={noticeDanger && checks.some(check => check.status !== 'pass')} />}
      {checks.some(check => check.status !== 'pass') && (
        <PreflightPanel checks={checks} checking={checking} onRetry={onRetry} expandSignal={expandSignal} />
      )}
    </section>
  )
}

export default function DashboardPage({ view = 'workbench' }: DashboardPageProps) {
  const {
    workbench,
    verification,
    resolveVerification,
    history,
    loading,
    error,
    refreshing,
    lastRefreshedAt,
    refresh,
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
  } = useDashboard(view)
  const [selected, setSelected] = useState<string[]>([])
  const [notice, setNotice] = useState('')
  /** 预检失败的提示用红字醒目；只在确实有未通过项时才红。 */
  const [noticeDanger, setNoticeDanger] = useState(false)
  /** 每次预检失败就 +1，传给检查面板让它自动展开。 */
  const [preflightFailSignal, setPreflightFailSignal] = useState(0)
  /** 停下超过 10 秒还没停住时，才出现“强制停止”。 */
  const [forceStopVisible, setForceStopVisible] = useState(false)
  const [forceStopping, setForceStopping] = useState(false)
  const update = useUpdateContext()
  const auth = useAuth()
  const { config } = useConfig()
  const [updateNote, setUpdateNote] = useState('')
  /** 手动检查没查到时才 true：提示旁边出现“再试一次”按钮。 */
  const [updateCheckFailed, setUpdateCheckFailed] = useState(false)
  const [manualUpdateSignal, setManualUpdateSignal] = useState(0)
  // 更新弹窗开着时收起顶部横幅：同一件事只出现一块（v1.0.8 上两块
  // 同时出现，用户分不清该点哪个）。
  const [updateModalOpen, setUpdateModalOpen] = useState(false)
  const handleManualUpdateCheck = useCallback(async () => {
    setUpdateNote('')
    setUpdateCheckFailed(false)
    const found = await update.check(true)
    if (found === null) {
      // 没查到（不是“已是最新”）：留文字 + 再试一次按钮。同一路连点
      // 共用同一个检查请求，后返回的不会把这条提示盖掉。
      setUpdateNote('没查到，点再试一次。')
      setUpdateCheckFailed(true)
    } else if (found === false) {
      setUpdateNote('已经是最新版，不用换。')
    } else {
      // 有新版：让弹窗弹出来（手动检查忽略跳过记录）。
      setManualUpdateSignal(Date.now())
    }
  }, [update])

  // 侧边栏点版本号：同样走这里的手动检查。
  useEffect(() => {
    const onCheckUpdate = () => {
      void handleManualUpdateCheck()
    }
    window.addEventListener(CHECK_UPDATE_EVENT, onCheckUpdate)
    return () => window.removeEventListener(CHECK_UPDATE_EVENT, onCheckUpdate)
  }, [handleManualUpdateCheck])

  // Dashboard visualization state
  const [activityData, setActivityData] = useState<ActivityData[]>([])
  const [topCompanies, setTopCompanies] = useState<TopCompany[]>([])
  const [recentActivity, setRecentActivity] = useState<HistoryItem[]>([])
  const [preflightChecks, setPreflightChecks] = useState<PreflightCheck[]>([])
  const [preflightMode, setPreflightMode] = useState<WorkbenchMode>('full')
  const [selectedJob, setSelectedJob] = useState<Job | null>(null)
  const [modePending, setModePending] = useState<WorkbenchMode | null>(null)
  const [sendingGreetingIds, setSendingGreetingIds] = useState<Set<string>>(new Set())
  const [busyGreetingIds, setBusyGreetingIds] = useState<Set<string>>(new Set())
  const onGreetingBusyChange = useCallback((id: string, busy: boolean) => {
    setBusyGreetingIds(previous => {
      const next = new Set(previous)
      if (busy) next.add(id)
      else next.delete(id)
      return next
    })
  }, [])
  const [confirmedDeliveryIds, setConfirmedDeliveryIds] = useState<Set<string>>(new Set())
  const [todayFilters, setTodayFilters] = useState<JobFilters>({ ...EMPTY_JOB_FILTERS })
  const [statsScope, setStatsScope] = useState<StatsScope>('today')
  const [collectDialogOpen, setCollectDialogOpen] = useState(false)
  const [collectDialogMode, setCollectDialogMode] = useState<'collect' | 'full'>('collect')
  const [preflightRunning, setPreflightRunning] = useState(false)
  const [generatingGreetings, setGeneratingGreetings] = useState(false)
  const [browserDiag, setBrowserDiag] = useState<BrowserDiagnostics | null>(null)
  const [browserDiagLoading, setBrowserDiagLoading] = useState(false)
  const [preflightSeeded, setPreflightSeeded] = useState(false)

  // 浏览器诊断：后端没提供该接口时静默，不挡首页。诊断本身可能很慢
  // （runtime 没起来时服务端要等几秒），10 秒没回来就当没有，不占着
  // 连接一直等——首页内容不依赖它。
  // 挂载时顺带用这份诊断推导一次登录态：库里的行可能是上次启动留
  // 下的旧值（登录态表重启后仍在），不先拿新鲜诊断对一遍，卡片就
  // 会按旧值显示“已掉线”。浏览器没连上时推导不出东西，旧值不动。
  const fetchBrowserDiagnostics = useCallback(async () => {
    const controller = new AbortController()
    const timer = window.setTimeout(() => controller.abort(), 10000)
    setBrowserDiagLoading(true)
    try {
      const res = await fetch('/api/browser/diagnostics', { cache: 'no-store', signal: controller.signal })
      if (!res.ok) return
      const data = await res.json()
      // 只认契约里的形状；后端没提供该接口、返回空对象时不误报。
      if (typeof data.ok === 'boolean' || data.error_kind) {
        setBrowserDiag(data)
        applyPlatformLoginStates(derivePlatformLoginStates(data))
      }
    } catch {
      // 忽略：诊断只是辅助信息。
    } finally {
      window.clearTimeout(timer)
      setBrowserDiagLoading(false)
    }
  }, [applyPlatformLoginStates])
  useEffect(() => { void fetchBrowserDiagnostics() }, [fetchBrowserDiagnostics])

  // 卡片右上角“刷新”：只刷登录态（POST 诊断 + 前端推导），不整页
  // 重载；诊断原样同步给下面的浏览器诊断面板，两处不各说各话。
  const handleRefreshPlatformLogin = useCallback(async () => {
    const data = await refreshPlatformLogin()
    if (data && typeof data === 'object' && (typeof (data as Record<string, unknown>).ok === 'boolean' || (data as Record<string, unknown>).error_kind)) {
      setBrowserDiag(data as BrowserDiagnostics)
    }
  }, [refreshPlatformLogin])

  // 首页 preflight 结果优先用后端保存的最近一次结果：路由切换回来
  // 不再显示“未检查过”，本地刚跑过的结果优先。
  useEffect(() => {
    if (preflightSeeded) return
    const last = workbench.last_preflight
    if (last?.checks?.length && preflightChecks.length === 0) {
      setPreflightChecks(last.checks)
      setPreflightSeeded(true)
    }
  }, [workbench.last_preflight, preflightChecks.length, preflightSeeded])
  const startedGreetTaskIdRef = useRef<string | null>(null)

  const todayJobs = useMemo(
    () => workbench.pending_confirmation.filter(job => !confirmedDeliveryIds.has(job.id)),
    [workbench.pending_confirmation, confirmedDeliveryIds]
  )
  const todayCityOptions = useMemo(
    () => [...new Set(todayJobs.map(job => jobCityBaseName(job.city)).filter(Boolean))].sort(),
    [todayJobs]
  )
  const debouncedTodayQuery = useDebouncedValue(todayFilters.query, 250)
  const activeTodayFilterCount = Object.values(todayFilters).filter(value => Array.isArray(value) ? value.length > 0 : value !== '').length
  const effectiveTodayFilters = useMemo(
    () => ({ ...todayFilters, query: debouncedTodayQuery }),
    [todayFilters, debouncedTodayQuery]
  )
  const filteredTodayJobs = useMemo(
    () => filterJobs(todayJobs, effectiveTodayFilters),
    [todayJobs, effectiveTodayFilters]
  )
  const visibleJobIds = useMemo(() => new Set(filteredTodayJobs.map(job => job.id)), [filteredTodayJobs])
  const actionableSelected = useMemo(() => selected.filter(id => visibleJobIds.has(id)), [selected, visibleJobIds])

  useEffect(() => {
    setSelected(previous => {
      const next = previous.filter(id => visibleJobIds.has(id))
      return next.length === previous.length ? previous : next
    })
  }, [visibleJobIds])

  useEffect(() => {
    const handleConfigSaved = () => { void refresh() }
    window.addEventListener('jianli-lieshou-config-saved', handleConfigSaved)
    return () => window.removeEventListener('jianli-lieshou-config-saved', handleConfigSaved)
  }, [refresh])

  // Keep the homepage overview separate from the monitor's conversation history.
  useEffect(() => {
    if (view !== 'workbench') return
    const controller = new AbortController()
    const fetchOverview = async <T,>(url: string, update: (data: T) => void) => {
      const response = await fetch(url, { cache: 'no-store', signal: controller.signal })
      if (!response.ok) return
      const data: T = await response.json()
      if (!controller.signal.aborted) update(data)
    }
    const fetchVisualizationData = async () => {
      // One unavailable chart must not prevent the other overview cards loading.
      await Promise.allSettled([
        fetchOverview('/api/activity?days=7', setActivityData),
        fetchOverview('/api/top-companies?limit=5', setTopCompanies),
        fetchOverview('/api/history?limit=3', setRecentActivity),
      ])
    }
    void fetchVisualizationData()
    const interval = window.setInterval(fetchVisualizationData, 60000)
    return () => {
      controller.abort()
      window.clearInterval(interval)
    }
  }, [view])

  const pendingGreetingJobs = workbench.pending_greetings
  const reviewedGreetingJobs = pendingGreetingJobs.filter(job => job.greeting_selection !== 'pending')
  const pendingGreetingReviewCount = pendingGreetingJobs.length - reviewedGreetingJobs.length
  const activeTask = workbench.task
  const visibleTask = activeTask || workbench.last_task
  // 面板文案里的浏览器名跟着“我的设置”里的选择走：没拿到诊断结果时
  // 用通用名“浏览器”，不凭空写死 Chrome。
  const browserName = browserDiag?.selected_browser_name || '浏览器'
  const visibleTaskError = visibleTask?.error ? taskErrorFeedback(visibleTask.error, browserName) : null
  const fullStage = visibleTask ? currentTaskStage(visibleTask) : ''
  const hasStageDetails = fullStage.trim().includes('\n')
  const detailMetrics = taskMetricItems.filter(item => {
    const metrics = visibleTask?.metrics || {}
    if (!(item.key in metrics)) return false
    if (item.key.startsWith('collect_') && visibleTask?.progress?.platforms) return false
    if (['collect_seen', 'collect_new', 'ai_passed', 'send_success', 'greet_generated'].includes(item.key)) return false
    return !(item.key.endsWith('_failed') && Number(metrics[item.key]) > 0)
  })
  const greetTaskRunning = activeTask != null && activeTask.mode === 'greet'
    && (activeTask.status === 'running' || activeTask.status === 'stopping')

  // v28：进入“正在停下”开始计时，10 秒还没停下就给出“强制停止”；
  // 停住了或任务没了就把强制按钮收起来。
  useEffect(() => {
    if (activeTask?.status !== 'stopping') {
      setForceStopVisible(false)
      return
    }
    const timer = window.setTimeout(() => setForceStopVisible(true), 10000)
    return () => window.clearTimeout(timer)
  }, [activeTask?.status])

  useEffect(() => {
    const startedId = startedGreetTaskIdRef.current
    if (!startedId) return
    const greetTask = activeTask?.id === startedId
      ? activeTask
      : workbench.last_task?.id === startedId ? workbench.last_task : null
    if (!greetTask || !TERMINAL_TASK_STATUSES.includes(greetTask.status)) return
    startedGreetTaskIdRef.current = null
    if (greetTask.status === 'completed') {
      setNotice(describeGreetTaskOutcome(greetTask))
    }
    void refresh()
  }, [activeTask, workbench.last_task, refresh])

  const taskSummary = visibleTask
    ? visibleTaskError?.title || taskStopReasonLabel(visibleTask.stop_reason) || currentTaskStage(visibleTask)
    : ''
  const pendingReplies = history.filter(item => item.action === 'reply_pending')

  const toggleJob = (id: string) => {
    setSelected(prev => (prev.includes(id) ? prev.filter(item => item !== id) : [...prev, id]))
  }

  const runPreflight = async (mode: WorkbenchMode, options?: Record<string, unknown>) => {
    setPreflightMode(mode)
    let res: Response
    try {
      // v36：以前是裸 fetch，没有任何超时——后端一旦卡住（冷启动诊断、
      // 响应丢了），点“重新采集”就永远转圈、按钮再也点不了。现在走
      // fetchWithRetry 单次超时，超时抛可读错误，调用方恢复按钮并显示原因。
      res = options
        ? await fetchWithRetry('/api/workbench/preflight', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ mode, options }),
        }, { timeoutMs: PREFLIGHT_TIMEOUT_MS, retries: 0 })
        : await fetchWithRetry(`/api/workbench/preflight?mode=${mode}`, {}, { timeoutMs: PREFLIGHT_TIMEOUT_MS, retries: 0 })
    } catch (err) {
      if (isAbortError(err)) throw new Error(PREFLIGHT_TIMEOUT_MESSAGE)
      throw err
    }
    const data = await withTimeout(parsePreflightResponse(res), PREFLIGHT_BODY_TIMEOUT_MS, '读取预检结果超时了，稍后再点一次。')
    setPreflightChecks(data.checks)
    if (!data.ok) {
      // v28：失败不清空已有提示（保留上一次的有效信息），只把提示条
      // 标红；同时自动展开下面的检查面板并滚过去看（弹窗开着时不滚，
      // 失败原因已经写进弹窗里了）。
      setNoticeDanger(true)
      setPreflightFailSignal(signal => signal + 1)
      if (!collectDialogOpen) {
        window.setTimeout(() => {
          const panel = document.querySelector('[aria-label="启动检查结果"]')
          if (panel && typeof (panel as HTMLElement).scrollIntoView === 'function') {
            ;(panel as HTMLElement).scrollIntoView({ behavior: 'smooth', block: 'nearest' })
          }
        }, 50)
      }
      return { ok: false as const, checks: data.checks }
    }
    setNoticeDanger(false)
    return { ok: true as const, checks: data.checks }
  }

  /** 预检里“未发现已打开的页面”且登录态是已登录的平台：可以自动开页补上。 */
  const missingLoggedInPlatforms = (checks: PreflightCheck[]): PlatformId[] => {
    const login = workbench.platform_login
    return (Object.keys(PLATFORM_TAB_CHECK_IDS) as Array<keyof typeof PLATFORM_TAB_CHECK_IDS>).filter(key => {
      const platform = PLATFORM_TAB_CHECK_IDS[key]
      const check = checks.find(item => item.id === key)
      return Boolean(
        check
        && check.status !== 'pass'
        && check.message.includes('未发现已打开')
        && login?.[platform]?.status === 'logged_in',
      )
    }).map(key => PLATFORM_TAB_CHECK_IDS[key])
  }

  /** 给缺页的平台自动开一页（走工作浏览器首页），返回没打开的平台。 */
  const openMissingPlatformPages = async (checks: PreflightCheck[]): Promise<PlatformId[]> => {
    const missing = missingLoggedInPlatforms(checks)
    const failed: PlatformId[] = []
    for (const platform of missing) {
      const result = await openPlatformPage(platform)
      if (!result.ok) failed.push(platform)
    }
    return failed
  }

  const openFailSuffix = (failed: PlatformId[]): string =>
    failed.length
      ? `${failed.map(platform => PLATFORM_LABELS[platform]).join('、')}页面没自动打开，开始任务时会再打开。`
      : ''

  /** 启动流程的预检：通过后把“已登录却缺页”的平台自动开页再重查一次；
   * 拦住的若只是这类缺页，也先开页再重查一次；重查还不过才算真没通过。
   * 返回 ok 与没自动打开的平台（启动提示里要如实告诉用户）。
   * 注意：后端已把“已登录却缺页”降级为提醒时，失败兜底那条路不会走到；
   * 没降级时兜底保证同一版行为一致——最终是否放行仍由重查的后端定。 */
  const runPreflightWithAutoOpen = async (mode: WorkbenchMode, options?: Record<string, unknown>): Promise<{ ok: boolean; checks: PreflightCheck[]; openFailed: PlatformId[] }> => {
    const first = await runPreflight(mode, options)
    if (!first.ok) {
      const blockers = first.checks.filter(check => check.status === 'error')
      const missing = missingLoggedInPlatforms(first.checks)
      const onlyMissingPages = blockers.length > 0
        && missing.length > 0
        && blockers.every(check => {
          const platform = PLATFORM_TAB_CHECK_IDS[check.id]
          return platform !== undefined && missing.includes(platform)
        })
      if (!onlyMissingPages) return { ok: false, checks: first.checks, openFailed: [] }
      const openFailed = await openMissingPlatformPages(first.checks)
      const second = await runPreflight(mode, options)
      return { ok: second.ok, checks: second.checks, openFailed }
    }
    const missing = missingLoggedInPlatforms(first.checks)
    if (missing.length === 0) return { ok: true, checks: first.checks, openFailed: [] }
    const openFailed = await openMissingPlatformPages(first.checks)
    const second = await runPreflight(mode, options)
    return { ok: second.ok, checks: second.checks, openFailed }
  }

  /** 返回是否真的启动了：投递跟进页的“再查一次 HR 回复”复用同一入口，
   * 靠返回值在跟进页给反馈（首页的 notice 在跟进页看不到）。 */
  const handleModeClick = async (mode: WorkbenchMode): Promise<boolean> => {
    try {
      if (activeTask?.mode === mode) {
        if (activeTask.status === 'stopping') {
          setNotice(`正在停下${activeTask.label}，请稍等一下。`)
          return false
        }
        if (window.confirm(`是否停止当前${activeTask.label}任务？已入库岗位会保留。`)) {
          setModePending(mode)
          setNotice(`正在停下${activeTask.label}…`)
          await stopTask(activeTask.id)
          setNotice(`${activeTask.label}已请求停止。`)
        }
        return false
      }
      if (modePending) {
        setNotice('正在处理上一个操作，请稍等一下。')
        return false
      }
      if (activeTask) {
        setNotice(
          activeTask.status === 'stopping'
            ? `当前${activeTask.label}正在停止，请等待后台完全结束后再启动其他模式。`
            : `当前正在运行${activeTask.label}，请先点击正在运行的卡片停止后再启动其他模式。`
        )
        return false
      }
      if (mode === 'full') {
        setCollectDialogMode('full')
        setCollectDialogOpen(true)
        return false
      }
      const target = modes.find(item => item.mode === mode)
      setModePending(mode)
      setNotice(`${target?.title || '任务'}启动前预检中...`)
      const preflight = await runPreflightWithAutoOpen(mode)
      if (!preflight.ok) {
        setNotice(`${preflightFailureNotice(preflight.checks)}${openFailSuffix(preflight.openFailed)}`)
        return false
      }
      setNotice(`${target?.title || '任务'}启动中，请稍候...`)
      await startTask(mode)
      const suffix = openFailSuffix(preflight.openFailed)
      setNotice(`${target?.title || '任务'}已启动，日志会在下方更新。${suffix}`)
      return true
    } catch (err) {
      setNotice(startFailureMessage(err))
      return false
    } finally {
      setModePending(null)
    }
  }

  /** 投递跟进页“再查一次 HR 回复”的反馈：正在查就别重复点，别的事
   * 在跑就直说，没启动成功就指去首页看原因。 */
  const checkRepliesFromMonitor = async (): Promise<string> => {
    if (activeTask?.mode === 'monitor' && (activeTask.status === 'running' || activeTask.status === 'stopping')) {
      return '正在查 HR 回复，等它查完就行，查到会出现在上面「等你处理」里。'
    }
    if (activeTask) {
      return `现在正忙着 ${activeTask.label}，先停下再查。`
    }
    const started = await handleModeClick('monitor')
    return started
      ? '已经开始查 HR 回复了，查到会出现在上面「等你处理」里。'
      : '没查成，原因写在首页的提示里，去首页看看。'
  }

  const retryPreflight = async () => {
    if (modePending) {
      setNotice('正在检查中，请稍等一下。')
      return
    }
    try {
      setModePending(preflightMode)
      setNotice('正在重新检查运行环境...')
      const result = await runPreflight(preflightMode)
      setNotice(result.ok ? '检查通过，可以开始任务。' : preflightFailureNotice(result.checks))
    } catch (err) {
      setNotice(err instanceof Error && err.message === PREFLIGHT_TIMEOUT_MESSAGE ? err.message : '重新检查失败，请确认简历猎手还在运行。')
    } finally {
      setModePending(null)
    }
  }

  const runStandalonePreflight = async () => {
    if (modePending || preflightRunning) {
      setNotice('正在检查中，请稍等一下。')
      return
    }
    try {
      setPreflightRunning(true)
      setNotice('正在检查全流程运行环境...')
      const result = await runPreflight('full')
      setNotice(result.ok ? '全流程预检通过，可以开始任务。' : preflightFailureNotice(result.checks))
    } catch (err) {
      setNotice(err instanceof Error && err.message === PREFLIGHT_TIMEOUT_MESSAGE ? err.message : '检查没通过，请确认简历猎手还在运行。')
    } finally {
      setPreflightRunning(false)
    }
  }

  const startCollection = async (options: Record<string, unknown>): Promise<{ ok: boolean; error?: string }> => {
    const mode = collectDialogMode
    setModePending(mode)
    setNotice(mode === 'full' ? '全流程启动前预检中...' : '岗位采集启动前预检中...')
    try {
      const preflight = await runPreflightWithAutoOpen(mode, options)
      if (!preflight.ok) {
        const message = `${preflightFailureNotice(preflight.checks)}${openFailSuffix(preflight.openFailed)}`
        setNotice(message)
        return { ok: false, error: `${message}请关闭弹窗后按检查提示修复，再重新启动。` }
      }
      setNotice('启动前预检通过，正在启动任务...')
      await startTask(mode, options)
      const suffix = openFailSuffix(preflight.openFailed)
      setNotice(mode === 'full' ? `全流程已启动，进度会在下方更新。${suffix}` : `岗位采集已启动，进度会在下方更新。${suffix}`)
      return { ok: true }
    } catch (err) {
      const message = startFailureMessage(err)
      setNotice(message)
      return { ok: false, error: message }
    } finally {
      setModePending(null)
    }
  }

  // v27：风控停轮后的“立即重试”——先清掉本轮的账号风险锁，再直接重
  // 开一轮岗位采集。跳过启动前预检是故意的：这轮刚刚还在跑，环境没
  // 变，唯一挡着的就是锁；真有登录失效，采集里会再次判出来并重新上锁。
  const retryCollectionAfterRisk = async () => {
    if (modePending) {
      setNotice('正在处理上一个操作，请稍等一下。')
      return
    }
    try {
      setModePending('collect')
      setNotice('正在解除冷却，马上重新开始找岗位…')
      const res = await fetch('/api/safety/clear-risk-lock', { method: 'POST' })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.error || '解除冷却失败')
      }
      await startTask('collect')
      setNotice('已重新开始找岗位，进度会在下方更新。')
    } catch (err) {
      setNotice(err instanceof Error ? err.message : '重试失败')
    } finally {
      setModePending(null)
    }
  }

  /** v28：停下超过 10 秒还没停住时才出现的强制停止。按约定的
   * POST /api/workbench/force-stop 写（后端由总协调接线）：成功返回
   * {"ok": true, "status": "stopped"}，失败返回 ok:false + message。
   * 强制停下后马上点启动可能报“浏览器正忙”，那是后端锁的正常保护，
   * 启动失败提示里会如实写“等几秒再点启动”，不要写成错误。 */
  const forceStopTask = async () => {
    if (!activeTask || forceStopping) return
    setForceStopping(true)
    try {
      const res = await fetch('/api/workbench/force-stop', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task_id: activeTask.id }),
      })
      const data = await res.json().catch(() => ({})) as { ok?: boolean; status?: string; message?: string; error?: string }
      if (res.ok && data.ok) {
        setNotice(`${activeTask.label}已经停下了。`)
        await refresh()
      } else {
        setNotice(data.message || data.error || '强制停止没成功，再等一下或重启程序试试。')
      }
    } catch {
      setNotice('强制停止没发出去，请确认简历猎手还在运行。')
    } finally {
      setForceStopping(false)
    }
  }

  /** 投递跟进卡片上的暂停 / 继续 / 停下：与首页任务面板同一套接口，
   * 只是入口放在卡片上，用户不用翻回首页找按钮。 */
  const pauseDeliveryTask = async (taskId: string) => {
    try {
      await pauseTask(taskId)
      setNotice('已经暂停了，点继续接着投。')
    } catch (err) {
      setNotice(err instanceof Error ? `没暂停：${err.message}` : '没暂停，请稍后再试。')
    }
  }

  const resumeDeliveryTask = async (taskId: string) => {
    try {
      await resumeTask(taskId)
      setNotice('已经继续投了。')
    } catch (err) {
      setNotice(err instanceof Error ? `没继续：${err.message}` : '没继续，请稍后再试。')
    }
  }

  const stopDeliveryTask = async (taskId: string) => {
    try {
      await stopTask(taskId)
      setNotice('已经让它停下了。')
    } catch (err) {
      setNotice(err instanceof Error ? `没停下来：${err.message}` : '没停下来，请稍后再试。')
    }
  }

  /** v54 图10：跳过当前等待——只跳这一轮等待，不停任务。点完后端
   * 清掉倒计时，下一轮等待开始后重新下发新的倒计时。 */
  const [skipWaitBusyId, setSkipWaitBusyId] = useState<string | null>(null)
  const skipWait = async (taskId: string) => {
    setSkipWaitBusyId(taskId)
    try {
      const res = await fetch(`/api/workbench/task/${encodeURIComponent(taskId)}/skip-wait`, { method: 'POST' })
      if (!res.ok) {
        setNotice('跳过没发出去，等倒计时自己走完吧。')
        return
      }
      setNotice('已经跳过这次等待，继续投下一个岗位。')
      await refresh()
    } catch {
      setNotice('跳过没发出去，等倒计时自己走完吧。')
    } finally {
      setSkipWaitBusyId(current => (current === taskId ? null : current))
    }
  }

  const confirmDeliver = async (ids: string[]) => {
    if (!ids.length) return
    const count = ids.length
    if (!window.confirm(`是否投递以下 ${count} 个岗位？确认后将进入投递/打招呼流程。`)) return
    try {
      const res = await fetch('/api/workbench/deliver', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_ids: ids }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.error || '投递失败')
      }
      const data = await res.json().catch(() => ({}))
      if (!ids.some(id => workbench.send_errors.some(job => job.id === id))) {
        setConfirmedDeliveryIds(prev => new Set([...prev, ...ids]))
      }
      await refresh()
      setNotice(
        data.already_queued_count === count
          ? `选中的 ${count} 个已经在投递队列里了，投出去后会出现在投递跟进里。`
          : data.queued_count
            ? `已经把 ${data.queued_count} 个加进投递队列了，投出去后会出现在投递跟进里。`
            : `已经确认投 ${count} 个岗位，后端会按队列推进，投出去后会出现在投递跟进里。`
      )
      setSelected(prev => prev.filter(id => !new Set(ids).has(id)))
    } catch (err) {
      setNotice(err instanceof Error ? err.message : '投递失败')
    }
  }

  const rejectSelectedJobs = async (ids: string[]) => {
    if (!ids.length) return
    const count = ids.length
    if (!window.confirm(`确定不要这 ${count} 个岗位了吗？不要了就不会投了，可以在找工作里看到它被标成“不要了”。`)) return
    try {
      const res = await fetch('/api/workbench/reject', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_ids: ids }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.error || '放弃失败')
      }
      const rejectedIds = new Set(ids)
      setSelected(prev => prev.filter(id => !rejectedIds.has(id)))
      setConfirmedDeliveryIds(prev => new Set([...prev, ...ids]))
      await refresh()
      setNotice(`已放弃 ${count} 个岗位。`)
    } catch (err) {
      setNotice(err instanceof Error ? err.message : '放弃失败')
    }
  }

  const generateGreetings = async (ids: string[]) => {
    if (!ids.length) return
    setGeneratingGreetings(true)
    try {
      const res = await fetch('/api/workbench/greetings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_ids: ids }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        throw new Error(data.error || '写打招呼失败')
      }
      startedGreetTaskIdRef.current = data.task?.id ?? null
      await refresh()
      setNotice(`已经开始给 ${ids.length} 个岗位写打招呼了，可以在任务面板看进度，随时能停。`)
    } catch (err) {
      setNotice(err instanceof Error ? err.message : '写打招呼失败')
    } finally {
      setGeneratingGreetings(false)
    }
  }

  const sendReadyGreetings = async (ids: string[]) => {
    if (!ids.length || ids.some(id => sendingGreetingIds.has(id) || busyGreetingIds.has(id) || pendingGreetingJobs.find(job => job.id === id)?.greeting_activity)) return
    const count = ids.length
    // 直接发送是手动批量操作，发送前请确认，防止误触。
    if (!window.confirm(`确定要给选中的 ${count} 个岗位发打招呼吗？\n发了就马上开始投，还受每天数量的限制。`)) return
    setSendingGreetingIds(prev => new Set([...prev, ...ids]))
    setNotice(`正在将 ${count} 个岗位加入发送队列...`)
    try {
      const res = await fetch('/api/workbench/deliver', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_ids: ids, direct_send: true }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.error || '没发出去')
      }
      const data = await res.json().catch(() => ({}))
      await refresh()
      setNotice(
        data.already_queued_count === count
          ? `所选 ${count} 个岗位已在当前发送队列中，请等待依次发送。`
          : data.queued_count
            ? `已经把 ${data.queued_count} 个岗位追加到当前发送队列。`
            : `已直接进入发送流程 ${count} 个岗位。`
      )
    } catch (err) {
      setNotice(err instanceof Error ? err.message : '没发出去')
    } finally {
      setSendingGreetingIds(prev => new Set([...prev].filter(id => !ids.includes(id))))
    }
  }

  const selectGreeting = async (
    job: Job,
    selection: 'original' | 'optimized' | 'edited',
    greeting = '',
  ) => {
    const res = await fetch(`/api/jobs/${job.id}/greeting-selection`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ selection, greeting, confirmed: true }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.error || '没保存上你选的打招呼')
    updateGreetingJob(data)
    await refresh()
    setNotice(
      selection === 'original'
        ? '就用原来的，后续生成不会覆盖。'
        : selection === 'optimized'
          ? '已经用了改完的版本，后续生成不会覆盖。'
          : '已经保存了你改的版本，后续生成不会覆盖。'
    )
  }

  const openJobDetail = async (job: Job) => {
    try {
      const res = await fetch(`/api/jobs/${job.id}`)
      if (!res.ok) throw new Error('读取岗位详情失败')
      setSelectedJob(await res.json())
    } catch (err) {
      setNotice(err instanceof Error ? err.message : '读取岗位详情失败')
    }
  }

  const downloadResume = (job: Job) => {
    window.open(`/api/jobs/${job.id}/resume/download`, '_blank')
  }

  const markResumeSent = async (job: Job) => {
    try {
      const res = await fetch(`/api/jobs/${job.id}/mark-resume-sent`, { method: 'POST' })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.error || '标记失败')
      }
      await refresh()
      setNotice(`已标记 ${job.company}｜${job.title} 的定制简历投过了。`)
    } catch (err) {
      setNotice(err instanceof Error ? err.message : '标记失败')
    }
  }

  if (loading) {
    return (
      <div className="space-y-4" aria-label="正在打开">
        <div className="skeleton h-28 w-full" />
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
          {[0, 1, 2, 3, 4].map(i => <div key={i} className="skeleton h-20" />)}
        </div>
        <div className="skeleton h-64 w-full" />
      </div>
    )
  }

  if (view === 'jobs') {
    return <JobsPoolView updateJobStatus={updateJobStatus} />
  }

  if (view === 'monitor') {
    return (
      <MonitorExecutionView
        history={history}
        refresh={refresh}
        refreshing={refreshing}
        lastRefreshedAt={lastRefreshedAt}
        onCheckReplies={checkRepliesFromMonitor}
        tasks={workbench.tasks ?? []}
        deliveryJobs={workbench.delivery_jobs ?? []}
        onPauseTask={(taskId) => { void pauseDeliveryTask(taskId) }}
        onResumeTask={(taskId) => { void resumeDeliveryTask(taskId) }}
        onStopTask={(taskId) => { void stopDeliveryTask(taskId) }}
        onSkipWaitTask={(taskId) => { void skipWait(taskId) }}
        skipWaitBusyId={skipWaitBusyId}
        onForceStopTask={() => { void forceStopTask() }}
        forceStopVisible={forceStopVisible}
      />
    )
  }

  const showGettingStarted = !Object.values(workbench.funnel).some(count => count > 0)

  return (
    <div className="space-y-4">
      <AuthStatusBar auth={auth} />
      {!updateModalOpen && <UpdateBanner update={update} />}
      <UpdateModal update={update} manualCheckSignal={manualUpdateSignal} onOpenChange={setUpdateModalOpen} />
      {showGettingStarted && (
        <GettingStartedCard
          onCheck={runStandalonePreflight}
          checking={preflightRunning || Boolean(modePending)}
          notice={notice}
          noticeDanger={noticeDanger}
          checks={preflightChecks}
          onRetry={retryPreflight}
          expandSignal={preflightFailSignal}
          browserName={browserName}
          onLoginFeedback={setNotice}
        />
      )}
      <div id="platform-login-card" className="scroll-mt-6">
      <PlatformStatusCard
        platformLogin={workbench.platform_login}
        browserName={browserName}
        onRefresh={() => { void handleRefreshPlatformLogin() }}
        refreshing={platformLoginRefreshing}
        refreshedAt={platformLoginRefreshedAt}
        refreshError={platformLoginRefreshError || null}
        checking={browserDiagLoading || platformLoginRefreshing}
        taskActive={activeTask != null}
      />
      </div>
      <BrowserDiagnosticsPanel diagnostics={browserDiag} onRefresh={fetchBrowserDiagnostics} />
      <section id="today-workbench" className="animate-fade-up scroll-mt-6 rounded-2xl border border-card-border bg-card p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
          <h2 className="text-lg font-bold tracking-tight">今天要做的事</h2>
          <div className="flex items-center gap-2 text-xs text-muted">
            <span className="mr-1 hidden sm:inline">{activeTask ? `${activeTask.label}中` : '现在没事做'}</span>
            {/* 常驻检查入口：引导区只在漏斗为空时显示，这一份一直都在，
                和引导区里的是同一个动作（runStandalonePreflight），不另起流程；
                新手引导最后一步的 data-tour 锚点也在这一份上。 */}
            <Button data-tour="check-preflight" variant="secondary" size="sm" className="h-7 px-1" onClick={runStandalonePreflight} disabled={refreshing || Boolean(modePending) || preflightRunning}>
              <ShieldCheck className={cn('mr-1 h-3.5 w-3.5', preflightRunning && 'animate-spin')} />
              {preflightRunning ? '检查中' : '检查一下能不能跑'}
            </Button>
            <Button variant="ghost" size="sm" className="h-7 px-1" onClick={refresh} disabled={refreshing} title={lastRefreshedAt ? `最后刷新：${lastRefreshedAt.toLocaleTimeString('zh-CN', { hour12: false })}` : undefined}>
              <RefreshCw className={cn('mr-1 h-3.5 w-3.5', refreshing && 'animate-spin')} />
              {refreshing ? '刷新中' : '刷新一下'}
            </Button>
            <Button variant="ghost" size="sm" className="h-7 px-1" onClick={() => void handleManualUpdateCheck()} disabled={update.checking} title={update.currentVersion ? `现在是 ${update.currentVersion} 版` : undefined}>
              <RefreshCw className={cn('mr-1 h-3.5 w-3.5', update.checking && 'animate-spin')} />
              {update.checking ? '正在看有没有新版…' : '检查更新'}
            </Button>
            {updateNote ? <span className="text-xs text-muted">{updateNote}</span> : null}
            {updateCheckFailed ? (
              <Button variant="ghost" size="sm" className="h-7 px-1" onClick={() => void handleManualUpdateCheck()} disabled={update.checking}>
                再试一次
              </Button>
            ) : null}
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
          {modes.map(item => {
            const isActive = activeTask?.mode === item.mode
            const disabled = Boolean(activeTask && !isActive)
            return (
              <button
                key={item.mode}
                onClick={() => {
                  if (isActive) {
                    void handleModeClick(item.mode)
                    return
                  }
                  if (disabled) {
                    setNotice(`现在正忙着 ${activeTask?.label || '别的任务'}，先停下再开始找新岗位。`)
                    return
                  }
                  if (item.mode === 'collect' || item.mode === 'full') {
                    setCollectDialogMode(item.mode)
                    setCollectDialogOpen(true)
                  }
                  else void handleModeClick(item.mode)
                }}
                aria-disabled={disabled}
                className={cn(
                  'hover-lift min-w-0 rounded-xl border p-4 text-left transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary md:min-h-32 md:p-5',
                  item.mode === 'full' ? 'col-span-2 min-h-28 md:col-span-1' : 'min-h-24',
                  isActive
                    ? 'border-primary bg-primary text-primary-foreground'
                    : disabled
                      ? 'cursor-not-allowed border-card-border bg-card text-muted opacity-45'
                      : 'border-card-border bg-surface text-foreground hover:border-primary/60 hover:shadow-md'
                )}
              >
                <div className="mb-2 flex items-center justify-between gap-2">
                  <div className={cn('font-bold tracking-tight', item.mode === 'full' ? 'text-2xl' : 'text-xl')}>
                    {modePending === item.mode
                      ? isActive ? '正在停下' : '正在开始'
                      : isActive ? `${item.title}中` : item.title}
                  </div>
                  <span className="flex h-9 w-9 shrink-0 items-center justify-center">
                    {isActive ? <Square className="h-4 w-4 fill-current" /> : <Play className="h-5 w-5 fill-current" />}
                  </span>
                </div>
                <p className={`text-xs leading-5 ${isActive ? 'text-primary-foreground/85' : 'text-muted'}`}>{item.mode === 'full' && config?.scoring?.auto_deliver_ready === false ? '找岗、打分，一气呵成（直接投递已关，打完分不自动投）' : item.description}</p>
              </button>
            )
          })}
        </div>
        {!showGettingStarted && notice && <CompactNotice message={notice} danger={noticeDanger && preflightChecks.some(check => check.status !== 'pass')} />}
        {!showGettingStarted && preflightChecks.some(check => check.status !== 'pass') && (
          <PreflightPanel checks={preflightChecks} checking={Boolean(modePending) || preflightRunning} onRetry={retryPreflight} expandSignal={preflightFailSignal} />
        )}
        {error && <CompactNotice message={error} danger />}
        <CollectionSessionsPanel />
        {visibleTask && (
          <div className="mt-3 border-t border-card-border pt-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="text-sm font-semibold">{visibleTask.label}</div>
            <div className="flex flex-wrap items-center gap-2">
              {activeTask && (
                <>
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={activeTask.status !== 'running'}
                  onClick={() => {
                    if (!window.confirm(`要停下现在的${activeTask.label}任务吗？已经找到的岗位都留着。`)) return
                    setNotice(`正在停止${activeTask.label}...`)
                    void stopTask(activeTask.id)
                      .then(() => setNotice(`${activeTask.label}已经让它停下了。`))
                      .catch(err => setNotice(
                        err instanceof Error
                          ? `${activeTask.label}没停下来：${err.message}`
                          : `${activeTask.label}没停下来，请稍后再试。`
                      ))
                  }}
                >
                  {activeTask.status === 'stopping' ? '正在停下...' : '停下'}
                </Button>
                {activeTask.status === 'stopping' && forceStopVisible && (
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={forceStopping}
                    onClick={() => { void forceStopTask() }}
                    title="停下超过 10 秒还没停住时用；会直接结束任务，马上再点启动可能提示等几秒"
                  >
                    {forceStopping ? '正在强制停止…' : '强制停止'}
                  </Button>
                )}
                </>
              )}
            </div>
            </div>
            <div className={`mt-3 rounded-xl border px-3 py-2.5 ${taskStatusClass(visibleTask.status)}`}>
              <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs" aria-label="任务状态摘要">
                <div className="flex shrink-0 items-center gap-2">
                  <span className="font-semibold text-foreground">{taskStatusTitle(visibleTask.status)}</span>
                  <span className="text-muted">{taskStatusText(visibleTask.status)}</span>
                </div>
                <p className="min-w-0 flex-[1_1_12rem] truncate leading-5 text-foreground" title={currentTaskStage(visibleTask)}>{currentTaskStage(visibleTask).split('\n')[0]}</p>
                {visibleTask.metrics && (
                  <div className="flex max-w-full flex-wrap items-center gap-x-4 gap-y-1" aria-label="任务关键统计">
                    {taskMetricItems.filter(item => ['ai_passed', 'send_success', 'greet_generated'].includes(item.key) && item.key in visibleTask.metrics!).map(item => (
                      <span key={item.key} className="whitespace-nowrap text-muted">{item.label} <strong className="font-semibold tabular-nums text-foreground">{visibleTask.metrics?.[item.key]}</strong></span>
                    ))}
                    {taskMetricItems.filter(item => !item.key.startsWith('collect_') && item.key.endsWith('_failed') && Number(visibleTask.metrics?.[item.key]) > 0).map(item => (
                      <span key={item.key} className="whitespace-nowrap text-danger">{item.label} {visibleTask.metrics?.[item.key]}</span>
                    ))}
                  </div>
                )}
                {visibleTask.deadline_at && <span className="text-muted">截止 {new Date(visibleTask.deadline_at).toLocaleString('zh-CN', { hour12: false })}</span>}
              </div>
              <TaskPhaseProgress task={visibleTask} />
              {/* v54 图10：正在等待时在这里显示 MM:SS 倒计时 + 跳过等待。 */}
              {visibleTask.progress?.wait && (
                <div className="mt-3">
                  <WaitCountdown
                    wait={visibleTask.progress.wait}
                    onSkip={() => void skipWait(visibleTask.id)}
                    skipping={skipWaitBusyId === visibleTask.id}
                  />
                </div>
              )}
              {(hasStageDetails || detailMetrics.length > 0) && (
                <details className="group mt-2 border-t border-card-border/60 pt-2 text-xs">
                  <summary className="flex w-fit cursor-pointer list-none items-center gap-1 text-muted hover:text-foreground [&::-webkit-details-marker]:hidden">
                    <ChevronDown className="h-3.5 w-3.5 transition-transform group-open:rotate-180" />看看详情
                  </summary>
                  {hasStageDetails && <p className="mt-2 whitespace-pre-line break-words leading-5 text-muted">{fullStage}</p>}
                  {detailMetrics.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-x-4 gap-y-2" aria-label="任务详细统计">
                      {detailMetrics.map(item => (
                        <span key={item.key} className="text-muted">{item.label} <strong className="font-medium tabular-nums text-foreground">{visibleTask.metrics?.[item.key]}</strong></span>
                      ))}
                    </div>
                  )}
                </details>
              )}
              {Boolean(visibleTask.metrics?.greet_paused) && (
                <div className="mt-2 rounded-xl border border-warning-border bg-warning-soft px-3 py-2 text-xs font-bold leading-5 text-warning-strong">
                  提前停下的原因：{greetPauseReasonLabel(visibleTask.metrics?.greet_pause_reason) || '打分服务出了点问题'}。写好的都留着，剩下的岗位下次继续。
                </div>
              )}
            </div>
            {visibleTask.progress?.session && <div className="mt-3 flex flex-wrap items-center gap-3 border-t border-card-border pt-3 text-xs text-muted">
              <span>这次找到 {new Date(visibleTask.progress.session.deadline_at).toLocaleString()} 为止</span>
              {visibleTask.progress.session.manual_pending_reason && <span className="text-warning">等你处理：{visibleTask.progress.session.manual_pending_reason}</span>}
              {activeTask && visibleTask.progress.session.phase === 'waiting_user' && <Button size="sm" variant="secondary" onClick={() => {
                void fetch(`/api/collection/sessions/${visibleTask.progress?.session_id}/continue`, { method: 'POST' })
                  .then(async response => { const data = await response.json(); if (!response.ok) throw new Error(data.error); setNotice('已经让它继续了，会重新检查登录、风控和额度。') })
                  .catch(error => setNotice(error instanceof Error ? error.message : '继续失败'))
              }}><RefreshCw className="mr-1 h-3.5 w-3.5" />处理后继续</Button>}
            </div>}
            {visibleTask.progress?.platforms && <CollectionProgressPanel progress={visibleTask.progress} onRetryCollection={retryCollectionAfterRisk} />}
            {visibleTask.error && visibleTaskError && (
              <div className="mt-3 rounded-2xl border border-danger-border bg-danger-soft px-4 py-3 text-sm text-danger">
                <div className="font-black">{visibleTaskError.title}</div>
                <p className="mt-1 text-xs leading-5">{visibleTaskError.detail}</p>
                <details className="mt-2 text-xs text-muted">
                  <summary className="cursor-pointer font-bold">查看原始错误</summary>
                  <pre className="mt-2 whitespace-pre-wrap break-words rounded-lg bg-card p-2">{visibleTask.error}</pre>
                </details>
              </div>
            )}
            {visibleTask.stop_reason && (
              <div className={`mt-3 rounded-2xl px-3 py-3 text-sm ${visibleTask.stop_reason === 'daily_limit' ? 'border border-warning-border bg-warning-soft text-warning-strong' : 'bg-secondary text-primary'}`}>
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <div className="font-black">{visibleTask.stop_reason === 'daily_limit' ? '这次没发出去' : '任务说明'}</div>
                    <div className="mt-1">{taskStopReasonLabel(visibleTask.stop_reason)}</div>
                  </div>
                  {visibleTask.stop_reason === 'daily_limit' && (
                    <Button size="sm" variant="secondary" onClick={() => { window.location.href = '/config' }}>
                      去改每天投多少
                    </Button>
                  )}
                </div>
              </div>
            )}
          </div>
        )}
      </section>

      <VerificationModal
        hold={verification.open_hold}
        onContinue={resolveVerification}
        onStop={async () => {
          const taskId = workbench.task?.id
          if (taskId) await stopTask(taskId)
        }}
        canStop={Boolean(workbench.task)}
      />

      {workbench.send_quota?.exhausted && (
        <section className="animate-fade-up anim-delay-1 rounded-xl border border-warning-border bg-warning-soft px-3 py-2 text-warning-strong">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h3 className="text-sm font-bold">今天能投的数量用完了</h3>
              <p className="mt-0.5 text-xs leading-5">
                今天已经投了 {workbench.send_quota.sent}/{workbench.send_quota.daily_limit} 份，没投的岗位都留着，明天额度恢复了再投。
              </p>
            </div>
            <Button variant="secondary" size="sm" onClick={() => { window.location.href = '/config' }}>
              去改每天投多少
            </Button>
          </div>
        </section>
      )}

      {(workbench.send_quota?.suspended_platforms?.length ?? 0) > 0 && (
        <section className="animate-fade-up anim-delay-1 rounded-xl border border-warning-border bg-warning-soft px-3 py-2 text-warning-strong">
          <h3 className="text-sm font-bold">有平台今天先不投了</h3>
          <p className="mt-0.5 text-xs leading-5">
            {(workbench.send_quota.suspended_platforms ?? []).map(platform => PLATFORM_LABELS[platform] ?? platform).join('、')}
            连续失败触发了熔断，今天不再投，明天自动恢复。
          </p>
        </section>
      )}

      <section>
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 className="text-lg font-black">今天的成果</h3>
          </div>
          <div className="inline-flex rounded-full border border-card-border bg-card p-1">
            {([
              { value: 'today' as const, label: '看今天' },
              { value: 'total' as const, label: '看全部' },
            ]).map(option => (
              <button
                key={option.value}
                type="button"
                onClick={() => setStatsScope(option.value)}
                className={`rounded-full px-3 py-1.5 text-xs font-black transition ${
                  statsScope === option.value ? 'bg-primary text-primary-foreground shadow-sm' : 'text-muted hover:text-primary'
                }`}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
          {statItems.map(item => {
            const currentValue = workbench.pending_confirmation.length
            const selectedFunnel = statsScope === 'today' ? workbench.funnel_today : workbench.funnel
            const alternateFunnel = statsScope === 'today' ? workbench.funnel : workbench.funnel_today
            const value = item.current ? currentValue : (selectedFunnel[item.key] || 0)
            const supportingText = item.current
              ? '现在就能投，不用等'
              : `${statsScope === 'today' ? '一共' : '今天'} ${alternateFunnel[item.key] || 0} 个`
            return (
              <div key={item.key} className="hover-lift animate-fade-up rounded-2xl border border-card-border bg-card p-4">
                <div className="text-xs text-muted">{statsScope === 'today' ? item.todayLabel : item.totalLabel}</div>
                <div className={`mt-1 text-2xl font-black tabular-nums ${item.highlight ? 'text-primary' : 'text-foreground'}`}>
                  <AnimatedNumber value={value} />
                </div>
                <div className="mt-1 text-[10px] font-bold text-muted">{supportingText}</div>
              </div>
            )
          })}
        </div>
      </section>


      {/* 数据可视化概览：7日趋势 + 高分公司 + 最近活动 */}
      {(activityData.length > 0 || topCompanies.length > 0 || recentActivity.length > 0) ? (
        <section>
          <div className="mb-3">
            <h3 className="text-lg font-black">最近 7 天怎么样</h3>
          </div>
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
            <div className="lg:col-span-2">
              <TrendChart data={activityData} />
            </div>
            <TopCompanies data={topCompanies} />
          </div>
          <div className="mt-3">
            <RecentActivity data={recentActivity} />
          </div>
        </section>
      ) : (
        <section>
          <div className="mb-3">
            <h3 className="text-lg font-black">还没开始？先走这三步</h3>
            <p className="mt-0.5 text-xs text-muted">把简历和想找的工作填好，点上面按钮就能开始找</p>
          </div>
          <PipelineFlow />
        </section>
      )}
      <section className="animate-fade-up anim-delay-2 rounded-xl border border-card-border bg-card px-3 py-2">
        <details className="group">
          <summary className="flex cursor-pointer list-none items-center justify-between gap-3 py-1 text-sm [&::-webkit-details-marker]:hidden">
            <span className="font-semibold">HR 找你要简历了 <span className="ml-2 text-xs font-normal text-muted">{workbench.needs_resume.length ? `${workbench.needs_resume.length} 个等你发` : '现在没有'}</span></span>
            <ChevronDown className="h-4 w-4 shrink-0 text-muted transition-transform group-open:rotate-180" />
          </summary>
          <div className="mt-2 flex items-center justify-between gap-2 border-t border-card-border pt-2">
            <p className="text-xs text-muted">下载下来，手动发给 HR，发完点“我发过了”。</p>
            <Button variant="ghost" size="sm" onClick={() => { window.location.href = '/monitor' }}>去看看</Button>
          </div>
          <div className="divide-y divide-card-border">
            {workbench.needs_resume.slice(0, 4).map(job => (
              <div key={job.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                <span className="flex min-w-0 flex-wrap items-center gap-2 break-words text-sm"><span>{job.company}｜{job.title}</span><OutsourcingBadge job={job} /></span>
                <div className="flex max-w-full flex-wrap items-center gap-1">
                  <Button variant="ghost" size="sm" disabled={!job.url} onClick={() => window.open(job.url, '_blank', 'noopener,noreferrer')}><ExternalLink className="mr-1 h-3.5 w-3.5" />打开这个岗位</Button>
                  <Button variant="ghost" size="sm" onClick={() => downloadResume(job)}><Download className="mr-1 h-3.5 w-3.5" />下载简历</Button>
                  <Button variant="secondary" size="sm" onClick={() => markResumeSent(job)}>我发过了</Button>
                </div>
              </div>
            ))}
          </div>
        </details>
      </section>

      {workbench.send_errors.length > 0 && (
        <section className="animate-fade-up rounded-3xl border border-danger-border bg-danger-soft p-5">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-4">
            <div>
              <h3 className="text-lg font-black text-danger">这几个没投出去</h3>
              <p className="mt-1 text-xs text-danger/80">打招呼的话已经写好了，就是没发出去。可以再试一次，或者不要了。</p>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button size="sm" disabled={workbench.send_errors.some(job => sendingGreetingIds.has(job.id))} onClick={() => sendReadyGreetings(workbench.send_errors.map(job => job.id))}>
                {workbench.send_errors.some(job => sendingGreetingIds.has(job.id)) ? '正在重新发…' : `全部再发一次（${workbench.send_errors.length} 个）`}
              </Button>
              <Button variant="secondary" size="sm" onClick={() => rejectSelectedJobs(workbench.send_errors.map(job => job.id))}>都不要了</Button>
            </div>
          </div>
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            {workbench.send_errors.map(job => (
              <div key={job.id} className="rounded-2xl border border-danger-border bg-card p-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="flex flex-wrap items-center gap-2 font-black"><span>{job.company}｜{job.title}</span><OutsourcingBadge job={job} /></div>
                    <div className="mt-1 text-xs text-danger">没发出去的原因：{job.last_error || '发失败了，等着再试'}</div>
                  </div>
                  <span className="rounded-full bg-danger-soft px-2 py-1 text-[11px] font-black text-danger">没发出去</span>
                </div>
                <p className="mt-3 line-clamp-2 text-sm leading-6 text-muted">{job.greeting || '打招呼的话写好了，等着重新发。'}</p>
                <div className="mt-3 flex gap-2">
                  <Button size="sm" disabled={sendingGreetingIds.has(job.id)} onClick={() => sendReadyGreetings([job.id])}>
                    {sendingGreetingIds.has(job.id) ? '正在重新发…' : '再发一次'}
                  </Button>
                  <Button variant="secondary" size="sm" onClick={() => rejectSelectedJobs([job.id])}>不要了</Button>
                  <Button variant="secondary" size="sm" onClick={() => openJobDetail(job)}><Eye className="mr-2 h-4 w-4" />看详情</Button>
                  <Button variant="secondary" size="sm" disabled={!job.url} onClick={() => window.open(job.url, '_blank', 'noopener,noreferrer')}><ExternalLink className="mr-2 h-4 w-4" />打开这个岗位</Button>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {pendingGreetingJobs.length > 0 && (
        <section className="animate-fade-up rounded-xl border border-card-border bg-card p-3">
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <div>
              <h3 className="text-sm font-semibold">还没发的打招呼 <span className="ml-1 text-xs font-normal text-muted">{pendingGreetingJobs.length} 条</span></h3>
              <p className="mt-1 text-xs text-muted">挑一个顺眼的版本点发送；自动找工作的时候会自己发。</p>
            </div>
            <div className="flex flex-wrap gap-2">
              {pendingGreetingReviewCount > 0 && (
                <span className="rounded-full bg-warning-soft px-2 py-1 text-xs font-medium text-warning">
                  {pendingGreetingReviewCount} 个还没挑
                </span>
              )}
              <Button
                size="sm"
                disabled={reviewedGreetingJobs.length === 0 || reviewedGreetingJobs.some(job => sendingGreetingIds.has(job.id) || busyGreetingIds.has(job.id) || Boolean(job.greeting_activity))}
                onClick={() => sendReadyGreetings(reviewedGreetingJobs.map(job => job.id))}
              >
                发出去 {reviewedGreetingJobs.length} 个
              </Button>
              <Button variant="secondary" size="sm" onClick={() => rejectSelectedJobs(pendingGreetingJobs.map(job => job.id))}>都不要了</Button>
            </div>
          </div>
          <div className="divide-y divide-card-border border-t border-card-border">
            {pendingGreetingJobs.map(job => (
              <GreetingReviewCard
                key={job.id}
                job={job}
                busy={sendingGreetingIds.has(job.id) || Boolean(job.greeting_activity)}
                onBusyChange={onGreetingBusyChange}
                onSelect={(selection, greeting) => selectGreeting(job, selection, greeting)}
                onSend={() => sendReadyGreetings([job.id])}
                onReject={() => rejectSelectedJobs([job.id])}
                onDetail={() => openJobDetail(job)}
              />
            ))}
          </div>
        </section>
      )}

      <section className="animate-fade-up rounded-2xl border border-card-border bg-card p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 className="text-lg font-black">今天能投的岗位</h3>
            <p className="mt-0.5 text-xs text-muted">勾上想投的，点一下就投出去；自动找工作时，够分的会自己投，不用你管。</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" size="sm" disabled={!filteredTodayJobs.length} onClick={() => setSelected(filteredTodayJobs.map(job => job.id))}>全选</Button>
            <Button variant="secondary" size="sm" disabled={!selected.length} onClick={() => setSelected([])}>清空</Button>
            <Button variant="secondary" size="sm" disabled={generatingGreetings || greetTaskRunning || !actionableSelected.length} onClick={() => generateGreetings(actionableSelected)}>
              {generatingGreetings || greetTaskRunning ? '正在写…' : `给选中的写打招呼 ${actionableSelected.length} 个`}
            </Button>
            <Button variant="secondary" size="sm" disabled={!actionableSelected.length} onClick={() => rejectSelectedJobs(actionableSelected)}>不要选中的 {actionableSelected.length} 个</Button>
            <Button size="sm" disabled={!actionableSelected.length} onClick={() => confirmDeliver(actionableSelected)}>把选中的都投了 {actionableSelected.length} 个</Button>
          </div>
        </div>
        <div className="mb-3 rounded-xl border border-card-border bg-surface px-2 pb-2 pt-2">
          <div className="flex flex-wrap items-center justify-between gap-2 px-1 pb-1 text-xs">
            <span className="flex items-center gap-2 font-bold">
              筛选条件
              {activeTodayFilterCount > 0 && <span className="font-normal text-primary">已启用 {activeTodayFilterCount} 项</span>}
            </span>
            <span className={hasInvalidSalaryRange(todayFilters) ? 'text-danger' : 'text-muted'}>
              {hasInvalidSalaryRange(todayFilters) ? '工资范围填错了，改一下' : `${filteredTodayJobs.length} / ${todayJobs.length} 个岗位`}
            </span>
          </div>
          <JobFilterBar
            compact
            filters={todayFilters}
            onChange={setTodayFilters}
            onReset={() => setTodayFilters({ ...EMPTY_JOB_FILTERS })}
            resultCount={filteredTodayJobs.length}
            totalCount={todayJobs.length}
            invalidSalary={hasInvalidSalaryRange(todayFilters)}
            cityOptions={todayCityOptions}
          />
        </div>
        {filteredTodayJobs.length ? (
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            {filteredTodayJobs.map(job => (
              <JobActionCard
                key={job.id}
                job={job}
                selected={selected.includes(job.id)}
                onToggle={() => toggleJob(job.id)}
                onDetail={() => openJobDetail(job)}
                onReject={() => rejectSelectedJobs([job.id])}
              />
            ))}
          </div>
        ) : todayJobs.length ? (
          <div className="rounded-2xl border border-dashed border-card-border bg-surface p-5 text-center text-sm text-muted">
            <p>按现在的条件，没找到合适的岗位</p>
            <Button className="mt-3" variant="secondary" size="sm" onClick={() => setTodayFilters({ ...EMPTY_JOB_FILTERS })}>重置筛选</Button>
          </div>
        ) : (
          <p className="py-2 text-xs text-muted">今天暂时没有能投的岗位，先去找找新的吧。</p>
        )}
      </section>

      {selectedJob && <JobDetailModal job={selectedJob} onClose={() => setSelectedJob(null)} onChanged={() => void refresh()} />}
      <CollectJobsDialog
        open={collectDialogOpen}
        mode={collectDialogMode}
        activeTask={activeTask && (activeTask.mode === 'collect' || activeTask.mode === 'full') ? activeTask : null}
        onClose={() => setCollectDialogOpen(false)}
        onStart={startCollection}
      />
    </div>
  )
}

function collectionReasonLabel(reason = '') {
  if (reason.includes('daily_detail_page_limit')) return '今天能打开的岗位详情用完了'
  if (reason.includes('daily_platform_page_limit')) return '今天能打开的页面都用完了'
  if (reason.includes('daily_search_page_limit')) return '今天能翻的搜索页用完了'
  return ''
}

function CollectionProgressPanel({ progress, onRetryCollection }: { progress: CollectionProgress; onRetryCollection?: () => Promise<void> }) {
  const outcome = ({ running: '采集中', scoring: '正在打分', completed: '找完了', completed_with_shortage: '找完了，但新岗位不多', completed_with_errors: '找完了，有几个出了点问题', failed: '这次没找成', stopped: '已停下', cancelled: '已取消' } as Record<string, string>)[progress.outcome || ''] || '不知道现在什么状态'
  const platforms = Object.entries(progress.platforms || {})
  return (
    <details className="group mt-2 rounded-xl border border-card-border bg-card px-3 py-2 text-xs">
      <summary className="flex cursor-pointer list-none flex-wrap items-center gap-x-3 gap-y-1 [&::-webkit-details-marker]:hidden">
        <span className="flex items-center gap-1.5 font-semibold"><ChevronDown className="h-3.5 w-3.5 text-muted transition-transform group-open:rotate-180" />找岗位的详情</span>
        <span className="text-muted">{outcome}</span>
        {platforms.map(([platform, state]) => (
          <span key={platform} className="text-muted">{PLATFORM_LABELS[platform] || platform} · 新找到 {state.new || 0} 条
            {collectionReasonLabel(`${state.reason_code || ''} ${state.message || ''}`) && <span className="ml-2 text-warning">{collectionReasonLabel(`${state.reason_code || ''} ${state.message || ''}`)}</span>}
          </span>
        ))}
      </summary>
      <div className="mt-2 divide-y divide-card-border border-t border-card-border">
        {platforms.map(([platform, state]) => (
          <div key={platform} className="py-2 leading-5">
            <div className="flex flex-wrap justify-between gap-x-3">
              <span className="font-medium">{PLATFORM_LABELS[platform] || platform}</span>
              <span className="text-muted">{state.status === 'queued' ? '等前面的平台找完' : `${state.city || '这个城市还没开始'} · ${state.keyword || '这个词还没开始'} · 第 ${state.page || 0}/${state.max_pages || 0} 页`}</span>
            </div>
            <p className="text-muted">看了 {state.seen || 0} · 新增 {state.new} · 重复 {state.duplicate || 0} · 筛掉的 {state.filtered || 0} · 没看懂 {state.parse_failed || 0} · 没保存上 {state.save_failed || 0}</p>
            {state.city_results && <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-muted">{Object.entries(state.city_results).map(([city, result]) => <span key={city}>{city}：新增 {result.new_total || 0} · {({ running: '采集中', completed_scope: '这轮找完了', partial: '找了一部分', interrupted: '中断' } as Record<string, string>)[result.status] || result.status}</span>)}</div>}
            {Number(state.filtered) > 0 && (
              <details className="mt-2 rounded-lg border border-card-border bg-secondary/40 p-2" open>
                <summary className="cursor-pointer font-medium">筛掉的记录 · {state.filtered} 条</summary>
                <p className="mt-1 text-xs text-muted">每个平台只留最近 200 条；筛掉的岗位不会出现在找工作里。</p>
                {state.filter_records?.length ? (
                  <>
                    <p className="text-xs text-muted">留了 {state.filter_records.length} 条{state.filter_records.length < Number(state.filtered) ? '，剩下的没留下来，或者是老版本找的' : ''}</p>
                    <ol tabIndex={0} aria-label={`${PLATFORM_LABELS[platform] || platform}筛掉的记录`} className="mt-2 max-h-60 space-y-2 overflow-y-auto overscroll-contain pr-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary">
                      {state.filter_records.map((record, index) => (
                        <li key={`${record.source_job_id}-${index}`} className="rounded-md bg-card p-2">
                          <p className="break-words font-medium">{record.title || '没写职位名'} · {record.company || '没写公司名'}</p>
                          <p className="break-words text-muted">{record.reason}</p>
                        </li>
                      ))}
                    </ol>
                  </>
                ) : <p className="mt-2 text-muted">这次没记下为什么筛掉，老记录补不回来；以后新找的都会记下来。</p>}
              </details>
            )}
            {(Number(state.parse_failed || 0) + Number(state.save_failed || 0)) > 0 && (
              <details open className="mt-2 rounded-lg border border-danger-border bg-danger-soft p-2">
                <summary className="cursor-pointer font-medium text-danger">出问题的记录 · {(state.parse_failed || 0) + (state.save_failed || 0)} 次</summary>
                <p className="mt-1 text-xs text-muted">每个平台只留最近 200 条出问题的记录，现在留了 {state.error_records?.length || 0} 条。</p>
                {state.error_records?.length ? (
                  <ol tabIndex={0} aria-label={`${PLATFORM_LABELS[platform] || platform}出问题的记录`} className="mt-2 max-h-60 space-y-2 overflow-y-auto overscroll-contain pr-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary">
                    {state.error_records.map((record, index) => (
                      <li key={index} className="rounded-md bg-card p-2">
                        <p className="font-medium text-danger">{record.kind === 'save' ? '没存上' : '解析失败'}</p>
                        {record.title && <p className="break-words">{record.title}{record.company ? ` · ${record.company}` : ''}</p>}
                        <p className="break-words text-muted">{[record.city, record.keyword, record.page ? `第 ${record.page} 页` : ''].filter(Boolean).join(' · ') || '没打开页面'}</p>
                        <p className="break-words">{record.reason}</p>
                      </li>
                    ))}
                  </ol>
                ) : <p className="mt-2 text-muted">这次没记下出问题的原因，老记录补不回来。</p>}
              </details>
            )}
            {(state.message || state.reason_code) && !state.filter_records?.some(record => record.reason === state.message) && !state.error_records?.some(record => record.reason === state.message) && <p className="mt-1 break-words text-muted">{state.message?.replace(/；本轮新增 \d+ 条，读取 \d+ 条，重复 \d+ 条，过滤 \d+ 条，解析失败 \d+ 条，保存失败 \d+ 条$/, '') || state.reason_code}</p>}
            {onRetryCollection && ['captcha', 'blocked', 'rate_limit'].includes(state.reason_code || '') && (
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <Button size="sm" variant="secondary" onClick={() => { void onRetryCollection() }}>立即重试（不等冷却）</Button>
                <span className="text-muted">点一下会先解除本轮的冷却，再重新开始找岗位。如果你确实在浏览器里看到了验证码，请先在浏览器里完成验证再点。</span>
              </div>
            )}
          </div>
        ))}
      </div>
    </details>
  )
}

function GreetingReviewCard({
  job,
  busy = false,
  onBusyChange,
  onSelect,
  onSend,
  onReject,
  onDetail,
}: {
  job: Job
  busy?: boolean
  onBusyChange?: (id: string, busy: boolean) => void
  onSelect: (selection: 'original' | 'optimized' | 'edited', greeting?: string) => Promise<void>
  onSend: () => void
  onReject: () => void
  onDetail: () => void
}) {
  const [expanded, setExpanded] = useState(false)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(job.greeting || '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => {
    onBusyChange?.(job.id, editing || saving)
    return () => onBusyChange?.(job.id, false)
  }, [job.id, editing, saving, onBusyChange])
  const original = job.greeting_original || job.greeting || ''
  const optimized = job.greeting_optimized || ''
  const hasPreview = Boolean(optimized && optimized !== original)
  const needsSelection = job.greeting_selection === 'pending'
  const issues = Array.isArray(job.greeting_style_issues) ? job.greeting_style_issues : []
  const selectionLabel = needsSelection
    ? '还没挑'
    : job.greeting_selection === 'auto_optimized'
      ? '已经帮你挑了一个，还能换'
      : job.greeting_reviewed_at
        ? '你已经确认过了'
        : '等着发'

  const saveSelection = async (selection: 'original' | 'optimized' | 'edited', greeting = '') => {
    setSaving(true)
    setError('')
    try {
      await onSelect(selection, greeting)
      setEditing(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="py-2.5">
      <button
        type="button"
        aria-expanded={expanded}
        aria-controls={`greeting-review-${job.id}`}
        aria-label={`${expanded ? '收起来' : '展开'}打招呼的话：${job.company}｜${job.title}`}
        disabled={editing || saving}
        onClick={() => setExpanded(value => !value)}
        className="w-full rounded-lg text-left outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:cursor-default"
      >
        <span className="flex items-center gap-2">
          <span className={`min-w-0 flex-1 text-sm font-medium ${expanded ? 'break-words' : 'truncate'}`}>{job.company}｜{job.title}</span>
          <OutsourcingBadge job={job} />
          <span className={`shrink-0 rounded px-1.5 py-0.5 text-xs ${needsSelection ? 'bg-warning-soft text-warning' : 'text-muted'}`}>{selectionLabel}</span>
          <ChevronDown className={`h-4 w-4 shrink-0 text-muted transition-transform ${expanded ? 'rotate-180' : ''}`} />
        </span>
        {!expanded && <span className="mt-1 block truncate text-xs leading-5 text-muted">{job.greeting || original || '点开看看打招呼的话'}</span>}
      </button>
      {job.greeting_activity && (
        <p className="mt-1 text-xs text-muted" role="status">
          {job.greeting_activity === 'sending' ? '这条正在发，现在改不了。'
            : job.greeting_activity === 'generating' ? '这条正在写，写好就能挑。'
              : '这条正在保存，等一下。'}
        </p>
      )}
      {expanded && <div id={`greeting-review-${job.id}`}>

        {issues.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted" aria-label="为什么这样改">
            {issues.map(issue => (
              <span key={issue} className="leading-5">
                {issue}
              </span>
            ))}
          </div>
        )}

        {hasPreview ? (
          <div className="mt-2 grid gap-2 md:grid-cols-2">
            <div className={`rounded-xl border p-3 ${job.greeting_selection === 'original' || needsSelection ? 'border-card-border bg-surface' : 'border-card-border/70 bg-card'}`}>
              <div className="text-[11px] font-semibold text-muted">原来的写法</div>
              <p className="mt-1 whitespace-pre-wrap text-sm leading-6 text-foreground">{original}</p>
              <Button className="mt-3" variant="secondary" size="sm" disabled={saving || busy} onClick={() => void saveSelection('original')}>
                就用原来的
              </Button>
            </div>
            <div className={`rounded-xl border p-3 ${job.greeting_selection === 'optimized' || job.greeting_selection === 'auto_optimized' ? 'border-primary/30 bg-surface-muted' : 'border-primary/20 bg-card'}`}>
              <div className="flex items-center gap-1.5 text-[11px] font-semibold text-primary">
                <Sparkles className="h-3.5 w-3.5" />改完的样子
              </div>
              <p className="mt-1 whitespace-pre-wrap text-sm leading-6 text-foreground">{optimized}</p>
              <Button className="mt-3" size="sm" disabled={saving || busy} onClick={() => void saveSelection('optimized')}>
                就用改完的
              </Button>
            </div>
          </div>
        ) : (
          <div className="mt-3 rounded-xl border border-card-border bg-surface p-3">
            <div className="text-[11px] font-semibold text-muted">现在用的版本</div>
            <p className="mt-1 whitespace-pre-wrap text-sm leading-6 text-foreground">{job.greeting || '打招呼的话写好了，等着发。'}</p>
          </div>
        )}

        {hasPreview && !needsSelection && (
          <div className="mt-3 rounded-xl border border-primary/30 bg-surface-muted p-3" aria-label="最后发出去的版本">
            <div className="text-[11px] font-semibold text-primary">最终发送版本</div>
            <p className="mt-1 whitespace-pre-wrap text-sm leading-6 text-foreground">{job.greeting}</p>
          </div>
        )}

        {editing && (
          <div className="mt-3 rounded-xl border border-card-border bg-surface p-3">
            <label className="text-xs font-black text-foreground" htmlFor={`greeting-edit-${job.id}`}>自己改改最后发的版本</label>
            <textarea
              id={`greeting-edit-${job.id}`}
              value={draft}
              maxLength={300}
              onChange={event => setDraft(event.target.value)}
              className="mt-2 min-h-28 w-full resize-y rounded-xl border border-card-border bg-card p-3 text-sm leading-6 outline-none focus:border-primary"
            />
            <div className="mt-2 flex items-center justify-between gap-3">
              <span className="text-xs text-muted">{draft.length}/300</span>
              <div className="flex gap-2">
                <Button variant="secondary" size="sm" disabled={saving} onClick={() => setEditing(false)}>取消</Button>
                <Button size="sm" disabled={saving || busy || !draft.trim()} onClick={() => void saveSelection('edited', draft)}>保存我改的</Button>
              </div>
            </div>
          </div>
        )}

        {error && <p className="mt-3 rounded-lg bg-danger-soft px-3 py-2 text-xs font-bold text-danger">{error}</p>}

        <div className="mt-3 flex flex-wrap gap-2">
          <Button size="sm" disabled={needsSelection || editing || saving || busy} onClick={onSend}>发这条打招呼</Button>
          <Button variant="secondary" size="sm" disabled={saving || busy} onClick={() => { setDraft(job.greeting || original); setEditing(true) }}>
            <Pencil className="mr-2 h-4 w-4" />手动改一改
          </Button>
          <Button variant="ghost" size="sm" onClick={onDetail}><Eye className="mr-2 h-4 w-4" />看详情</Button>
          <Button variant="ghost" size="sm" disabled={saving || busy} onClick={onReject}>放弃</Button>
        </div>
      </div>}
    </div>
  )
}

function JobActionCard({ job, selected, onToggle, onDetail, onReject }: { job: Job; selected: boolean; onToggle: () => void; onDetail: () => void; onReject: () => void }) {
  return (
    <div className={`rounded-2xl border p-4 ${selected ? 'border-primary bg-surface' : 'border-card-border bg-surface'}`}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2 font-black"><span>{job.company}｜{job.title}</span><OutsourcingBadge job={job} /></div>
          <div className="mt-1 text-xs text-muted">{jobSubtitle(job)}</div>
        </div>
        <input type="checkbox" checked={selected} onChange={onToggle} className="mt-1 h-4 w-4 accent-primary" />
      </div>
      <p className="mt-3 line-clamp-2 text-sm leading-6 text-muted">{job.score_reason || job.greeting || '等你继续。'}</p>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button variant="secondary" size="sm" onClick={onDetail}><Eye className="mr-2 h-4 w-4" />查看详情</Button>
        <Button variant="secondary" size="sm" disabled={!job.url} onClick={() => window.open(job.url, '_blank', 'noopener,noreferrer')}><ExternalLink className="mr-2 h-4 w-4" />打开这个岗位</Button>
        <Button variant="secondary" size="sm" onClick={onReject}><XCircle className="mr-2 h-4 w-4" />不要这个岗位</Button>
      </div>
    </div>
  )
}

function JobDetailModal({ job, onClose, onChanged }: { job: Job; onClose: () => void; onChanged?: () => void }) {
  const [greeting, setGreeting] = useState(job.greeting || '')
  const [savedGreeting, setSavedGreeting] = useState(job.greeting || '')
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [regenerating, setRegenerating] = useState(false)
  const [notice, setNotice] = useState('')
  const [reviewed, setReviewed] = useState(Boolean(job.greeting_reviewed_at))
  const [selectionPending, setSelectionPending] = useState(job.greeting_selection === 'pending')

  const saveGreeting = async () => {
    const text = greeting.trim()
    if (!text) {
      setNotice('打招呼的话不能为空')
      return
    }
    setSaving(true)
    try {
      const res = await fetch(`/api/jobs/${job.id}/greeting`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ greeting: text, confirmed: true }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.error || '保存失败')
      }
      setEditing(false)
      setSavedGreeting(text)
      setReviewed(true)
      setSelectionPending(false)
      setNotice('最后发的版本已经定好了，不会被后面的生成盖掉。')
      onChanged?.()
    } catch (err) {
      setNotice(err instanceof Error ? err.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  const regenerateGreeting = async () => {
    setRegenerating(true)
    setNotice('')
    try {
      const res = await fetch('/api/workbench/greetings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_ids: [job.id], regenerate: true }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        throw new Error(data.error || '重新写失败了')
      }
      const taskId = data.task?.id
      const finished = taskId ? await waitForGreetTask(taskId) : null
      if (!finished) {
        const detailRes = await fetch(`/api/jobs/${job.id}`)
        if (detailRes.ok) {
          const detail = await detailRes.json()
          setGreeting(detail.greeting || '')
          setSavedGreeting(detail.greeting || '')
          setReviewed(Boolean(detail.greeting_reviewed_at))
          setSelectionPending(detail.greeting_selection === 'pending')
        }
        onChanged?.()
        setNotice('写得有点慢，还在后台写着，过会儿刷新看看。')
        return
      }
      if (finished.status === 'stopped') {
        throw new Error('重新写停下来了，还是用原来那句打招呼')
      }
      if (finished.status === 'failed') {
        throw new Error(finished.error ? `重新写失败：${finished.error}` : '重新生成失败')
      }
      if ((finished.progress?.conflict_ids ?? []).includes(job.id)) {
        throw new Error('这个岗位的状态变了，打招呼的话没保存上，刷新一下再试')
      }
      if (!finished.metrics?.greet_generated) {
        throw new Error('这次没写完整，这个岗位先记着没写好，过会儿再试')
      }
      const detailRes = await fetch(`/api/jobs/${job.id}`)
      if (detailRes.ok) {
        const detail = await detailRes.json()
        setGreeting(detail.greeting || '')
        setSavedGreeting(detail.greeting || '')
        setReviewed(Boolean(detail.greeting_reviewed_at))
        setSelectionPending(detail.greeting_selection === 'pending')
      }
      setNotice('已经重新写好了')
      onChanged?.()
    } catch (err) {
      setNotice(err instanceof Error ? err.message : '重新生成失败')
    } finally {
      setRegenerating(false)
    }
  }

  // 定位：经 ModalPortal 挂到 body，fixed 才相对视口居中，不会被
  // .animate-page-in 的 transform 拽到整页中部。
  // 布局：之前整个面板一个 overflow-y-auto 在滚，顶部的“关闭”按钮会跟着内容
  // 滚出视口；现在面板 flex 纵向分栏，标题栏固定在顶部，只有正文区滚动。
  return (
    <ModalPortal>
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-6">
      <div className="flex max-h-[86vh] w-full max-w-3xl flex-col overflow-hidden rounded-3xl border border-card-border bg-card shadow-2xl">
        <div className="flex shrink-0 items-start justify-between gap-4 border-b border-card-border px-6 pb-4 pt-6">
          <div>
            <div className="text-xs font-black tracking-[0.18em] text-primary">岗位详情</div>
            <h3 className="mt-1 flex flex-wrap items-center gap-2 text-2xl font-black"><span>{job.company}｜{job.title}</span><OutsourcingBadge job={job} /></h3>
            <p className="mt-1 text-sm text-muted">{job.salary || '没填工资'} · {job.city || '没填城市'} · {getStatusLabel(job.status)}</p>
          </div>
          <Button variant="secondary" size="sm" onClick={onClose}>关闭</Button>
        </div>
        <div data-testid="job-detail-body" className="min-h-0 flex-1 overflow-y-auto px-6 pb-6 pt-4">
        <InterviewPreparation key={job.id} job={job} />
        <div className="grid gap-3 text-sm lg:grid-cols-2">
          <InfoBlock label="HR" value={[job.hr_name, job.hr_title].filter(Boolean).join(' · ') || '-'} />
          <InfoBlock label="HR 活跃程度" value={job.hr_active || '活跃度未知'} />
          <InfoBlock label="公司" value={[job.company_size, job.company_industry].filter(Boolean).join(' · ') || '-'} />
          <InfoBlock label="来自哪个平台" value={job.source_platform && job.source_platform !== 'boss' && PLATFORM_LABELS[job.source_platform] ? `${PLATFORM_LABELS[job.source_platform]}｜现在只用来找岗位` : 'BOSS 直聘'} />
          <InfoBlock label="匹配分数" value={String(job.score || '-')} />
          <InfoBlock label="定制简历" value={job.resume_path || '还没做'} />
        </div>
        <div className="mt-4 rounded-2xl border border-card-border bg-surface p-4">
          <div className="text-sm font-black">为什么给这个分</div>
          <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-muted">{job.score_reason || '-'}</p>
        </div>
        <div className="mt-4 rounded-2xl border border-card-border bg-surface p-4">
          <div className="flex items-center justify-between gap-3">
            <div className="text-sm font-black">打招呼的话</div>
            <div className="flex gap-2">
              {editing ? (
                <>
                  <Button size="sm" disabled={saving} onClick={saveGreeting}>{saving ? '保存中...' : '保存'}</Button>
                  <Button size="sm" variant="secondary" disabled={saving} onClick={() => { setEditing(false); setGreeting(savedGreeting) }}>取消</Button>
                </>
              ) : (
                <>
                  <Button size="sm" variant="secondary" disabled={regenerating} onClick={() => { setEditing(true); setNotice('') }}>编辑</Button>
                  <Button size="sm" variant="secondary" disabled={regenerating || reviewed} onClick={regenerateGreeting}>
                    {regenerating ? '正在写...' : '重新写'}
                  </Button>
                </>
              )}
            </div>
          </div>
          {reviewed && <p className="mt-2 text-xs text-muted">最后发的版本你已经确认过了，想改就自己编辑。</p>}
          {selectionPending && <p className="mt-2 text-xs text-warning">已经有改好的版本了，回“还没发的打招呼”里挑一个，或者自己改改保存。</p>}
          {editing ? (
            <textarea
              className="mt-2 w-full rounded-xl border border-card-border bg-card p-3 text-sm leading-6 text-foreground focus:border-primary focus:outline-none"
              rows={4}
              maxLength={300}
              value={greeting}
              onChange={e => setGreeting(e.target.value)}
            />
          ) : (
            <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-muted">{greeting || '未生成'}</p>
          )}
          {notice && <div className="mt-2 text-xs font-bold text-primary">{notice}</div>}
        </div>
        <div className="mt-4 rounded-2xl border border-card-border bg-surface p-4">
          <div className="text-sm font-black">JD</div>
          <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-muted">{job.jd || '-'}</p>
        </div>
        </div>
      </div>
    </div>
    </ModalPortal>
  )
}

function InfoBlock({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-2xl border border-card-border bg-surface p-4">
      <div className="text-xs text-muted">{label}</div>
      <div className="mt-1 font-bold text-foreground">{value}</div>
    </div>
  )
}

function JobsPoolView({ updateJobStatus }: { updateJobStatus: (jobId: string, status: string) => Promise<void> }) {
  const pageSize = 15
  const [page, setPage] = useState(0)
  const [filters, setFilters] = useState<JobFilters>({ ...EMPTY_JOB_FILTERS })
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const [notice, setNotice] = useState('')
  // “选中全部岗位”那次拿到的全量 ID：只用来判断是不是还处在全选态
  // （全选后单改一两个勾选，全选态就没了，按钮变回“选中全部岗位”）。
  const [selectAllIds, setSelectAllIds] = useState<string[] | null>(null)
  const [selectingAll, setSelectingAll] = useState(false)
  const [blockedCompanies, setBlockedCompanies] = useState<string[]>([])
  const [blockingJobId, setBlockingJobId] = useState<string | null>(null)
  const blockingCompanyRef = useRef(false)
  const [showRecycleBin, setShowRecycleBin] = useState(false)
  const [showScoreDialog, setShowScoreDialog] = useState(false)
  const [quickScoring, setQuickScoring] = useState(false)
  const [sortBy, setSortBy] = useState<JobSortKey>('created_at')
  const [sortOrder, setSortOrder] = useState<JobSortOrder>('desc')
  const [recycleJobs, setRecycleJobs] = useState<Job[]>([])
  const [recycleSelectedIds, setRecycleSelectedIds] = useState<string[]>([])
  const [recycleLoading, setRecycleLoading] = useState(false)
  const [permanentDeleteIds, setPermanentDeleteIds] = useState<string[]>([])
  const [permanentDeleteAcknowledged, setPermanentDeleteAcknowledged] = useState(false)
  const { items, total, allTotal, cityOptions, loading, error, lastRefreshedAt: jobsRefreshedAt, refresh: refreshJobs } = useJobSearch(filters, page, pageSize, sortBy, sortOrder)
  const { config } = useConfig()
  // 分数发光的门槛就是“多少分以上才投”：配置里改了阈值，发光跟着变。
  const scoreThreshold = typeof config?.scoring?.threshold === 'number' ? config.scoring.threshold : 60
  const { workbench: deliveryWorkbench, error: progressError, refresh: refreshWorkbench } = useDashboard('workbench')
  const deliveryTask = deliveryWorkbench.task?.mode === 'deliver'
    ? deliveryWorkbench.task
    : deliveryWorkbench.last_task?.mode === 'deliver' ? deliveryWorkbench.last_task : null

  useEffect(() => {
    const controller = new AbortController()
    const loadBlockedCompanies = () => {
      fetch('/api/agent/state', { cache: 'no-store', signal: controller.signal })
        .then(async res => {
          if (!res.ok) return
          const data = await res.json()
          if (!controller.signal.aborted && Array.isArray(data.preferences?.blocked_companies)) {
            setBlockedCompanies(data.preferences.blocked_companies)
          }
        })
        .catch(() => { /* 点屏蔽的时候会重新读一遍，读不出来会告诉你。 */ })
    }
    loadBlockedCompanies()
    const handleConfigSaved = () => {
      loadBlockedCompanies()
      setSelectedIds([])
      setSelectAllIds(null)
      setPage(0)
      refreshJobs()
    }
    window.addEventListener('jianli-lieshou-config-saved', handleConfigSaved)
    return () => {
      controller.abort()
      window.removeEventListener('jianli-lieshou-config-saved', handleConfigSaved)
    }
  }, [refreshJobs])

  useEffect(() => {
    setPage(0)
    // 筛选变了，之前那批全选 ID 就不再是“全部岗位”，全选态清掉。
    setSelectAllIds(null)
    // 勾选也清掉：留着旧筛选的勾选，投递/打分/回收站会把旧岗位也算进去。
    setSelectedIds([])
  }, [filters.query, filters.minScore, filters.salaryMin, filters.salaryMax, filters.status, filters.createdWithin, filters.sourcePlatform, filters.city, filters.education, filters.recruitmentType])

  const toggleSelected = (jobId: string) => {
    setSelectedIds(previous => previous.includes(jobId) ? previous.filter(id => id !== jobId) : [...previous, jobId])
  }

  const allPageSelected = items.length > 0 && items.every(job => selectedIds.includes(job.id))

  // v48：工具栏回到两个切换按钮——①“这页全选 / 这页都不选”是当前页
  // 单按钮切换；②“选中全部岗位 / 取消选中全部岗位”是跨页全选（当前筛
  // 选条件下的全部岗位）。本页切换只动本页的勾选，不碰全选态的记录；
  // 全选态只认“上次全选那批 ID 是不是都还勾着”，单改一两个就掉出全选态。
  const selectCurrentPage = () => {
    const pageIds = items.map(job => job.id)
    setSelectedIds(previous => [...new Set([...previous, ...pageIds])])
  }

  const deselectCurrentPage = () => {
    const pageIds = new Set(items.map(job => job.id))
    setSelectedIds(previous => previous.filter(id => !pageIds.has(id)))
  }

  const toggleCurrentPage = () => {
    if (allPageSelected) {
      deselectCurrentPage()
    } else {
      selectCurrentPage()
    }
  }

  const allSelected = selectAllIds !== null
    && selectAllIds.length > 0
    && selectAllIds.every(id => selectedIds.includes(id))

  const selectAllJobs = async () => {
    setSelectingAll(true)
    try {
      const params = buildJobSearchParams(filters, sortBy, sortOrder)
      const res = await fetch(`/api/jobs/search/ids?${params.toString()}`, { cache: 'no-store' })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(data.error || '全部岗位没拿到，请再点一次试试。')
      const ids = (Array.isArray(data.ids) ? data.ids : []).map(String)
      setSelectAllIds(ids)
      setSelectedIds(ids)
      setNotice(`已选 ${ids.length} 个岗位。`)
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : '全部岗位没拿到，请再点一次试试。')
    } finally {
      setSelectingAll(false)
    }
  }

  const cancelSelectAll = () => {
    setSelectAllIds(null)
    setSelectedIds([])
  }

  const changeSort = (nextSortBy: JobSortKey) => {
    setPage(0)
    if (nextSortBy === sortBy) {
      setSortOrder(previous => previous === 'asc' ? 'desc' : 'asc')
      return
    }
    setSortBy(nextSortBy)
    setSortOrder(nextSortBy === 'score' || nextSortBy === 'created_at' ? 'desc' : 'asc')
  }

  const loadRecycleBin = async () => {
    setRecycleLoading(true)
    try {
      const collected: Job[] = []
      let offset = 0
      const limit = 200
      while (true) {
        const res = await fetch(`/api/jobs?deleted=only&limit=${limit}&offset=${offset}`, { cache: 'no-store' })
        if (!res.ok) throw new Error(`回收站那边返回 ${res.status}`)
        const pageItems = await res.json()
        if (!Array.isArray(pageItems)) throw new Error('回收站返回的东西看不懂')
        collected.push(...pageItems)
        const totalCount = Number(res.headers.get('X-Total-Count'))
        if (!pageItems.length || pageItems.length < limit || (Number.isFinite(totalCount) && collected.length >= totalCount)) break
        offset += pageItems.length
      }
      const unique = new Map(collected.map(job => [String(job.id), job]))
      setRecycleJobs([...unique.values()])
      setRecycleSelectedIds(previous => previous.filter(id => unique.has(id)))
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : '回收站没打开')
    } finally {
      setRecycleLoading(false)
    }
  }

  useEffect(() => {
    void loadRecycleBin()
  }, [])

  const postJobAction = async (path: string, payload: Record<string, unknown>) => {
    const res = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      const blocked = Array.isArray(data.blocked)
        ? data.blocked.map((item: { job_id?: string; reasons?: string[] }) => `${item.job_id || '岗位'}：${(item.reasons || []).join('、')}`).join('；')
        : ''
      throw new Error([data.error || '这个操作没成功', blocked].filter(Boolean).join('；'))
    }
    return res.json()
  }

  const softDelete = async (jobIds: string[]) => {
    if (!jobIds.length || !window.confirm(`确定要把 ${jobIds.length} 个岗位扔进回收站吗？扔进去还能找回来，不会彻底删掉。`)) return
    try {
      const result = await postJobAction('/api/jobs/soft-delete', { job_ids: jobIds, confirmed: true })
      setSelectedIds(previous => previous.filter(id => !jobIds.includes(id)))
      refreshJobs()
      await loadRecycleBin()
      setNotice(`已经扔进回收站了 ${result.affected_count || 0} 个岗位。`)
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : '没扔进去')
    }
  }

  const blockCompany = async (job: Job) => {
    const company = (job.company || '').trim()
    if (!company || blockingCompanyRef.current) return
    blockingCompanyRef.current = true
    setBlockingJobId(job.id)
    setNotice('正在读屏蔽名单，马上给你看预览…')
    try {
      const readRules = async (): Promise<string[]> => {
        const res = await fetch('/api/agent/state', { cache: 'no-store' })
        const data = await res.json()
        const rules = data.preferences?.blocked_companies
        if (!res.ok || !Array.isArray(rules) || !rules.every(rule => typeof rule === 'string')) {
          throw new Error(data.error || '屏蔽名单没读出来，再试一次。')
        }
        return rules
      }
      const current = await readRules()
      setBlockedCompanies(current)
      const existing = current.find(rule => rule.trim() && company.toLowerCase().includes(rule.trim().toLowerCase()))
      if (existing) {
        setSelectedIds([])
        setPage(0)
        refreshJobs()
        setNotice(`“${company}”已经被“${existing}”屏蔽了。想解除就去“我的设置 → 我的简历和我 → 不想去的公司”里删掉它。`)
        return
      }
      const preferences = { blocked_companies: [...current, company] }
      await postJobAction('/api/agent/config/preview', { preferences })
      if (!window.confirm(`配置预览\n新增屏蔽这家公司：${company}\n已有屏蔽规则：${current.join('、') || '无'}\n\n保存后，公司名包含“${company}”的岗位将从列表隐藏，并跳过后续采集和评分。岗位记录和投递状态保留。\n可在“我的设置 → 我的简历和我 → 不想去的公司”里解除，解除后恢复显示。\n\n确认保存？`)) {
        setNotice('取消了，设置没动。')
        return
      }
      // 你看预览的时候，名单可能在别的地方被改过，别用旧的盖掉新的。
      const latest = await readRules()
      if (JSON.stringify(latest) !== JSON.stringify(current)) {
        setBlockedCompanies(latest)
        throw new Error('屏蔽名单变了，请重新点“屏蔽公司”看最新的预览。')
      }
      const result = await postJobAction('/api/agent/config/apply', { preferences, confirm: true })
      setBlockedCompanies(result.preferences.blocked_companies)
      window.dispatchEvent(new Event('jianli-lieshou-config-saved'))
      setNotice(`已经屏蔽了“${company}”，相关的岗位从列表里藏起来了，记录还留着；解除屏蔽就恢复。`)
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : '屏蔽没成功，再试一次。')
    } finally {
      blockingCompanyRef.current = false
      setBlockingJobId(null)
    }
  }

  const markManuallySent = async (job: Job) => {
    if (job.source_platform !== 'zhilian' && job.source_platform !== '51job' && job.source_platform !== 'liepin') return
    const platformLabel = PLATFORM_LABELS[job.source_platform]
    if (!window.confirm(`请确认：你已经在${platformLabel}投过这个岗位了。这只是改一下这里的记录，不会往平台发任何东西。`)) return
    try {
      const result = await postJobAction('/api/jobs/manual-sent', {
        job_ids: [job.id],
        confirmed: true,
      })
      refreshJobs()
      setNotice(
        result.affected_count
          ? `已将 ${platformLabel} 记成“已发送”。`
          : `这个岗位之前已经记成“已发送”。`
      )
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : '没标记上')
    }
  }

  const changeJobStatus = async (job: Job, status: string) => {
    if (!window.confirm(`确定要把“${job.company} ${job.title}”的状态改成“${getStatusLabel(status)}”吗？`)) return
    try {
      await updateJobStatus(job.id, status)
      refreshJobs()
      setNotice('状态改好了。')
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : '状态没改成')
    }
  }

  const deliverSelectedJobs = async () => {
    if (!selectedIds.length) return
    const count = selectedIds.length
    if (!window.confirm(`确定要投选中的 ${count} 个岗位吗？四个平台的都能投，但每天能投的数量是有限的。`)) return
    try {
      const result = await postJobAction('/api/workbench/deliver', { job_ids: selectedIds })
      setSelectedIds([])
      refreshJobs()
      setNotice(
        result.already_queued_count === count
          ? `所选 ${count} 个岗位已在当前发送队列中，投出去后会出现在投递跟进里。`
          : result.queued_count
            ? `已将 ${result.queued_count} 个岗位追加到当前发送队列，投出去后会出现在投递跟进里。`
            : `已确认投递 ${count} 个了，系统会按顺序慢慢投，投出去后会出现在投递跟进里。`
      )
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : '投递没成功')
    }
  }

  const restoreJobs = async (jobIds: string[]) => {
    if (!jobIds.length || !window.confirm(`确定要把 ${jobIds.length} 个岗位从回收站拿回来吗？拿回来不会自动打分、也不会自动投。`)) return
    try {
      const result = await postJobAction('/api/jobs/restore', { job_ids: jobIds, confirmed: true })
      setRecycleSelectedIds(previous => previous.filter(id => !jobIds.includes(id)))
      refreshJobs()
      await loadRecycleBin()
      setNotice(`已经拿回来了 ${result.affected_count || 0} 个岗位。`)
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : '没拿回来')
    }
  }

  const requestPermanentDelete = (jobIds: string[]) => {
    if (!jobIds.length) return
    setPermanentDeleteIds(jobIds)
    setPermanentDeleteAcknowledged(false)
  }

  const confirmPermanentDelete = async () => {
    if (!permanentDeleteIds.length || !permanentDeleteAcknowledged) return
    try {
      const result = await postJobAction('/api/jobs/permanent-delete', {
        job_ids: permanentDeleteIds,
        confirmed: true,
        confirmation: 'PERMANENT_DELETE',
      })
      setRecycleSelectedIds(previous => previous.filter(id => !permanentDeleteIds.includes(id)))
      setPermanentDeleteIds([])
      setPermanentDeleteAcknowledged(false)
      await loadRecycleBin()
      setNotice(`已经彻底删掉了 ${result.affected_count || 0} 个岗位。`)
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : '没删掉')
    }
  }

  const exportJobs = async (format: 'xlsx' | 'csv', scope: 'all' | 'filtered' | 'selected') => {
    try {
      const res = await fetch('/api/jobs/export', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          format,
          scope,
          job_ids: scope === 'selected' ? selectedIds : [],
          filters: scope === 'filtered' ? {
            q: filters.query.trim(),
            min_score: filters.minScore,
            salary_min: filters.salaryMin,
            salary_max: filters.salaryMax,
            status: filters.status,
            created_within: filters.createdWithin,
            source_platform: filters.sourcePlatform,
            city: filters.city,
            education: filters.education,
            recruitment_type: filters.recruitmentType,
          } : {},
        }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.error || '没导出来')
      }
      const blob = await res.blob()
      const disposition = res.headers.get('Content-Disposition') || ''
      const filename = disposition.match(/filename="?([^";]+)"?/i)?.[1] || `jianli-lieshou-jobs.${format}`
      const url = window.URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = filename
      anchor.click()
      window.URL.revokeObjectURL(url)
      const exportedCount = Number(res.headers.get('X-Exported-Count'))
      setNotice(`已经导出来了 ${Number.isFinite(exportedCount) ? exportedCount : 0} 条岗位。`)
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : '导出失败')
    }
  }

  const startScoring = async (options: {
    scope: 'pending' | 'failed' | 'selected' | 'all_scored'
    limit: number | null
    job_ids: string[]
    force_rescore: boolean
    force?: boolean
  }): Promise<{ ok: boolean; error?: string }> => {
    const res = await fetch('/api/scoring/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ options, force: options.force ?? false }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      if (res.status === 409 && data.code === 'scoring_run_paused' && !options.force) {
        const confirmed = window.confirm(
          `有个打分任务还没完：${data.error || ''}\n要先结束它，再开始新的打分吗？（已经打好的分都留着）`,
        )
        if (confirmed) {
          return await startScoring({ ...options, force: true })
        }
      }
      const checks = Array.isArray(data.messages) ? data.messages.join('；') : ''
      return { ok: false, error: [data.error || '打分没启动', checks].filter(Boolean).join('：') }
    }
    setNotice(`打分开始了，一共 ${data.run?.remaining_job_ids?.length || 0} 个岗位。`)
    await refreshWorkbench()
    return { ok: true }
  }

  const startQuickScoring = async () => {
    // v38 根因修：以前这里写死 scope 'pending'、job_ids 空数组——点的
    // 是“给这些岗位打分”，实际却把当前全部未打分岗位都打了（选 15 个
    // 却跑出 25 个）。现在严格按勾选走：只打选中的，没选的不打；没选
    // 中时按钮直接禁用，不静默扩充成“全部未打分”。
    // v46：快捷按钮也重打已打过分的岗位（lucky 2026-11-24 01:40 定，
    // 没有预览，直接重打会再花 AI 调用，确认框里写清楚）；先拿预览看
    // 有多少个真能打——选中的都已在投时直接明说，不悄悄没动静（v45
    // 上选 1 个 81 分岗位点了确定却什么都没发生，就是 force_rescore
    // 为 false 把已打过分的跳过、符合条件的 0 个）。
    if (!selectedIds.length) return
    setQuickScoring(true)
    try {
      const previewRes = await fetch('/api/scoring/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ options: { scope: 'selected', limit: null, job_ids: [...selectedIds], force_rescore: true } }),
      })
      const preview = await previewRes.json().catch(() => ({}))
      if (!previewRes.ok) {
        setNotice(preview.error || '打分预览没拿到，请再点一次试试。')
        return
      }
      const eligible = Number(preview.eligible_jobs) || 0
      const skipped = Number(preview.skipped_jobs) || 0
      if (!eligible) {
        setNotice(`选中的 ${selectedIds.length} 个岗位已经在投了，不用再打。`)
        return
      }
      if (!window.confirm(
        `要给选中的 ${eligible} 个岗位打分吗？只打勾选的这几个，没选的不打；已经打过分的会重新打一遍。`
        + (skipped ? `选中的另外 ${skipped} 个已经在投了，不打。` : ''),
      )) return
      const result = await startScoring({ scope: 'selected', limit: null, job_ids: [...selectedIds], force_rescore: true })
      if (!result.ok) {
        setNotice(result.error === '没有符合条件的待评分岗位' ? '选中的岗位已经在投了，不用再打。' : (result.error || '打分没启动'))
      }
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : '打分没启动')
    } finally {
      setQuickScoring(false)
    }
  }

  if (showRecycleBin) {
    return (
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <Button variant="ghost" size="sm" onClick={() => setShowRecycleBin(false)}>回找工作</Button>
          <Button variant="secondary" size="sm" onClick={() => void loadRecycleBin()} disabled={recycleLoading}>刷新回收站</Button>
        </div>
        {notice && <div className="rounded-xl bg-secondary px-4 py-3 text-sm text-primary">{notice}</div>}
        <RecycleBinPanel
          jobs={recycleJobs}
          selectedIds={recycleSelectedIds}
          loading={recycleLoading}
          onToggleSelected={id => setRecycleSelectedIds(previous => previous.includes(id) ? previous.filter(item => item !== id) : [...previous, id])}
          onSelectAll={setRecycleSelectedIds}
          onRestore={ids => void restoreJobs(ids)}
          onPermanentDelete={requestPermanentDelete}
        />
        {permanentDeleteIds.length > 0 && (
          <ModalPortal>
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-4" role="dialog" aria-modal="true">
            <div className="flex max-h-[calc(100dvh-2rem)] w-full max-w-lg flex-col overflow-hidden rounded-3xl border border-danger-border bg-card shadow-2xl">
              <div data-testid="permanent-delete-body" className="min-h-0 flex-1 overflow-y-auto p-6">
              <div className="flex items-start gap-3"><AlertTriangle className="mt-0.5 h-6 w-6 shrink-0 text-danger" /><div><h3 className="text-xl font-black">真的要彻底删掉吗</h3><p className="mt-2 text-sm leading-6 text-muted">这 {permanentDeleteIds.length} 个岗位和它们的记录会彻底删掉，找不回来。已经投过、有来往记录的删不掉。</p></div></div>
              <label className="mt-5 flex cursor-pointer items-start gap-3 rounded-2xl border border-danger-border bg-danger-soft p-3 text-sm font-bold"><input type="checkbox" checked={permanentDeleteAcknowledged} onChange={event => setPermanentDeleteAcknowledged(event.target.checked)} className="mt-0.5 h-4 w-4 accent-danger" /><span>我想好了，删掉吧，删了就找不回来了。</span></label>
              </div>
              <div className="flex shrink-0 justify-end gap-3 border-t border-card-border px-6 py-4"><Button variant="secondary" size="sm" onClick={() => setPermanentDeleteIds([])}>取消</Button><Button variant="destructive" size="sm" disabled={!permanentDeleteAcknowledged} onClick={() => void confirmPermanentDelete()}>彻底删除</Button></div>
            </div>
          </div>
          </ModalPortal>
        )}
      </div>
    )
  }

  return (
    <div className="animate-fade-up rounded-3xl border border-card-border bg-card p-4">
      <div className="mb-3 flex flex-wrap items-center justify-end gap-2">
        <Button variant="secondary" size="sm" onClick={() => { setShowRecycleBin(true); void loadRecycleBin() }}><Trash2 className="mr-1 h-4 w-4" />回收站（{recycleJobs.length}）</Button>
        <BriefcaseBusiness className="h-6 w-6 text-primary" />
      </div>
      <JobFilterBar
        filters={filters}
        onChange={setFilters}
        onReset={() => setFilters({ ...EMPTY_JOB_FILTERS })}
        resultCount={total}
        totalCount={allTotal}
        invalidSalary={hasInvalidSalaryRange(filters)}
        showStatus
        showSource
        collapsible
        cityOptions={cityOptions}
        statusOptions={Object.entries(STATUS_LABELS)
          .filter(([value]) => !isAppliedJobStatus(value))
          .map(([value, label]) => ({ value, label }))}
      />
      <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
        <Button variant="secondary" size="sm" disabled={!items.length} onClick={toggleCurrentPage}>
          {allPageSelected ? '这页都不选' : '这页全选'}
        </Button>
        <Button variant="secondary" size="sm" disabled={!total || selectingAll} onClick={() => { allSelected ? cancelSelectAll() : void selectAllJobs() }}>
          {selectingAll ? '正在选…' : allSelected ? '取消选中全部岗位' : '选中全部岗位'}
        </Button>
        <span className="rounded-full bg-secondary px-3 py-2 font-bold text-primary">已选 {selectedIds.length} 个</span>
        {selectedIds.length > 0 && <Button variant="ghost" size="sm" onClick={() => setSelectedIds([])}>取消选择</Button>}
        <Button variant="destructive" size="sm" disabled={!selectedIds.length} onClick={() => void softDelete(selectedIds)}>扔进回收站</Button>
        <Button size="sm" disabled={!selectedIds.length} onClick={() => void deliverSelectedJobs()} data-tour="deliver-selected">
          <Send className="mr-1 h-4 w-4" />把选中的都投了
        </Button>
        <Button size="sm" onClick={() => void startQuickScoring()} disabled={quickScoring || !total || !selectedIds.length} title={selectedIds.length ? undefined : '先勾选要打分的岗位'}>
          {quickScoring ? '正在开始打分…' : '给选中的岗位打分'}
        </Button>
        <Button variant="secondary" size="sm" onClick={() => setShowScoreDialog(true)} data-tour="score-open">打分设置</Button>
        <ExportMenu onExport={exportJobs} hasSelection={selectedIds.length > 0} hasFiltered={total > 0} />
      </div>
      {notice && <div className="mb-4 rounded-xl bg-secondary px-4 py-3 text-sm text-primary">{notice}</div>}
      <div className="mb-3">
        <TaskPhaseProgress task={deliveryWorkbench.task || deliveryWorkbench.last_task} />
        <p className="mt-2 text-xs text-muted">
          岗位与评分进度每 5 秒自动刷新；每完成一个岗位后更新评分计数。
          {jobsRefreshedAt && ` 岗位更新于 ${jobsRefreshedAt.toLocaleTimeString('zh-CN', { hour12: false })}`}
        </p>
        {progressError && <p role="alert" className="mt-2 text-xs text-danger">{progressError}</p>}
      </div>
      {deliveryTask && (
        <div className="mb-4 rounded-2xl border border-card-border bg-surface p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <div className="text-sm font-black">投递队列</div>
              <p className="mt-1 text-xs text-muted">展示四个平台的投递任务；投递进度与结果在左侧“投递跟进”里查看。</p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Link to="/monitor" className="rounded-full bg-primary px-3 py-1 text-xs font-black text-primary-foreground">去投递跟进看看</Link>
              <span className="rounded-full bg-secondary px-3 py-1 text-xs font-black text-primary">
                {deliveryTask.status === 'running' ? '处理中' : deliveryTask.status === 'completed' ? '已完成' : deliveryTask.status === 'failed' ? '失败' : deliveryTask.status}
              </span>
            </div>
          </div>
          <div className="mt-3 rounded-xl border border-card-border bg-card px-3 py-2 text-sm">
            <div className="font-bold">{deliveryTask.logs?.[deliveryTask.logs.length - 1] || '队列已创建，等待执行'}</div>
            <div className="mt-1 text-xs text-muted">任务 ID：{deliveryTask.id}</div>
          </div>
        </div>
      )}
      {error && <div className="mb-4 rounded-xl border border-danger-border bg-danger-soft px-4 py-3 text-sm text-danger">{error}</div>}
      <JobsTable
        jobs={items}
        page={page}
        pageSize={pageSize}
        total={total}
        onPageChange={setPage}
        selectedIds={selectedIds}
        onToggleSelected={toggleSelected}
        onSoftDelete={job => void softDelete([job.id])}
        onMarkManuallySent={job => void markManuallySent(job)}
        onStatusChange={changeJobStatus}
        onBlockCompany={job => void blockCompany(job)}
        blockedCompanies={blockedCompanies}
        blockingJobId={blockingJobId}
        loading={loading}
        sortBy={sortBy}
        sortOrder={sortOrder}
        onSortChange={changeSort}
        threshold={scoreThreshold}
      />
      <ScoreJobsDialog
        open={showScoreDialog}
        selectedJobIds={selectedIds}
        onClose={() => setShowScoreDialog(false)}
        onStart={startScoring}
      />
    </div>
  )
}

function ExportMenu({
  onExport,
  hasSelection,
  hasFiltered,
}: {
  onExport: (format: 'xlsx' | 'csv', scope: 'all' | 'filtered' | 'selected') => void
  hasSelection: boolean
  hasFiltered: boolean
}) {
  const [format, setFormat] = useState<'xlsx' | 'csv'>('xlsx')
  return (
    <div className="ml-auto flex flex-wrap items-center gap-2">
      <select
        value={format}
        onChange={event => setFormat(event.target.value as 'xlsx' | 'csv')}
        className="rounded-xl border border-card-border bg-card px-2 py-2 text-xs outline-none focus:border-primary"
      >
        <option value="xlsx">XLSX</option>
        <option value="csv">CSV</option>
      </select>
      <Button variant="secondary" size="sm" disabled={!hasFiltered} onClick={() => onExport(format, 'filtered')}>导出筛选结果</Button>
      <Button variant="secondary" size="sm" disabled={!hasSelection} onClick={() => onExport(format, 'selected')}>导出所选岗位</Button>
      <Button variant="secondary" size="sm" onClick={() => onExport(format, 'all')}>导出全部岗位</Button>
    </div>
  )
}

type MonitorFilter = 'pending' | 'resume' | 'follow_up' | 'replied' | 'rejected'
const REPLY_RESOLUTION_ACTIONS = ['reply_dismissed', 'replied', 'auto_replied']
const DETECTION_RESOLUTION_ACTIONS = [
  ...REPLY_RESOLUTION_ACTIONS,
  'reply_pending',
  'needs_resume',
  'resume_failed',
  'resume_sent',
  'rejected',
  'hr_rejected',
]

function uniqueLatestByJob(items: HistoryItem[]) {
  const seen = new Set<string>()
  return items.filter(item => {
    const key = item.job_id || `${item.company}-${item.title}-${item.action}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

function sameHistoryJob(left: HistoryItem, right: HistoryItem) {
  if (left.job_id && right.job_id) return left.job_id === right.job_id
  return left.company === right.company && left.title === right.title
}

function isReplyPendingResolved(item: HistoryItem, history: HistoryItem[]) {
  return history.some(candidate =>
    candidate.id !== item.id
    && sameHistoryJob(item, candidate)
    && REPLY_RESOLUTION_ACTIONS.includes(candidate.action)
    && candidate.created_at >= item.created_at
  )
}

function isResumeFailureResolved(item: HistoryItem, history: HistoryItem[]) {
  return Boolean(item.resolved || item.resume_path) || history.some(candidate =>
    candidate.id > item.id
    && sameHistoryJob(item, candidate)
    && (candidate.action === 'needs_resume' || candidate.action === 'resume_sent' || candidate.action === 'resume_failed_dismissed')
  )
}

function isDetectedReplyResolved(item: HistoryItem, history: HistoryItem[]) {
  return history.some(candidate =>
    candidate.id > item.id
    && sameHistoryJob(item, candidate)
    && DETECTION_RESOLUTION_ACTIONS.includes(candidate.action)
  )
}

function isResumeRequestResolved(item: HistoryItem, history: HistoryItem[]) {
  return history.some(candidate =>
    candidate.id > item.id
    && sameHistoryJob(item, candidate)
    && candidate.action === 'resume_sent'
  )
}

function isOutboundReplyRecord(item: HistoryItem) {
  if (item.action === 'resume_sent') return true
  if (item.action === 'auto_replied') return true
  return item.action === 'replied' && parseHistoryDetail(item).schema.startsWith('replied.')
}

type MonitorConversationMessage = {
  sender: 'hr' | 'ai'
  text: string
}

function monitorConversationMessages(item: HistoryItem, history: HistoryItem[]): MonitorConversationMessage[] {
  const parsed = parseHistoryDetail(item)
  const pendingItem = parsed.pendingHistoryId
    ? history.find(candidate => candidate.id === parsed.pendingHistoryId)
    : undefined
  const pendingParsed = pendingItem ? parseHistoryDetail(pendingItem) : null
  const resumeRequestItem = item.action === 'resume_sent'
    ? history
      .filter(candidate =>
        candidate.id < item.id
        && candidate.action === 'needs_resume'
        && sameHistoryJob(item, candidate)
      )
      .sort((left, right) => right.id - left.id)[0]
    : undefined
  const resumeRequestParsed = resumeRequestItem ? parseHistoryDetail(resumeRequestItem) : null
  const source = parsed.conversationTail.length
    ? parsed
    : (pendingParsed || resumeRequestParsed || parsed)
  const messages: MonitorConversationMessage[] = []

  const append = (sender: 'hr' | 'ai', text: string) => {
    const normalizedText = text.trim()
    if (!normalizedText) return
    const previous = messages[messages.length - 1]
    if (previous?.sender === sender && previous.text === normalizedText) return
    messages.push({ sender, text: normalizedText })
  }

  source.conversationTail.forEach(message => {
    if (message.sender === 'hr') append('hr', message.text)
    if (message.sender === 'me') append('ai', message.text)
  })

  const hrQuestion = parsed.hrQuestion || source.hrQuestion
  if (!messages.some(message => message.sender === 'hr')) append('hr', hrQuestion)
  if (item.action !== 'resume_sent' && isOutboundReplyRecord(item) && !parsed.schema.startsWith('replied.external.')) {
    append('ai', parsed.aiReply)
  }

  return messages
}

function MonitorExecutionView({
  history,
  refresh,
  refreshing,
  lastRefreshedAt,
  onCheckReplies,
  tasks,
  deliveryJobs,
  onPauseTask,
  onResumeTask,
  onStopTask,
  onSkipWaitTask,
  skipWaitBusyId,
  onForceStopTask,
  forceStopVisible,
}: {
  history: HistoryItem[]
  refresh: () => Promise<void>
  refreshing: boolean
  lastRefreshedAt: Date | null
  /** “再查一次 HR 回复”：复用首页同一启动流程，返回一句反馈给面板显示。 */
  onCheckReplies?: () => Promise<string>
  /** 投递任务列表：最近的投递任务快照 + 最近走过投递流程的岗位。 */
  tasks: WorkbenchTask[]
  deliveryJobs: DeliveryJob[]
  /** 投递任务卡片上的暂停 / 继续 / 停下 / 强制停止：入口在卡片上，动作由首页同一套接口做。 */
  onPauseTask: (taskId: string) => void
  onResumeTask: (taskId: string) => void
  onStopTask: (taskId: string) => void
  /** v54 图10：投递任务卡片上的跳过等待（只跳当前等待，不停任务）。 */
  onSkipWaitTask?: (taskId: string) => void
  skipWaitBusyId?: string | null
  onForceStopTask: () => void
  forceStopVisible: boolean
}) {
  const pendingReplies = uniqueLatestByJob(history.filter(item =>
    item.action === 'reply_pending' && !isReplyPendingResolved(item, history)
  ))
  const detectedReplies = uniqueLatestByJob(history.filter(item =>
    item.action === 'hr_reply_detected' && !isDetectedReplyResolved(item, history)
  ))
  const resumeFailures = uniqueLatestByJob(history.filter(item =>
    item.action === 'resume_failed' && !isResumeFailureResolved(item, history)
  ))
  const pendingItems = uniqueLatestByJob(
    [...detectedReplies, ...pendingReplies, ...resumeFailures].sort((left, right) => right.id - left.id)
  )
  const pendingResumeRequests = history.filter(item =>
    item.action === 'needs_resume' && !isResumeRequestResolved(item, history)
  )
  const resumeRequests = uniqueLatestByJob(
    [...pendingResumeRequests, ...resumeFailures].sort((left, right) => right.id - left.id)
  )
  const followUpRecords = uniqueLatestByJob(history.filter(item => item.action === 'follow_up_sent'))
  const repliedRecords = history.filter(isOutboundReplyRecord)
  const rejectedRecords = uniqueLatestByJob(history.filter(item => item.action === 'hr_rejected'))
  const [activeMonitorFilter, setActiveMonitorFilter] = useState<MonitorFilter>('pending')
  const visibleHistory = activeMonitorFilter === 'resume'
    ? resumeRequests
    : activeMonitorFilter === 'follow_up'
      ? followUpRecords
      : activeMonitorFilter === 'replied'
        ? repliedRecords
        : activeMonitorFilter === 'rejected'
          ? rejectedRecords
          : pendingItems
  const displayedHistory = activeMonitorFilter === 'pending' || activeMonitorFilter === 'resume'
    ? visibleHistory
    : visibleHistory.slice(0, 8)
  const [replyDrafts, setReplyDrafts] = useState<Record<number, string>>({})
  const [notice, setNotice] = useState('')
  const [openingChatId, setOpeningChatId] = useState<number | null>(null)
  const [preparingReplyId, setPreparingReplyId] = useState<number | null>(null)

  const draftFor = (item: HistoryItem) => {
    const parsed = parseHistoryDetail(item)
    return replyDrafts[item.id] ?? parsed.aiReply ?? item.detail ?? ''
  }

  const sendManualReply = async (item: HistoryItem) => {
    try {
      const res = await fetch(`/api/history/${item.id}/reply`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: draftFor(item) }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.error || '回复失败')
      }
      await refresh()
      setNotice('回复已记录，请在招聘平台手动发送。')
    } catch (err) {
      setNotice(err instanceof Error ? err.message : '回复失败')
    }
  }

  const dismissPendingReply = async (item: HistoryItem) => {
    if (!window.confirm('确定放弃这条待回复建议吗？放弃后不会发送消息，也不会把岗位标记为拒绝。')) return
    try {
      const res = await fetch(`/api/history/${item.id}/dismiss`, { method: 'POST' })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.error || '放弃失败')
      }
      await refresh()
      setNotice('已放弃这条待回复建议。')
    } catch (err) {
      setNotice(err instanceof Error ? err.message : '放弃失败')
    }
  }

  const openMonitorConversation = async (item: HistoryItem) => {
    const platform = item.source_platform || 'boss'
    if (platform !== 'boss') {
      const targetUrl = monitorChatUrl(item)
      if (targetUrl) window.open(targetUrl, '_blank', 'noopener,noreferrer')
      return
    }
    setOpeningChatId(item.id)
    try {
      const res = await fetch(`/api/history/${item.id}/open-chat`, { method: 'POST' })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(data.error || '聊天定位失败')
      setNotice('已在 BOSS 中定位到对应聊天对话。')
    } catch (err) {
      setNotice(err instanceof Error ? err.message : '聊天定位失败')
    } finally {
      setOpeningChatId(null)
    }
  }

  const prepareDetectedReply = async (item: HistoryItem) => {
    setPreparingReplyId(item.id)
    try {
      const res = await fetch(`/api/history/${item.id}/prepare-reply`, { method: 'POST' })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(data.error || '对话读取失败')
      await refresh()
      setNotice(data.already_processed ? '这条消息已经处理。' : '完整对话已读取，请检查生成的处理结果。')
    } catch (err) {
      setNotice(err instanceof Error ? err.message : '对话读取失败')
    } finally {
      setPreparingReplyId(null)
    }
  }

  const retryResumeGeneration = async (item: HistoryItem) => {
    if (!window.confirm('确定要重新做这份定制简历吗？需要打分服务和浏览器都正常。')) return
    try {
      const res = await fetch(`/api/history/${item.id}/resume-retry`, { method: 'POST' })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.error || '重试生成失败')
      }
      await refresh()
      setNotice('定制简历已经重新做好了，请到首页“HR 找你要简历了”那里下载发过去。')
    } catch (err) {
      setNotice(err instanceof Error ? err.message : '重试生成失败')
    }
  }

  const dismissResumeFailure = async (item: HistoryItem) => {
    if (!window.confirm('确定放弃这条简历生成失败记录吗？放弃后将不再出现在待处理中。')) return
    try {
      const res = await fetch(`/api/history/${item.id}/resume-dismiss`, { method: 'POST' })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.error || '忽略失败')
      }
      await refresh()
      setNotice('已放弃这条简历生成失败记录。')
    } catch (err) {
      setNotice(err instanceof Error ? err.message : '忽略失败')
    }
  }

  return (
    <div className="animate-fade-up rounded-3xl border border-card-border bg-card p-5">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="text-2xl font-black">投递跟进</h2>
          <p className="mt-1 text-sm text-muted">投出去的岗位，HR 回了消息都在这儿处理。全部已投出的岗位在下面「已投出的岗位」里，能按公司、职位、平台、时间找。</p>
          <a href="#applied-jobs" className="mt-1 inline-block text-sm font-bold text-primary hover:underline">看全部已投出的岗位 ↓</a>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-2">
          <span className="text-xs text-muted">
            {lastRefreshedAt ? `${lastRefreshedAt.toLocaleTimeString('zh-CN', { hour12: false })} 更新的` : '正在读取'} · 每 2 秒自动刷新
          </span>
          <Button variant="secondary" size="sm" onClick={refresh} disabled={refreshing}>
            <RefreshCw className={`mr-2 h-4 w-4 ${refreshing ? 'animate-spin' : ''}`} />
            {refreshing ? '刷新中' : '马上刷新'}
          </Button>
          <span className="rounded-full bg-secondary px-3 py-2 text-xs font-black text-primary">等你处理 {pendingItems.length}</span>
        </div>
      </div>
      <DeliveryTaskList
        tasks={tasks}
        deliveryJobs={deliveryJobs}
        onPause={onPauseTask}
        onResume={onResumeTask}
        onStop={onStopTask}
        onSkipWait={onSkipWaitTask}
        skipWaitBusyId={skipWaitBusyId}
        onForceStop={onForceStopTask}
        forceStopVisible={forceStopVisible}
      />
      <div className="mb-4 flex flex-wrap gap-2">
        {[
          { key: 'pending' as const, label: '等你处理', count: pendingItems.length },
          { key: 'resume' as const, label: 'HR 要简历', count: resumeRequests.length },
          { key: 'follow_up' as const, label: '已自动跟进', count: followUpRecords.length },
          { key: 'replied' as const, label: '已回复了', count: repliedRecords.length },
          { key: 'rejected' as const, label: 'HR 已拒绝', count: rejectedRecords.length },
        ].map(item => {
          const active = activeMonitorFilter === item.key
          return (
            <button
              key={item.key}
              type="button"
              onClick={() => setActiveMonitorFilter(item.key)}
              className={`rounded-full px-3 py-1 text-xs font-bold transition ${active ? 'bg-primary text-primary-foreground' : 'border border-card-border text-muted hover:border-primary/60 hover:text-primary'}`}
            >
              {item.label} {item.count}
            </button>
          )
        })}
      </div>
      {notice && <div className="mb-3 rounded-2xl bg-secondary px-4 py-3 text-sm text-primary">{notice}</div>}
      <div className="space-y-3">
        {displayedHistory.map((item, index) => {
          const canReply = item.action === 'reply_pending'
          const isFollowUp = item.action === 'follow_up_sent'
          const isDetectedReply = item.action === 'hr_reply_detected'
          const isResumeFailure = item.action === 'resume_failed'
          const isResumeRequest = item.action === 'needs_resume' || isResumeFailure
          const isReplied = isOutboundReplyRecord(item)
          const parsed = parseHistoryDetail(item)
          const hasGeneratedReply = !isDetectedReply && Boolean(parsed.aiReply)
          const detectedReplyPreview = isDetectedReply ? item.detail.replace(/^HR回复:\s*/, '') : ''
          const conversationMessages = monitorConversationMessages(item, history)
          const showReplyContent = canReply || Boolean(parsed.hrQuestion) || hasGeneratedReply || isResumeRequest || isReplied || conversationMessages.length > 0
          const systemFailureReason = parsed.systemReason || (isResumeFailure ? '未获得更具体的错误信息，请查看运行日志。' : '')
          const targetUrl = monitorChatUrl(item)
          const openingChat = openingChatId === item.id
          const preparingReply = preparingReplyId === item.id
          const canOpenChat = (item.source_platform || 'boss') === 'boss' ? Boolean(item.id) : Boolean(targetUrl)
          return (
            <div key={item.id || `${item.created_at}-${index}`} className="grid gap-3 rounded-2xl border border-card-border bg-surface p-4 lg:grid-cols-[130px_1fr_160px]">
              <div className="text-xs text-muted">
                <div>{item.created_at}</div>
                <div className="mt-2 rounded-full bg-card px-2 py-1 text-center font-bold text-primary">{getActionLabel(item.action)}</div>
              </div>
              <div>
                <div className="font-black">{item.company || '岗位'}｜{item.title || '监测记录'}</div>
                {isDetectedReply ? (
                  <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-muted">{detectedReplyPreview}</p>
                ) : showReplyContent ? (
                  <div className="mt-3 space-y-3">
                    {isFollowUp && (
                      <div>
                        <div className="text-xs font-black text-primary">为什么跟进了一次</div>
                        <p className="mt-1 whitespace-pre-wrap text-sm leading-6 text-muted">
                          HR 好久没回，就自动帮你问了一次。
                        </p>
                      </div>
                    )}
                    {conversationMessages.length > 0 && (
                      <div className="overflow-hidden rounded-2xl border border-card-border bg-card">
                        <div className="flex items-center justify-between border-b border-card-border px-3 py-1.5">
                          <span className="text-xs font-black text-foreground">聊天记录</span>
                        </div>
                        <div className="max-h-[260px] divide-y divide-card-border overflow-y-auto overscroll-contain">
                          {conversationMessages.map((message, messageIndex) => {
                            const fromHr = message.sender === 'hr'
                            return (
                              <div key={`${item.id}-${messageIndex}-${message.sender}`} className="grid grid-cols-[28px_minmax(0,1fr)] items-start gap-2 px-3 py-1.5">
                                <div className={`flex h-7 w-7 items-center justify-center rounded-full text-[10px] font-black ${fromHr ? 'bg-secondary text-primary' : 'bg-success-soft text-success'}`}>
                                  {fromHr ? 'HR' : 'AI'}
                                </div>
                                <p className="min-w-0 whitespace-pre-wrap break-words text-[13px] leading-5 text-foreground">{message.text}</p>
                              </div>
                            )
                          })}
                        </div>
                      </div>
                    )}
                    {isResumeFailure && (
                      <div className="rounded-2xl border border-danger/30 bg-danger-soft p-3">
                        <div className="text-xs font-black text-danger">没弄成的原因</div>
                        <p className="mt-1 whitespace-pre-wrap text-sm leading-6 text-danger">{systemFailureReason}</p>
                      </div>
                    )}
                    {canReply ? (
                      <div>
                        <div className="mb-1 text-xs font-black text-primary">建议这样回（还没发）</div>
                        <textarea
                          id={`reply-draft-${item.id}`}
                          value={draftFor(item)}
                          onChange={event => setReplyDrafts(prev => ({ ...prev, [item.id]: event.target.value }))}
                          className="min-h-[92px] w-full rounded-2xl border border-card-border bg-card p-3 text-sm leading-6 text-foreground outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
                        />
                      </div>
                    ) : null}
                  </div>
                ) : (
                  <p className="mt-2 text-sm leading-6 text-muted">{item.detail || getActionLabel(item.action)}</p>
                )}
                {isDetectedReply ? (
                  <p className="mt-2 text-xs text-primary">HR 刚回了新消息，正在看完整对话，马上给你建议。</p>
                ) : item.action === 'hr_rejected' ? (
                  <p className="mt-2 text-xs text-muted">HR 回了拒绝，这单不用再跟了。</p>
                ) : canReply ? (
                  <p className="mt-2 text-xs text-primary">建议你看一眼再发。</p>
                ) : item.action === 'needs_resume' ? (
                  <p className="mt-2 text-xs text-primary">HR 找你要简历，定制好的简历已经准备好了，你手动发过去就行。</p>
                ) : item.action === 'resume_sent' ? (
                  <p className="mt-2 text-xs text-primary">定制简历已经发过去了，聊天记录在上面。</p>
                ) : isResumeFailure ? (
                  <p className="mt-2 text-xs text-danger">定制简历这次没做出来，还没有能下载的文件，你手动处理一下，或者等会儿再试。</p>
                ) : isReplied ? (
                  <p className="mt-2 text-xs text-primary">这轮的对话都在上面的聊天记录里。</p>
                ) : null}
              </div>
              <div className="grid gap-2">
                <div className="grid gap-2">
                  <Button
                    variant="secondary"
                    size="sm"
                    disabled={!canOpenChat || openingChat}
                    onClick={() => void openMonitorConversation(item)}
                  >
                    <ExternalLink className="mr-2 h-4 w-4" />{openingChat ? '正在找' : monitorLinkLabel(item)}
                  </Button>
                  {isDetectedReply ? (
                    <Button size="sm" disabled={preparingReply} onClick={() => void prepareDetectedReply(item)}>
                      {preparingReply ? '正在看' : '看看 HR 说了啥'}
                    </Button>
                  ) : canReply ? (
                    <>
                      <Button size="sm" onClick={() => sendManualReply(item)}><MessageCircle className="mr-2 h-4 w-4" />就这么回</Button>
                      <Button variant="secondary" size="sm" onClick={() => document.getElementById(`reply-draft-${item.id}`)?.focus()}>改改再回</Button>
                      <Button variant="secondary" size="sm" onClick={() => dismissPendingReply(item)}>不回了</Button>
                    </>
                  ) : isResumeFailure ? (
                    <>
                      <Button size="sm" variant="secondary" onClick={() => retryResumeGeneration(item)}><RefreshCw className="mr-2 h-4 w-4" />再做一次</Button>
                      <Button variant="secondary" size="sm" onClick={() => dismissResumeFailure(item)}><XCircle className="mr-2 h-4 w-4" />不管了</Button>
                    </>
                  ) : (
                    <div className="px-2 py-1 text-center text-xs text-muted">这轮已经处理完了</div>
                  )}
                </div>
              </div>
            </div>
          )
        })}
        {!visibleHistory.length && (
          <div className="rounded-2xl border border-dashed border-card-border bg-surface p-5 text-sm text-muted">
            {activeMonitorFilter === 'replied' ? '最近 7 天还没有回复过谁。' : '现在没有要处理的事，HR 回了消息会来这儿。'}
          </div>
        )}
      </div>
      <div className="mt-4">
        <AppliedJobsPanel onCheckReplies={onCheckReplies} />
      </div>
    </div>
  )
}
