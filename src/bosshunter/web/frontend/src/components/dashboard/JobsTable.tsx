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

/** 雇佣类型只留短标签，未知直接省略，不写“还没弄清”这种句子。 */
function employmentShort(employmentType: string | undefined): string | null {
  if (employmentType === 'internship') return '实习'
  if (employmentType === 'full_time') return '全职'
  if (employmentType === 'part_time') return '兼职'
  return null
}

/** 校招/社招只留短标签，未知直接省略。 */
function recruitmentShort(recruitmentType: string | undefined): string | null {
  if (recruitmentType === 'campus') return '校招'
  if (recruitmentType === 'experienced') return '社招'
  return null
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

  const sortableHeader = (label: string, key: JobSortKey, hint?: string) => (
    <button
      type="button"
      onClick={() => onSortChange(key)}
      className="inline-flex items-center gap-1 whitespace-nowrap font-bold hover:text-primary"
      title={`按${hint || label}排`}
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
        <div>
          {/* 瘦身布局约定（v38）：8 列定宽百分比，总和恒等于 100%，表格
              宽度永远等于内容区宽，不再定最小宽、不再横向滚动。
              宽度账（内容区 = 窗口宽 − 侧边栏 240 − 主区 padding 48 −
              卡片 padding 32）：1920 全屏内容区 1600px，1366 全屏
              内容区 1046px；各列按百分比换算后都 ≥ 最小内容宽，
              操作列 24% 在 1366 下是 251px，四个操作按钮两排能放下。 */}
          <table className="w-full table-fixed text-sm">
            <colgroup>
              <col style={{ width: '4%' }} />
              <col style={{ width: '15%' }} />
              <col style={{ width: '23%' }} />
              <col style={{ width: '10%' }} />
              <col style={{ width: '5%' }} />
              <col style={{ width: '8%' }} />
              <col style={{ width: '11%' }} />
              {hasActions && <col style={{ width: '24%' }} />}
            </colgroup>
            <thead>
              <tr className="border-b border-card-border bg-secondary text-xs text-muted">
                <th className="px-2 py-2.5 text-center font-bold">选</th>
                <th className="px-2 py-2.5 text-left font-bold">公司</th>
                <th className="px-2 py-2.5 text-left">{sortableHeader('岗位', 'salary', '工资')}</th>
                <th className="px-2 py-2.5 text-left">{sortableHeader('学历 · 类型', 'education')}</th>
                <th className="px-2 py-2.5 text-left">{sortableHeader('打分', 'score')}</th>
                <th className="px-2 py-2.5 text-left">{sortableHeader('状态', 'status')}</th>
                <th className="px-2 py-2.5 text-left">
                  <span className="inline-flex flex-wrap items-center gap-x-2">
                    {sortableHeader('HR活跃', 'hr_active')}
                    {sortableHeader('时间', 'created_at')}
                  </span>
                </th>
                {hasActions && <th className="px-2 py-2.5 text-center font-bold">能做什么</th>}
              </tr>
            </thead>
            <tbody>
              {loading && jobs.length === 0 && (
                // 加载时用流光骨架行占位，不留空白。
                [0, 1, 2, 3, 4].map(row => (
                  <tr key={`skeleton-${row}`} className="border-b border-card-border">
                    <td className="px-2 py-3"><div className="skeleton mx-auto h-4 w-4" /></td>
                    <td className="px-2 py-3"><div className="skeleton h-4 w-4/5" /></td>
                    <td className="px-2 py-3"><div className="skeleton h-4 w-11/12" /><div className="skeleton mt-1.5 h-3 w-3/5" /></td>
                    <td className="px-2 py-3"><div className="skeleton h-4 w-4/5" /></td>
                    <td className="px-2 py-3"><div className="skeleton h-4 w-3/5" /></td>
                    <td className="px-2 py-3"><div className="skeleton h-4 w-4/5" /></td>
                    <td className="px-2 py-3"><div className="skeleton h-4 w-4/5" /><div className="skeleton mt-1.5 h-3 w-3/5" /></td>
                    {hasActions && <td className="px-2 py-3"><div className="skeleton ml-auto h-7 w-4/5" /></td>}
                  </tr>
                ))
              )}
              {jobs.map((job, rowIndex) => {
                const isExpanded = expanded === job.id
                const isExternalPlatform = job.source_platform === 'zhilian' || job.source_platform === '51job' || job.source_platform === 'liepin'
                const jobUrl = safeJobUrl(job)
                const alreadySent = ['sent', 'replied', 'resume_sent', 'needs_resume', 'follow_up_sent'].includes(job.status)
                const company = (job.company || '').trim()
                const blockedRule = blockedCompanies.find(rule => rule.trim() && company.toLowerCase().includes(rule.trim().toLowerCase()))
                // 岗位次级行：城市 · 工资，缺失项直接省略、不留空位。
                const jobSubLine = [job.city && job.city !== '没认出来' ? job.city : null, job.salary && job.salary !== '-' ? job.salary : null]
                  .filter(Boolean).join(' · ')
                // 学历列只留短标签：学历 · 雇佣类型 · 校招社招，未知段直接省略。
                const eduLine = [job.education || null, employmentShort(job.employment_type), recruitmentShort(job.recruitment_type)]
                  .filter(Boolean).join(' · ') || '—'
                return (
                  <Fragment key={job.id}>
                    <tr
                      className="job-row-enter cursor-pointer border-b border-card-border bg-card transition-colors hover:bg-surface"
                      style={{ animationDelay: `${Math.min(rowIndex, 7) * 0.045}s` }}
                      onClick={() => setExpanded(isExpanded ? null : job.id)}
                    >
                      <td className="px-2 py-2.5 text-center" onClick={event => event.stopPropagation()}>
                        <input
                          type="checkbox"
                          checked={selectedIds.includes(job.id)}
                          onChange={() => onToggleSelected(job.id)}
                          aria-label={`选这个 ${job.company} ${job.title}`}
                          className="h-4 w-4 accent-primary"
                        />
                      </td>
                      <td className="min-w-0 px-2 py-2.5">
                        <div className="flex min-w-0 items-center gap-1.5">
                          <span className="min-w-0 flex-1 truncate font-black text-foreground" title={job.company}>{job.company}</span>
                          <span className={`shrink-0 whitespace-nowrap rounded-full px-2 py-0.5 text-[10px] font-black ${job.source_platform === 'boss' || !job.source_platform ? 'bg-secondary text-primary' : 'bg-info-soft text-info'}`}>
                            {PLATFORM_SHORT_LABELS[job.source_platform || 'boss'] || 'BOSS'}
                          </span>
                          <OutsourcingBadge job={job} />
                          {job.company_size && (
                            <span className="hidden shrink-0 whitespace-nowrap rounded-full bg-surface px-2 py-0.5 text-[10px] font-bold text-muted xl:inline">{job.company_size}</span>
                          )}
                        </div>
                      </td>
                      <td className="min-w-0 px-2 py-2.5">
                        <div className="truncate font-bold text-foreground" title={job.title}>{job.title}</div>
                        {jobSubLine && <div className="mt-0.5 truncate text-xs text-muted" title={jobSubLine}>{jobSubLine}</div>}
                      </td>
                      <td className="min-w-0 px-2 py-2.5 text-xs">
                        <div className="truncate font-bold text-foreground" title={eduLine}>{eduLine}</div>
                      </td>
                      <td className="whitespace-nowrap px-2 py-2.5">
                        <span className={`font-mono font-black tabular-nums ${getScoreColor(job.score)}`}>{job.score || '-'}</span>
                      </td>
                      <td className="whitespace-nowrap px-2 py-2.5">
                        <Badge variant={statusVariant(job.status) as any} className="whitespace-nowrap">{getStatusLabel(job.status)}</Badge>
                      </td>
                      <td className="min-w-0 px-2 py-2.5 text-xs text-muted">
                        <div className="truncate whitespace-nowrap" title={job.hr_active || ''}>{job.hr_active || '—'}</div>
                        <div className="mt-0.5 flex items-center gap-1 whitespace-nowrap">
                          {timeAgo(job.created_at)}
                          {isExpanded ? <ChevronUp className="h-3 w-3 shrink-0" /> : <ChevronDown className="h-3 w-3 shrink-0" />}
                        </div>
                      </td>
                      {hasActions && (
                        <td className="px-2 py-2.5" onClick={event => event.stopPropagation()}>
                          <div className="flex flex-wrap items-center justify-center gap-1">
                            {isExternalPlatform && jobUrl && (
                              <a href={jobUrl} target="_blank" rel="noopener noreferrer" className="inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-lg border border-card-border px-1.5 py-1 text-[11px] font-bold text-primary transition-all duration-200 hover:-translate-y-px hover:bg-secondary active:scale-[0.97]">
                                <ExternalLink className="h-3.5 w-3.5" />去平台看看
                              </a>
                            )}
                            {isExternalPlatform && !jobUrl && (
                              <span className="shrink-0 whitespace-nowrap rounded-lg bg-warning-soft px-1.5 py-1 text-[11px] font-bold text-warning">这个链接打不开</span>
                            )}
                            {!isExternalPlatform && jobUrl && (
                              <a href={jobUrl} target="_blank" rel="noopener noreferrer" className="inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-lg border border-card-border px-1.5 py-1 text-[11px] font-bold text-primary transition-all duration-200 hover:-translate-y-px hover:bg-secondary active:scale-[0.97]">
                                <ExternalLink className="h-3.5 w-3.5" />打开这个岗位
                              </a>
                            )}
                            {isExternalPlatform && onMarkManuallySent && (
                              <button
                                type="button"
                                disabled={alreadySent}
                                onClick={() => onMarkManuallySent(job)}
                                className="inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-lg bg-primary px-1.5 py-1 text-[11px] font-bold text-primary-foreground transition-all duration-200 hover:-translate-y-px hover:opacity-90 active:scale-[0.97] disabled:bg-success-soft disabled:text-success disabled:opacity-100 disabled:hover:translate-y-0"
                              >
                                <CheckCircle2 className="h-3.5 w-3.5" />{alreadySent ? '已投出' : '我投过了'}
                              </button>
                            )}
                            {onSoftDelete && (
                              <button type="button" onClick={() => onSoftDelete(job)} className="shrink-0 whitespace-nowrap rounded-lg p-1.5 text-muted transition-all duration-200 hover:-translate-y-px hover:bg-danger-soft hover:text-danger active:scale-[0.97]" aria-label={`将 ${job.company} ${job.title} 扔进回收站`}>
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
                                className="inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-lg border border-card-border px-1.5 py-1 text-[11px] font-bold text-muted transition-all duration-200 hover:-translate-y-px hover:bg-danger-soft hover:text-danger active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:translate-y-0"
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
                                className="shrink-0 whitespace-nowrap rounded-lg border border-card-border bg-card px-1.5 py-1 text-[11px] font-bold text-foreground transition-colors hover:border-primary"
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
                        <td colSpan={hasActions ? 8 : 7} className="px-6 py-4">
                          <div className="w-full max-w-2xl">
                            <InterviewPreparation key={job.id} job={job} />
                          </div>
                          {job.employment_review && (
                            <p className="mb-4 text-sm font-bold text-amber-600" title={job.employment_review}>{job.employment_review}</p>
                          )}
                          <div className="grid grid-cols-1 gap-4 text-sm lg:grid-cols-3">
                            <div className="rounded-2xl border border-card-border bg-card p-4">
                              <p className="mb-2 text-xs font-black text-primary">JD简单说</p>
                              <p className="line-clamp-6 leading-6 text-muted">{job.jd || '没有'}</p>
                            </div>
                            <div className="rounded-2xl border border-card-border bg-card p-4">
                              <p className="mb-2 text-xs font-black text-primary">打招呼的话</p>
                              <p className="line-clamp-6 whitespace-pre-wrap leading-6 text-muted">{job.effective_greeting || job.greeting || '还没写'}</p>
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
              {!jobs.length && !loading && (
                <tr>
                  <td colSpan={hasActions ? 8 : 7} className="px-4 py-10 text-center text-sm text-muted">
                    按现在的条件没找到合适的
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
