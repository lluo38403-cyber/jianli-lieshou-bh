import { RotateCcw, Search } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { MultiSelect } from '@/components/ui/multi-select'
import { cn } from '@/lib/utils'
import { STATUS_LABELS } from '@/lib/status'
import { hasActiveJobFilters, type JobFilters } from '@/lib/jobFilters'

interface JobFilterBarProps {
  filters: JobFilters
  onChange: (filters: JobFilters) => void
  onReset: () => void
  resultCount: number
  totalCount: number
  invalidSalary?: boolean
  showStatus?: boolean
  showSource?: boolean
  cityOptions?: string[]
  compact?: boolean
  /** 状态筛选项：不传就是全部状态。找工作页只传未投出状态，投递跟进页
   * 只传已投出状态；后端分池会再拦一道，选项与后端不一致也漏不出去。 */
  statusOptions?: Array<{ value: string; label: string }>
}

export function JobFilterBar({
  filters,
  onChange,
  onReset,
  resultCount,
  totalCount,
  invalidSalary = false,
  showStatus = false,
  showSource = false,
  cityOptions = [],
  compact = false,
  statusOptions = Object.entries(STATUS_LABELS).map(([value, label]) => ({ value, label })),
}: JobFilterBarProps) {
  const controlClass = cn('min-w-0', compact && 'h-7 px-2 text-xs')
  const update = (key: keyof JobFilters, value: string) => onChange({ ...filters, [key]: value })
  const updateMulti = (key: keyof JobFilters, value: string[]) => onChange({ ...filters, [key]: value })

  return (
    <div className={cn(!compact && "mb-4 rounded-2xl border border-card-border bg-surface p-3")}>
      <div className={cn("grid min-w-0", compact ? "grid-cols-2 gap-1.5 xl:grid-cols-4" : "grid-cols-1 gap-2 md:grid-cols-2 2xl:grid-cols-4")}>
        <label className={cn("relative min-w-0", compact ? "col-span-2 xl:col-span-1" : "md:col-span-2 2xl:col-span-1")}>
          <Search className={cn("pointer-events-none absolute text-muted", compact ? "left-2 top-2 h-3 w-3" : "left-3 top-2.5 h-4 w-4")} />
          <Input
            value={filters.query}
            onChange={event => update('query', event.target.value)}
            placeholder="搜岗位、公司，或者打分理由"
            className={cn(controlClass, compact ? "pl-7" : "pl-9")}
            aria-label="关键词"
          />
        </label>
        <Select className={controlClass} value={filters.createdWithin} onChange={event => update('createdWithin', event.target.value)} aria-label="什么时候发布的">
          <option value="">什么时候发布的：全部</option>
          <option value="today">今天</option>
          <option value="3d">近 3 天</option>
          <option value="7d">近 7 天</option>
        </Select>
        <Select className={controlClass} value={filters.minScore} onChange={event => update('minScore', event.target.value)} aria-label="最低分">
          <option value="">最低分：不限</option>
          <option value="60">60+</option>
          <option value="71">71+</option>
          <option value="80">80+</option>
        </Select>
        <Input
          type="number"
          min="0"
          step="1"
          value={filters.salaryMin}
          onChange={event => update('salaryMin', event.target.value)}
          placeholder="最低工资（千）"
          className={cn(controlClass, compact && 'xl:order-1')}
          aria-label="最低工资（千）"
        />
        <Input
          type="number"
          min="0"
          step="1"
          value={filters.salaryMax}
          onChange={event => update('salaryMax', event.target.value)}
          placeholder="最高工资（千）"
          className={cn(controlClass, compact && 'xl:order-1')}
          aria-label="最高工资（千）"
        />
        {showStatus && (
          <MultiSelect className={controlClass} compact={compact} value={filters.status} onChange={value => updateMulti('status', value)} placeholder="什么状态都行" options={statusOptions} />
        )}
        {showSource && (
          <MultiSelect className={controlClass} compact={compact} value={filters.sourcePlatform} onChange={value => updateMulti('sourcePlatform', value)} placeholder="哪个平台的都行" options={[
            { value: 'boss', label: 'BOSS 直聘' },
            { value: 'zhilian', label: '智联招聘' },
            { value: '51job', label: '前程无忧' },
            { value: 'liepin', label: '猎聘' },
          ]} />
        )}
        {cityOptions.length > 0 && (
          <MultiSelect className={cn(controlClass, compact && 'xl:order-1')} compact={compact} value={filters.city} onChange={value => updateMulti('city', value)} placeholder="哪个城市都行" options={cityOptions.map(city => ({ value: city, label: city }))} />
        )}
        <MultiSelect className={cn(controlClass, compact && 'xl:order-1')} compact={compact} value={filters.education} onChange={value => updateMulti('education', value)} placeholder="什么学历都行" options={[
          { value: '博士', label: '博士' }, { value: '硕士', label: '硕士' }, { value: '本科', label: '本科' },
          { value: '大专', label: '大专' }, { value: '不限', label: '学历不限' }, { value: 'unknown', label: '未识别' },
        ]} />
        <MultiSelect className={cn(controlClass, compact && 'xl:order-1')} compact={compact} value={filters.recruitmentType} onChange={value => updateMulti('recruitmentType', value)} placeholder="校招社招都行" options={[
          { value: 'campus', label: '校招' }, { value: 'experienced', label: '社招' }, { value: 'unknown', label: '未识别' },
        ]} />
        <div className={cn("flex min-w-0 items-center gap-2", compact ? "col-span-2 justify-end xl:col-span-1" : "min-h-9 flex-wrap justify-between rounded-md border border-card-border bg-card px-3 py-1")}>
          {!compact && <span className="whitespace-nowrap text-xs font-bold text-muted">找到 {resultCount} 个 / 一共 {totalCount} 个</span>}
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className={cn("h-7 px-2", compact && "text-xs")}
            disabled={!hasActiveJobFilters(filters)}
            onClick={onReset}
          >
            <RotateCcw className="mr-1 h-3 w-3" />重置筛选
          </Button>
        </div>
      </div>
      {invalidSalary && <p className="mt-2 text-xs font-bold text-danger">最低工资不能比最高工资还高，改一下再筛。</p>}
    </div>
  )
}
