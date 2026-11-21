import { Fragment, useEffect, useState } from 'react'
import { OutsourcingBadge } from '@/components/jobs/OutsourcingBadge'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Ban, CheckCircle2, ChevronDown, ChevronUp, ExternalLink, Trash2 } from 'lucide-react'
import { getStatusLabel } from '@/lib/status'
import { PLATFORM_SHORT_LABELS } from '@/lib/platforms'
import type { Job } from '@/hooks/useDashboard'
import type { JobSortKey, JobSortOrder } from '@/hooks/useJobSearch'
import { InterviewPreparation } from '@/components/jobs/InterviewPreparation'

interface JobsTableProps {
  jobs: Job[]
  page: number
  pageSize: number
  total: number
  onPageChange: (page: number) => void
  selectedIds: string[]
  onToggleSelected: (id: string) => void
  onSoftDelete?: (job: Job) => void
  onMarkManuallySent?: (job: Job) => void
  onStatusChange?: (job: Job, status: string) => void
  onBlockCompany?: (job: Job) => void
  blockedCompanies?: string[]
  blockingJobId?: string | null
  loading?: boolean
  sortBy: JobSortKey
  sortOrder: JobSortOrder
  onSortChange: (sortBy: JobSortKey) => void
}

function safeJobUrl(job: Job): string | null {
  const platform = job.source_platform || 'boss'
  if (platform !== 'boss' && platform !== 'zhilian' && platform !== '51job' && platform !== 'liepin') return null
  try {
    const parsed = platform === 'boss'
      ? new URL(job.url || '', 'https://www.zhipin.com')
      : new URL(job.url || '')
    if (parsed.protocol !== 'https:') return null
    const rootDomain = platform === 'boss'
      ? 'zhipin.com'
      : platform === 'zhilian'
        ? 'zhaopin.com'
        : platform === '51job'
          ? '51job.com'
          : 'liepin.com'
    if (parsed.hostname !== rootDomain && !parsed.hostname.endsWith(`.${rootDomain}`)) return null
    return parsed.toString()
  } catch {
    return null
  }
}

function statusVariant(status: string) {
  const variants = new Set([
    'pending',
    'scored',
    'filtered',
    'ready',
    'approved',
    'skipped',
    'sent',
    'replied',
    'resume_sent',
    'needs_resume',
    'follow_up_sent',
    'rejected',
    'error',
  ])
  return variants.has(status) ? status : 'default'
}

