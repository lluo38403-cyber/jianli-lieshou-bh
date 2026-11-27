import { Link } from 'react-router-dom'
import type { DeliveryJob, WorkbenchTask } from '@/hooks/useDashboard'
import { PLATFORM_SHORT_LABELS } from '@/lib/platforms'

/** 只展示会投递的任务：确认投递、运行全流程（包含投递阶段）、单独监测
 * （监测期间也会投新确认的岗位）。采集 / 打分 / 招呼语任务不在这儿，
 * 它们在首页的任务面板里看。
 * 中途停下的任务（已停下、失败了）不留在这儿：它们已经结束了，占着
 * 列表只会把新任务往下挤；正在投、暂停中、刚投完的才留着。 */
const DELIVERY_MODES = new Set(['deliver', 'full', 'monitor'])
const HIDDEN_TASK_STATUSES = new Set(['stopped', 'failed'])

const TASK_STATUS_LABELS: Record<string, string> = {
  running: '投递中',
  paused: '已暂停',
  stopping: '正在停下',
  completed: '已完成',
  failed: '失败了',
  stopped: '已停下',
}

function taskStatusLabel(status: string): string {
  return TASK_STATUS_LABELS[status] ?? '未知状态'
}

function formatCount(value: unknown): number | null {
  const num = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(num) ? num : null
}

/** 每个岗位的投递结果：状态仍以 jobs.status 为准，这里只翻译成大白话，
 * 再把“走到哪一步”按确认 → 投出 / 失败的顺序写出来。
 * 只列还没投出去的岗位（已确认排队中、没投出去）：已经投出去的岗位
 * 由同一页下面的“已投出的岗位”分区展示，这里不再重复列一遍。 */
function jobResult(job: DeliveryJob, deliveryRunning: boolean): { label: string; detail: string; tone: 'ok' | 'bad' | 'wait' } {
  const reason = (job.last_error || job.step_detail || '').trim()
  switch (job.status) {
    case 'error':
      return { label: '没投出去', detail: reason || '没写原因，等着再试一次', tone: 'bad' }
    case 'approved':
      return deliveryRunning
        ? { label: '正在排队，等着投', detail: '已经确认了，正在按顺序投', tone: 'wait' }
        : { label: '已确认，等下次投递', detail: '已经确认了，下次投递时发出', tone: 'wait' }
    default:
      return { label: '未知状态', detail: '', tone: 'wait' }
  }
}

function jobFlow(job: DeliveryJob): string {
  if (job.status === 'error') return '已确认 → 没投出去'
  return '已确认 → 等着投'
}

