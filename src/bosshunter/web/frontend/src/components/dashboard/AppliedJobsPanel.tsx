import { useEffect, useState } from 'react'
import { ChevronDown, ChevronUp, ExternalLink, RefreshCw } from 'lucide-react'
import { JobFilterBar } from '@/components/jobs/JobFilterBar'
import { Button } from '@/components/ui/button'
import { useJobSearch } from '@/hooks/useJobSearch'
import type { Job } from '@/hooks/useDashboard'
import { EMPTY_JOB_FILTERS, hasInvalidSalaryRange, type JobFilters } from '@/lib/jobFilters'
import { PLATFORM_SHORT_LABELS } from '@/lib/platforms'
import { APPLIED_JOB_STATUSES, getStatusLabel, STATUS_LABELS } from '@/lib/status'
import { cn } from '@/lib/utils'

/** 已投出岗位在投递跟进页的分区：与任务一的在建任务列表、待处理消息
 * 列表是同一页里的不同分区，共用 /api/jobs/search 这一份数据源
 * （applied_only=1），不另起接口、不另起一套列表。
 * 找工作页（默认搜索）永远拿不到这些岗位，判定以后端分池为准。 */
const APPLIED_STATUS_OPTIONS = APPLIED_JOB_STATUSES.map(value => ({
  value,
  label: STATUS_LABELS[value] ?? value,
}))

function safeJobUrl(job: Job): string | null {
  const platform = job.source_platform || 'boss'
  if (!job.url) return null
  try {
    const parsed = platform === 'boss'
      ? new URL(job.url, 'https://www.zhipin.com')
      : new URL(job.url)
    if (parsed.protocol !== 'https:') return null
    const rootDomain = platform === 'boss'
      ? 'zhipin.com'
      : platform === 'zhilian'
        ? 'zhaopin.com'
        : platform === '51job'
          ? '51job.com'
          : platform === 'liepin'
            ? 'liepin.com'
            : ''
    if (!rootDomain) return null
    return parsed.hostname === rootDomain || parsed.hostname.endsWith(`.${rootDomain}`)
      ? parsed.toString()
      : null
  } catch {
    return null
  }
}

function shortTime(value: string | undefined): string {
  return (value || '').slice(0, 16) || '-'
}