export function JobsTable({ jobs, page, pageSize, total, onPageChange, selectedIds, onToggleSelected, onSoftDelete, onMarkManuallySent, onStatusChange, onBlockCompany, blockedCompanies = [], blockingJobId = null, loading = false, sortBy, sortOrder, onSortChange }: JobsTableProps) {
  const [expanded, setExpanded] = useState<string | null>(null)
  const [pageInput, setPageInput] = useState(String(page + 1))
  const totalPages = Math.ceil(total / pageSize)
  const hasActions = Boolean(onSoftDelete || onMarkManuallySent || onStatusChange || onBlockCompany)

  useEffect(() => {
    setPageInput(String(page + 1))
  }, [page])

  const jumpToPage = () => {
    const requested = Number.parseInt(pageInput, 10)
    if (!Number.isFinite(requested) || totalPages < 1) {
      setPageInput(String(page + 1))
      return
    }
    onPageChange(Math.min(totalPages - 1, Math.max(0, requested - 1)))
  }

  const getScoreColor = (score: number) => {
    if (score >= 80) return 'text-success'
    if (score >= 60) return 'text-primary'
    return 'text-muted'
  }

  const timeAgo = (dateStr: string) => {
    if (!dateStr) return ''
    const normalizedDate = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(dateStr)
      ? `${dateStr.replace(' ', 'T')}Z`
      : dateStr
    const timestamp = new Date(normalizedDate).getTime()
    if (Number.isNaN(timestamp)) return ''
    const diff = Date.now() - timestamp
    const hours = Math.floor(diff / 3600000)
    if (hours < 1) return '刚刚'
    if (hours < 24) return `${hours}h 前`
    return `${Math.floor(hours / 24)}d 前`
  }

  const sortableHeader = (label: string, key: JobSortKey) => (
    <button
      type="button"
      onClick={() => onSortChange(key)}
      className="inline-flex items-center gap-1 whitespace-nowrap font-bold hover:text-primary"
      title={`按${label}排`}
    >
      {label}<span className="text-[10px]">{sortBy === key ? (sortOrder === 'asc' ? '↑' : '↓') : '↕'}</span>
    </button>
  )

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
        <CardTitle>岗位列表</CardTitle>
        <span className="text-xs text-muted">{total} 个岗位</span>
      </CardHeader>
      <CardContent className="p-0">
        <div className="overflow-x-auto">
          {/* 响应式布局约定：各列只定最小宽 + 伸缩权重，不强行压扁。
              表格总最小宽 1260px；窗口比它窄时这里横向滚动，列宽、
              表头与数据列始终对齐，操作按钮不被挤出或重叠。 */}
          <table className="w-full min-w-[1260px] text-sm">
            <thead>
              <tr className="border-b border-card-border bg-secondary text-xs text-muted">
                <th className="w-10 min-w-10 px-3 py-3 text-center font-bold">选</th>
                <th className="min-w-[170px] px-4 py-3 text-left font-bold">公司</th>
                <th className="min-w-[150px] px-4 py-3 text-left font-bold">岗位</th>
                <th className="min-w-[84px] whitespace-nowrap px-4 py-3 text-left font-bold">城市</th>
                <th className="min-w-[92px] whitespace-nowrap px-4 py-3 text-left">{sortableHeader('工资', 'salary')}</th>
                <th className="min-w-[150px] whitespace-nowrap px-4 py-3 text-left">{sortableHeader('学历 · 校招社招', 'education')}</th>
                <th className="min-w-[52px] whitespace-nowrap px-4 py-3 text-left">{sortableHeader('打分', 'score')}</th>
                <th className="min-w-[84px] whitespace-nowrap px-4 py-3 text-left">{sortableHeader('状态', 'status')}</th>
                <th className="min-w-[120px] whitespace-nowrap px-4 py-3 text-left">{sortableHeader('HR 活跃吗', 'hr_active')}</th>
                <th className="min-w-[76px] whitespace-nowrap px-4 py-3 text-left">{sortableHeader('时间', 'created_at')}</th>
                {hasActions && <th className="min-w-[240px] px-3 py-3 text-center font-bold">能做什么</th>}
              </tr>
            </thead>
            <tbody>
              {jobs.map(job => {
                const isExpanded = expanded === job.id
                const isExternalPlatform = job.source_platform === 'zhilian' || job.source_platform === '51job' || job.source_platform === 'liepin'
                const jobUrl = safeJobUrl(job)
                const alreadySent = ['sent', 'replied', 'resume_sent', 'needs_resume', 'follow_up_sent'].includes(job.status)
                const company = (job.company || '').trim()
                const blockedRule = blockedCompanies.find(rule => rule.trim() && company.toLowerCase().includes(rule.trim().toLowerCase()))
                return (
                  <Fragment key={job.id}>
                    <tr
                      className="cursor-pointer border-b border-card-border bg-card transition-colors hover:bg-surface"
                      onClick={() => setExpanded(isExpanded ? null : job.id)}
                    >
                      <td className="px-3 py-3 text-center" onClick={event => event.stopPropagation()}>
                        <input
                          type="checkbox"
                          checked={selectedIds.includes(job.id)}
                          onChange={() => onToggleSelected(job.id)}
                          aria-label={`选这个 ${job.company} ${job.title}`}
                          className="h-4 w-4 accent-primary"
                        />
                      </td>
                      <td className="min-w-[170px] px-4 py-3">
                        <div className="flex min-w-0 items-center gap-2">
                          <span className="max-w-[160px] truncate font-black text-foreground" title={job.company}>{job.company}</span>
                          <span className={`shrink-0 whitespace-nowrap rounded-full px-2 py-0.5 text-[10px] font-black ${job.source_platform === 'boss' || !job.source_platform ? 'bg-secondary text-primary' : 'bg-info-soft text-info'}`}>
                            {PLATFORM_SHORT_LABELS[job.source_platform || 'boss'] || 'BOSS'}
                          </span>
                          <OutsourcingBadge job={job} />
                          {job.company_size && (
                            <span className="shrink-0 whitespace-nowrap rounded-full bg-surface px-2 py-0.5 text-[10px] font-bold text-muted">{job.company_size}</span>
                          )}
                        </div>
                      </td>
                      <td className="max-w-[220px] truncate whitespace-nowrap px-4 py-3 font-bold text-foreground" title={job.title}>{job.title}</td>
                      <td className="whitespace-nowrap px-4 py-3 text-muted">{job.city || '没认出来'}</td>
                      <td className="whitespace-nowrap px-4 py-3 text-muted">{job.salary || '-'}</td>
                      <td className="px-4 py-3 text-xs">
                        <div className="truncate whitespace-nowrap font-bold text-foreground" title={job.education || '学历没认出来'}>{job.education || '学历没认出来'}</div>
                        <div className="mt-1 truncate whitespace-nowrap text-muted" title={`这份工作是：${job.employment_type === 'internship' ? '实习' : job.employment_type === 'full_time' ? '全职' : job.employment_type === 'part_time' ? '兼职' : '还没弄清'}`}>这份工作是：{job.employment_type === 'internship' ? '实习' : job.employment_type === 'full_time' ? '全职' : job.employment_type === 'part_time' ? '兼职' : '还没弄清'}</div>
                        {job.employment_review && <div className="mt-1 truncate whitespace-nowrap text-amber-600" title={job.employment_review}>{job.employment_review}</div>}
                        <div className="mt-1 truncate whitespace-nowrap text-muted" title={job.recruitment_type === 'campus' ? '校招' : job.recruitment_type === 'experienced' ? '社招' : '还没弄清是哪种'}>{job.recruitment_type === 'campus' ? '校招' : job.recruitment_type === 'experienced' ? '社招' : '还没弄清是哪种'}</div>
                      </td>
                      <td className="whitespace-nowrap px-4 py-3">
                        <span className={`font-mono font-black tabular-nums ${getScoreColor(job.score)}`}>{job.score || '-'}</span>
                      </td>
                      <td className="whitespace-nowrap px-4 py-3">
                        <Badge variant={statusVariant(job.status) as any} className="whitespace-nowrap">{getStatusLabel(job.status)}</Badge>
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-xs text-muted"><span className="block max-w-[140px] truncate whitespace-nowrap" title={job.hr_active || '不知道 HR 活跃不活跃'}>{job.hr_active || '不知道 HR 活跃不活跃'}</span></td>
                      <td className="whitespace-nowrap px-4 py-3 text-xs text-muted">
                        <div className="flex items-center gap-2 whitespace-nowrap">
                          {timeAgo(job.created_at)}
                          {isExpanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
                        </div>
                      </td>
                      {hasActions && (
                        <td className="min-w-[240px] px-3 py-3" onClick={event => event.stopPropagation()}>
                          <div className="flex flex-wrap items-center justify-center gap-1.5">
                            {isExternalPlatform && jobUrl && (
                              <a href={jobUrl} target="_blank" rel="noopener noreferrer" className="inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-lg border border-card-border px-2 py-1.5 text-[11px] font-bold text-primary hover:bg-secondary">
                                <ExternalLink className="h-3.5 w-3.5" />去平台看看
                              </a>
                            )}
                            {isExternalPlatform && !jobUrl && (
                              <span className="shrink-0 whitespace-nowrap rounded-lg bg-warning-soft px-2 py-1.5 text-[11px] font-bold text-warning">这个链接打不开</span>
                            )}
                            {!isExternalPlatform && jobUrl && (
                              <a href={jobUrl} target="_blank" rel="noopener noreferrer" className="inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-lg border border-card-border px-2 py-1.5 text-[11px] font-bold text-primary hover:bg-secondary">
                                <ExternalLink className="h-3.5 w-3.5" />打开这个岗位
                              </a>
                            )}
                            {isExternalPlatform && onMarkManuallySent && (
                              <button
                                type="button"
                                disabled={alreadySent}
                                onClick={() => onMarkManuallySent(job)}
                                className="inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-lg bg-primary px-2 py-1.5 text-[11px] font-bold text-primary-foreground hover:opacity-90 disabled:bg-success-soft disabled:text-success disabled:opacity-100"
                              >
                                <CheckCircle2 className="h-3.5 w-3.5" />{alreadySent ? '已投出' : '我投过了'}
                              </button>
                            )}
                            {onSoftDelete && (
                              <button type="button" onClick={() => onSoftDelete(job)} className="shrink-0 whitespace-nowrap rounded-lg p-2 text-muted hover:bg-danger-soft hover:text-danger" aria-label={`将 ${job.company} ${job.title} 扔进回收站`}>
                                <Trash2 className="h-4 w-4" />
                              </button>
                            )}
                            {onBlockCompany && (
                              <button
                                type="button"
                                disabled={!company || Boolean(blockedRule) || Boolean(blockingJobId)}
                                onClick={() => onBlockCompany(job)}
                                aria-label={`屏蔽这家公司 ${company || '没写公司名'}`}
                                title={blockedRule ? `已经被“${blockedRule}”屏蔽了，在设置里能解除` : company ? '看完预览就把这家公司加进屏蔽名单' : '没写公司名，屏蔽不了'}
                                className="inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-lg border border-card-border px-2 py-1.5 text-[11px] font-bold text-muted hover:bg-danger-soft hover:text-danger disabled:cursor-not-allowed disabled:opacity-50"
                              >
                                <Ban className="h-3.5 w-3.5" />{blockingJobId === job.id ? '正在处理…' : blockedRule ? '已经屏蔽了' : '屏蔽公司'}
                              </button>
                            )}
                            {onStatusChange && !alreadySent && (
                              <select
                                defaultValue=""
                                aria-label={`改一下 ${job.company} ${job.title} 状态`}
                                onChange={event => {
                                  const status = event.target.value
                                  if (status) onStatusChange(job, status)
                                  event.currentTarget.value = ''
                                }}
                                className="shrink-0 whitespace-nowrap rounded-lg border border-card-border bg-card px-2 py-1.5 text-[11px] font-bold text-foreground"
                              >
                                <option value="">改状态</option>
                                <option value="ready">等着投</option>
                                <option value="filtered">已筛掉</option>
                                <option value="skipped">已跳过</option>
                                <option value="rejected">不要了</option>
                              </select>
                            )}
                          </div>
                        </td>
                      )}
                    </tr>
                    {isExpanded && (
                      <tr className="border-b border-card-border bg-surface">
                        <td colSpan={hasActions ? 11 : 10} className="px-6 py-4">
                          <div className="w-full max-w-2xl">
                            <InterviewPreparation key={job.id} job={job} />
                          </div>
                          <div className="grid grid-cols-1 gap-4 text-sm lg:grid-cols-3">
                            <div className="rounded-2xl border border-card-border bg-card p-4">
                              <p className="mb-2 text-xs font-black text-primary">JD简单说</p>
                              <p className="line-clamp-6 leading-6 text-muted">{job.jd || '没有'}</p>
                            </div>
                            <div className="rounded-2xl border border-card-border bg-card p-4">
                              <p className="mb-2 text-xs font-black text-primary">打招呼的话</p>
                              <p className="line-clamp-6 whitespace-pre-wrap leading-6 text-muted">{job.greeting || '还没写'}</p>
                            </div>
                            <div className="rounded-2xl border border-card-border bg-card p-4">
                              <p className="mb-2 text-xs font-black text-primary">为什么给这个分</p>
                              <p className="line-clamp-6 whitespace-pre-wrap leading-6 text-muted">{job.score_reason || '无'}</p>
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                )
              })}
              {!jobs.length && (
                <tr>
                  <td colSpan={hasActions ? 11 : 10} className="px-4 py-10 text-center text-sm text-muted">
                    {loading ? '正在找岗位…' : '按现在的条件没找到合适的'}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {totalPages > 0 && (
          <div className="flex flex-wrap items-center justify-center gap-3 border-t border-card-border px-4 py-3 text-xs">
            <button
              onClick={() => onPageChange(0)}
              disabled={page === 0}
              className="font-bold text-muted transition hover:text-foreground disabled:opacity-30"
            >
              第一页
            </button>
            <button
              onClick={() => onPageChange(Math.max(0, page - 1))}
              disabled={page === 0}
              className="font-bold text-muted transition hover:text-foreground disabled:opacity-30"
            >
              上一页
            </button>
            <label className="flex items-center gap-1 text-muted">
              第
              <input
                type="number"
                min={1}
                max={Math.max(1, totalPages)}
                value={pageInput}
                onChange={event => setPageInput(event.target.value)}
                onKeyDown={event => { if (event.key === 'Enter') jumpToPage() }}
                onBlur={jumpToPage}
                aria-label="跳到第几页"
                className="w-14 rounded-md border border-card-border bg-surface px-2 py-1 text-center text-foreground outline-none focus:border-primary"
              />
              页 / {totalPages} 页
            </label>
            <button
              onClick={() => onPageChange(Math.min(totalPages - 1, page + 1))}
              disabled={page >= totalPages - 1}
              className="font-bold text-muted transition hover:text-foreground disabled:opacity-30"
            >
              下一页
            </button>
            <button
              onClick={() => onPageChange(totalPages - 1)}
              disabled={page >= totalPages - 1}
              className="font-bold text-muted transition hover:text-foreground disabled:opacity-30"
            >
              最后一页
            </button>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