export function DeliveryTaskList({
  tasks,
  deliveryJobs,
  onPause,
  onResume,
  onStop,
  onForceStop,
  forceStopVisible,
}: {
  tasks: WorkbenchTask[]
  deliveryJobs: DeliveryJob[]
  onPause?: (taskId: string) => void
  onResume?: (taskId: string) => void
  onStop?: (taskId: string) => void
  onForceStop?: () => void
  forceStopVisible?: boolean
}) {
  const deliveryTasks = tasks
    .filter(task => DELIVERY_MODES.has(task.mode) && !HIDDEN_TASK_STATUSES.has(task.status))
    .slice(0, 5)
  const runningCount = deliveryTasks.filter(task => task.status === 'running').length
  const deliveryRunning = deliveryTasks.some(task => task.status === 'running')
  // 还没投出去的岗位：已确认排队中、没投出去。已经投出去的（已投出 / HR 回了 /
  // 要简历 / 已问过一次）由同一页下面的“已投出的岗位”分区展示，这里不重复列。
  const pendingJobs = deliveryJobs.filter(job => job.status === 'approved' || job.status === 'error')
  const hasSentJobs = deliveryJobs.some(job => job.status !== 'approved' && job.status !== 'error')

  return (
    <section aria-label="投递任务" className="mb-4 rounded-2xl border border-card-border bg-surface p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="text-base font-black">投递任务</h3>
          <p className="mt-1 text-xs text-muted">正在投、刚投完的任务都在这儿；中途停下的任务已经结束，不会留在这儿。已经投出去的岗位在下面“已投出的岗位”里。</p>
        </div>
        {runningCount > 0 && (
          <span className="rounded-full bg-secondary px-3 py-1 text-xs font-black text-primary">正在投 {runningCount} 个任务</span>
        )}
      </div>

      {deliveryTasks.length === 0 ? (
        <p className="rounded-xl bg-card px-3 py-2 text-sm text-muted">
          最近没有投递任务。去<Link to="/jobs" className="font-bold text-primary underline">找工作</Link>选几个岗位点“把选中的都投了”，回来这里就能看到进度。
        </p>
      ) : (
        <ul className="space-y-2">
          {deliveryTasks.map(task => {
            const metrics = task.metrics ?? {}
            const requested = formatCount(metrics.send_requested)
            const success = formatCount(metrics.send_success)
            const failed = formatCount(metrics.send_failed)
            const deferred = formatCount(metrics.send_deferred)
            const lastLog = task.logs?.[task.logs.length - 1]
            const stopNote = task.stop_reason || (task.status === 'failed' ? task.error : '')
            return (
              <li key={task.id} className="rounded-xl border border-card-border bg-card px-3 py-2">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-sm font-black">{task.label}</span>
                  <span className="rounded-full bg-secondary px-2 py-1 text-[11px] font-black text-primary">{taskStatusLabel(task.status)}</span>
                </div>
                {(task.status === 'running' || task.status === 'paused' || task.status === 'stopping') && (
                  <div className="mt-2 flex flex-wrap gap-2">
                    {task.status === 'running' && (
                      <button
                        type="button"
                        className="rounded-full border border-card-border px-3 py-1 text-xs font-bold text-primary hover:border-primary/60"
                        onClick={() => onPause?.(task.id)}
                      >
                        暂停
                      </button>
                    )}
                    {task.status === 'paused' && (
                      <button
                        type="button"
                        className="rounded-full bg-primary px-3 py-1 text-xs font-bold text-primary-foreground"
                        onClick={() => onResume?.(task.id)}
                      >
                        继续
                      </button>
                    )}
                    {task.status === 'stopping' ? (
                      <>
                        <button
                          type="button"
                          disabled
                          className="rounded-full border border-card-border px-3 py-1 text-xs font-bold text-muted"
                        >
                          正在停下…
                        </button>
                        {forceStopVisible && (
                          <button
                            type="button"
                            className="rounded-full border border-card-border px-3 py-1 text-xs font-bold text-primary hover:border-primary/60"
                            onClick={() => onForceStop?.()}
                          >
                            强制停止
                          </button>
                        )}
                      </>
                    ) : (
                      <button
                        type="button"
                        className="rounded-full border border-card-border px-3 py-1 text-xs font-bold text-primary hover:border-primary/60"
                        onClick={() => {
                          if (!window.confirm(`要停下现在的${task.label}任务吗？已经投出去的都留着，没投的下次再投。`)) return
                          onStop?.(task.id)
                        }}
                      >
                        停下
                      </button>
                    )}
                  </div>
                )}
                {requested !== null && (
                  <p className="mt-1 text-xs text-muted">
                    共 {requested} 个 · 已投出 {success ?? 0} 个 · 没投出去 {failed ?? 0} 个 · 等下次 {deferred ?? 0} 个
                  </p>
                )}
                {lastLog && <p className="mt-1 text-sm leading-6">{lastLog}</p>}
                {stopNote && <p className="mt-1 text-xs text-danger">停下来的原因：{stopNote}</p>}
                <p className="mt-1 text-xs text-muted">{task.created_at}</p>
              </li>
            )
          })}
        </ul>
      )}

      <h4 className="mb-2 mt-4 text-sm font-black">还没投出去的岗位</h4>
      {pendingJobs.length === 0 ? (
        <p className="rounded-xl bg-card px-3 py-2 text-sm text-muted">
          没有正在排队、也没投失败的岗位。{hasSentJobs ? '已经投出去的在下面“已投出的岗位”里。' : '投完之后，没投出去的会在这儿写原因。'}
        </p>
      ) : (
        <ul className="space-y-2">
          {pendingJobs.map(job => {
            const result = jobResult(job, deliveryRunning)
            const platform = PLATFORM_SHORT_LABELS[job.source_platform ?? ''] ?? job.source_platform ?? ''
            const when = job.step_created_at || job.updated_at
            return (
              <li key={job.id} className="rounded-xl border border-card-border bg-card px-3 py-2">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <span className="text-sm font-black">{job.company}｜{job.title}</span>
                    {platform && <span className="ml-2 text-xs text-muted">{platform}</span>}
                  </div>
                  <span className={`rounded-full px-2 py-1 text-[11px] font-black ${result.tone === 'ok' ? 'bg-success-soft text-success' : result.tone === 'bad' ? 'bg-danger-soft text-danger' : 'bg-secondary text-primary'}`}>
                    {result.label}
                  </span>
                </div>
                <p className="mt-1 text-xs text-muted">{jobFlow(job)}{when ? ` · ${when}` : ''}</p>
                {result.detail && <p className="mt-1 text-sm leading-6 text-muted">{result.detail}</p>}
                {job.status === 'error' && (
                  <p className="mt-1 text-xs text-primary">
                    去<Link to="/" className="font-bold underline">首页“这几个没投出去”</Link>那里再发一次，或者不要了。
                  </p>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}