export function AppliedJobsPanel({ onCheckReplies }: { onCheckReplies?: () => Promise<string> }) {
  const pageSize = 10
  const [page, setPage] = useState(0)
  const [filters, setFilters] = useState<JobFilters>({ ...EMPTY_JOB_FILTERS })
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [checkNotice, setCheckNotice] = useState('')
  const [checkingReplies, setCheckingReplies] = useState(false)
  // 按最近一次动态（投递 / 回复 / 跟进的最近动作）排，2 秒一轮：
  // 刚投出的岗位从找工作页消失后，最多 2 秒就会出现在这里。
  const { items, total, allTotal, cityOptions, loading, error, refresh } = useJobSearch(
    filters, page, pageSize, 'updated_at', 'desc',
    { appliedOnly: true, pollIntervalMs: 2000 },
  )
  const totalPages = Math.ceil(total / pageSize)

  useEffect(() => {
    setPage(0)
  }, [filters.query, filters.minScore, filters.salaryMin, filters.salaryMax, filters.status, filters.createdWithin, filters.sourcePlatform, filters.city, filters.education, filters.recruitmentType])

  const handleCheckReplies = async () => {
    if (!onCheckReplies || checkingReplies) return
    setCheckingReplies(true)
    try {
      setCheckNotice(await onCheckReplies())
    } finally {
      setCheckingReplies(false)
    }
  }

  return (
    <section id="applied-jobs" aria-label="已投出的岗位" className="scroll-mt-6 rounded-2xl border border-card-border bg-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-lg font-black">已投出的岗位</h3>
          <p className="mt-1 text-xs leading-5 text-muted">
            投出去的岗位都搬到这儿了，找工作页里不再出现。按公司、职位、平台、状态筛；
            时间筛的是找到岗位的时间，列表按最近一次动态（投递、回复、跟进）排。
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded-full bg-secondary px-3 py-2 text-xs font-black text-primary">一共 {allTotal} 个已投出</span>
          {onCheckReplies && (
            <Button size="sm" variant="secondary" onClick={() => void handleCheckReplies()} disabled={checkingReplies}>
              <RefreshCw className={cn('mr-1 h-3.5 w-3.5', checkingReplies && 'animate-spin')} />
              {checkingReplies ? '正在开始查…' : '再查一次 HR 回复'}
            </Button>
          )}
          <Button variant="ghost" size="sm" onClick={() => { void refresh() }}>刷新一下</Button>
        </div>
      </div>
      {checkNotice && <div className="mt-3 rounded-xl bg-secondary px-4 py-3 text-sm text-primary">{checkNotice}</div>}
      <div className="mt-3">
        <JobFilterBar
          filters={filters}
          onChange={setFilters}
          onReset={() => setFilters({ ...EMPTY_JOB_FILTERS })}
          resultCount={total}
          totalCount={allTotal}
          invalidSalary={hasInvalidSalaryRange(filters)}
          showStatus
          showSource
          cityOptions={cityOptions}
          compact
          statusOptions={APPLIED_STATUS_OPTIONS}
        />
      </div>
      {error && <div className="mb-3 rounded-xl border border-danger-border bg-danger-soft px-4 py-3 text-sm text-danger">{error}</div>}
      <div className="space-y-2">
        {items.map(job => {
          const jobUrl = safeJobUrl(job)
          const isExpanded = expandedId === job.id
          return (
            <div key={job.id} className="rounded-2xl border border-card-border bg-card p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <button
                  type="button"
                  onClick={() => setExpandedId(isExpanded ? null : job.id)}
                  className="min-w-0 flex-1 text-left"
                  aria-expanded={isExpanded}
                >
                  <span className="flex flex-wrap items-center gap-2 font-black">
                    <span className="truncate">{job.company}｜{job.title}</span>
                    <span className="rounded-full bg-secondary px-2 py-0.5 text-[10px] font-black text-primary">
                      {PLATFORM_SHORT_LABELS[job.source_platform || 'boss'] || 'BOSS'}
                    </span>
                    <span className="rounded-full bg-success-soft px-2 py-0.5 text-[10px] font-black text-success">
                      {getStatusLabel(job.status)}
                    </span>
                    {isExpanded ? <ChevronUp className="h-3.5 w-3.5 text-muted" /> : <ChevronDown className="h-3.5 w-3.5 text-muted" />}
                  </span>
                  <span className="mt-1 block text-xs text-muted">
                    {[job.salary, job.score ? `打分 ${job.score}` : '', job.city].filter(Boolean).join(' · ')}
                    {` · 最近动态 ${shortTime(job.last_activity_at || job.updated_at || job.created_at)}`}
                  </span>
                </button>
                <div className="flex shrink-0 flex-wrap gap-1.5">
                  {jobUrl && (
                    <a
                      href={jobUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 rounded-lg border border-card-border px-2 py-1.5 text-[11px] font-bold text-primary hover:bg-secondary"
                    >
                      <ExternalLink className="h-3.5 w-3.5" />打开这个岗位
                    </a>
                  )}
                </div>
              </div>
              {isExpanded && (
                <div className="mt-3 grid grid-cols-1 gap-3 border-t border-card-border pt-3 text-sm lg:grid-cols-3">
                  <div className="rounded-2xl border border-card-border bg-surface p-3">
                    <p className="mb-1 text-xs font-black text-primary">JD 简单说</p>
                    <p className="line-clamp-6 whitespace-pre-wrap leading-6 text-muted">{job.jd || '没有'}</p>
                  </div>
                  <div className="rounded-2xl border border-card-border bg-surface p-3">
                    <p className="mb-1 text-xs font-black text-primary">当时发的打招呼</p>
                    <p className="line-clamp-6 whitespace-pre-wrap leading-6 text-muted">{job.greeting || '还没写'}</p>
                  </div>
                  <div className="rounded-2xl border border-card-border bg-surface p-3">
                    <p className="mb-1 text-xs font-black text-primary">为什么给这个分</p>
                    <p className="line-clamp-6 whitespace-pre-wrap leading-6 text-muted">{job.score_reason || '无'}</p>
                    <p className="mt-2 text-xs text-muted">找到时间 {shortTime(job.created_at)} · 最近动态 {shortTime(job.last_activity_at || job.updated_at || job.created_at)}</p>
                  </div>
                </div>
              )}
            </div>
          )
        })}
        {!items.length && (
          <div className="rounded-2xl border border-dashed border-card-border bg-card p-5 text-sm text-muted">
            {loading
              ? '正在找已投出的岗位…'
              : total === 0 && allTotal === 0
                ? '还没投出过岗位。投出去的岗位会自动来这儿，不用在找工作页里翻。'
                : '按现在的条件没找到已投出的岗位，换个关键词或状态试试。'}
          </div>
        )}
      </div>
      {totalPages > 1 && (
        <div className="mt-3 flex flex-wrap items-center justify-center gap-3 text-xs">
          <Button variant="secondary" size="sm" disabled={page === 0} onClick={() => setPage(value => Math.max(0, value - 1))}>上一页</Button>
          <span className="text-muted">第 {page + 1} 页 / {totalPages} 页</span>
          <Button variant="secondary" size="sm" disabled={page >= totalPages - 1} onClick={() => setPage(value => Math.min(totalPages - 1, value + 1))}>下一页</Button>
        </div>
      )}
    </section>
  )
}
