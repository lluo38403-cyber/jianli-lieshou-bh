import type { ScoringCurrentJob, WorkbenchTask } from '@/hooks/useDashboard'

interface ScoringProgressProps {
  metrics?: Record<string, number | string>
  progress?: { scoring_current?: ScoringCurrentJob[]; deliver?: { sent?: number; failed?: number; total?: number } }
  status: string
}

function count(value: number | string | undefined): number {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? Math.max(0, Math.floor(parsed)) : 0
}

/** 在途岗位的尝试状态：二次复核、特殊重试（带原因）、第 X/Y 次重试。 */
export function scoringAttemptSuffix(job: ScoringCurrentJob): string {
  if (job.note) return `（${job.note}）`
  if (typeof job.attempt === 'number' && job.attempt > 1) {
    return typeof job.max_attempts === 'number' && job.max_attempts > 1
      ? `（第 ${job.attempt}/${job.max_attempts} 次尝试）`
      : `（第 ${job.attempt} 次尝试）`
  }
  return ''
}

/** “正在为第 X/N 份岗位打分：公司｜职位”，N 未知时不写份数。 */
export function formatScoringCurrentJob(job: ScoringCurrentJob, total: number): string {
  const prefix = total > 0 ? `正在为第 ${job.index}/${total} 份岗位打分` : '正在打分'
  return `${prefix}：${job.company}｜${job.title}${scoringAttemptSuffix(job)}`
}

/** Counts processed jobs, including filtered and failed jobs, rather than API requests. */
export function ScoringProgress({ metrics, progress, status }: ScoringProgressProps) {
  const total = count(metrics?.ai_total)
  const completed = total > 0 ? Math.min(count(metrics?.ai_completed), total) : 0
  const percent = total > 0 ? Math.floor(completed * 100 / total) : undefined
  const current = (progress?.scoring_current ?? []).filter((job) => job && typeof job.index === 'number')
  const running = ['running', 'stopping'].includes(status)
  const currentLine = running && current.length > 0
    ? formatScoringCurrentJob(current[0], total) + (current.length > 1 ? `（同时还在打另外 ${current.length - 1} 个）` : '')
    : ''
  const state = status === 'paused' ? '先停下了'
    : status === 'stopping' ? '正在停下'
    : status === 'stopped' ? '已经停了'
    : status === 'failed' ? '这次没打成'
    : status === 'completed_with_errors' ? '打完了，有几个没打好'
    : total > 0 && completed === total ? '这轮打完了'
    : status === 'completed' ? '已经结束了'
    : '正在打分'

  const emptyText = ['running', 'stopping'].includes(status) ? '正在准备要打分的岗位…' : '还没开始打分'

  return (
    <section className="mt-3 rounded-xl border border-card-border bg-surface px-3 py-3" aria-label="这轮打分进度">
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
        <span className="font-semibold">这轮打分 · {state}</span>
        <span className="tabular-nums text-muted">{total > 0 ? `已打 ${completed} / ${total} 个 · ${percent}%` : emptyText}</span>
      </div>
      <div
        role="progressbar"
        aria-label="打分进度"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        aria-valuetext={total > 0 ? `${state}，已处理 ${completed} / ${total} 个岗位，${percent}%` : `${state}，${emptyText}`}
        className="mt-2 h-2 overflow-hidden rounded-full bg-secondary"
      >
        <div className={`h-full rounded-full transition-[width] duration-500 motion-reduce:transition-none ${status === 'failed' ? 'bg-danger' : 'bg-primary'} ${percent === undefined && status === 'running' ? 'animate-pulse motion-reduce:animate-none' : ''}`} style={{ width: `${percent ?? (status === 'running' ? 15 : 0)}%` }} />
      </div>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
        <span>通过 {count(metrics?.ai_passed)} 个</span>
        <span>筛掉 {count(metrics?.ai_filtered)} 个</span>
        <span className={count(metrics?.ai_failed) > 0 ? 'text-danger' : undefined}>没打好 {count(metrics?.ai_failed)} 个</span>
        <span>按岗位数算，重试的不重复算；接着打的时候显示这轮还剩多少。</span>
      </div>
      {currentLine ? <p className="mt-2 truncate text-xs text-muted" title={currentLine}>{currentLine}</p> : null}
    </section>
  )
}

export function TaskScoringProgress({ task }: { task: WorkbenchTask | null }) {
  if (!task || (task.mode !== 'score' && task.mode !== 'rescore'
    && task.metrics?.ai_total === undefined && task.progress?.outcome !== 'scoring')) return null
  return <ScoringProgress metrics={task.metrics} progress={task.progress} status={task.status} />
}

/** v54 图8：投递阶段的主进度条——每投完一个岗位更新一次（后端
 * _record_send_progress），不再停留在打分进度。总数是本批要投的岗位
 * 数；失败的单独计数，不混进“已投”。 */
