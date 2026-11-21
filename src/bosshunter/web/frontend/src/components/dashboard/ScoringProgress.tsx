import type { ScoringCurrentJob, WorkbenchTask } from '@/hooks/useDashboard'

interface ScoringProgressProps {
  metrics?: Record<string, number | string>
  progress?: { scoring_current?: ScoringCurrentJob[] }
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