export function DeliverProgress({ metrics, progress, status }: ScoringProgressProps) {
  const deliver = progress?.deliver
  const total = deliver?.total !== undefined ? count(deliver.total) : count(metrics?.send_requested)
  const sent = deliver?.sent !== undefined ? count(deliver.sent) : count(metrics?.send_success)
  const failed = deliver?.failed !== undefined ? count(deliver.failed) : count(metrics?.send_failed)
  const percent = total > 0 ? Math.floor(Math.min(sent, total) * 100 / total) : undefined
  const state = status === 'paused' ? '先停下了'
    : status === 'stopping' ? '正在停下'
    : status === 'stopped' ? '已经停了'
    : status === 'failed' ? '这次没投成'
    : total > 0 && sent >= total ? '这轮投完了'
    : status === 'completed' ? '已经结束了'
    : '正在投递'
  const emptyText = ['running', 'stopping'].includes(status) ? '正在准备要投的岗位…' : '还没开始投递'

  return (
    <section className="mt-3 rounded-xl border border-card-border bg-surface px-3 py-3" aria-label="这轮投递进度">
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
        <span className="font-semibold">这轮投递 · {state}</span>
        <span className="tabular-nums text-muted">{total > 0 ? `已投 ${sent} / ${total} 个 · ${percent}%` : emptyText}</span>
      </div>
      <div
        role="progressbar"
        aria-label="投递进度"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        aria-valuetext={total > 0 ? `${state}，已投 ${sent} / ${total} 个岗位，${percent}%` : `${state}，${emptyText}`}
        className="mt-2 h-2 overflow-hidden rounded-full bg-secondary"
      >
        <div className={`h-full rounded-full transition-[width] duration-500 motion-reduce:transition-none ${status === 'failed' ? 'bg-danger' : 'bg-primary'} ${percent === undefined && status === 'running' ? 'animate-pulse motion-reduce:animate-none' : ''}`} style={{ width: `${percent ?? (status === 'running' ? 15 : 0)}%` }} />
      </div>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
        <span>已投 {sent} 个</span>
        {failed > 0 && <span className="text-danger">没投出去 {failed} 个</span>}
        <span>投完这批就去等 HR 回消息。</span>
      </div>
    </section>
  )
}

/** v54 图8：找岗位阶段的主进度条——总数事先不知道，只显示已看/新增
 * 数，进度条用呼吸动效表示“正在找”，不编一个假百分比。 */
export function CollectProgress({ metrics, status }: ScoringProgressProps) {
  const seen = count(metrics?.collect_seen)
  const added = count(metrics?.collect_new)
  const running = ['running', 'stopping'].includes(status)
  const state = status === 'paused' ? '先停下了'
    : status === 'stopping' ? '正在停下'
    : status === 'stopped' ? '已经停了'
    : status === 'failed' ? '这次没找成'
    : status === 'completed' ? '这轮找完了'
    : '正在找岗位'

  return (
    <section className="mt-3 rounded-xl border border-card-border bg-surface px-3 py-3" aria-label="找岗位进度">
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
        <span className="font-semibold">正在找岗位 · {state}</span>
        <span className="tabular-nums text-muted">{running || seen > 0 ? `已看 ${seen} 个，新增 ${added} 个` : '还没开始找'}</span>
      </div>
      <div
        role="progressbar"
        aria-label="找岗位进度"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuetext={`${state}，已看 ${seen} 个岗位，新增 ${added} 个`}
        className="mt-2 h-2 overflow-hidden rounded-full bg-secondary"
      >
        <div className={`h-full rounded-full bg-primary ${running ? 'animate-pulse motion-reduce:animate-none' : ''}`} style={{ width: running ? '35%' : '100%' }} />
      </div>
    </section>
  )
}

/** v54 图8：等 HR 回消息阶段——没有总数可画，就不画进度条，只留一句
 * 状态，免得一条假进度条在那儿转。 */
export function MonitorProgress({ status }: { status: string }) {
  const state = status === 'paused' ? '先停下了'
    : status === 'stopping' ? '正在停下'
    : status === 'stopped' ? '已经停了'
    : '正在等 HR 回消息'
  return (
    <section className="mt-3 rounded-xl border border-card-border bg-surface px-3 py-3" aria-label="等 HR 回消息">
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
        <span className="font-semibold">等 HR 回消息 · {state}</span>
        <span className="text-muted">有回复会第一时间显示在“投递跟进”里。</span>
      </div>
    </section>
  )
}

/** v54 图8：主进度条跟随当前阶段切——当前在干什么，最显眼的进度条
 * 就显示什么。没写阶段的老任务（重启前留下的）沿用以前的打分条，
 * 不凭空消失。 */
export function TaskPhaseProgress({ task }: { task: WorkbenchTask | null }) {
  if (!task) return null
  const phase = task.progress?.phase
  if (phase === 'delivering' || task.mode === 'deliver') {
    return <DeliverProgress metrics={task.metrics} progress={task.progress} status={task.status} />
  }
  if (phase === 'collecting' || task.mode === 'collect') {
    return <CollectProgress metrics={task.metrics} status={task.status} />
  }
  if (phase === 'monitoring' || task.mode === 'monitor') {
    return <MonitorProgress status={task.status} />
  }
  return <TaskScoringProgress task={task} />
}
