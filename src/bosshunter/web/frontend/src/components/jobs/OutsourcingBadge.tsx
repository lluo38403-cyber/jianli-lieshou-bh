import type { Job } from '@/hooks/useDashboard'

type Props = {
  job: Pick<Job, 'outsourcing_level' | 'outsourcing_matches'>
}

/** The same evidence hint is visible wherever the user reviews a job. */
export function OutsourcingBadge({ job }: Props) {
  const level = job.outsourcing_level
  if (level !== 'confirmed' && level !== 'suspected') return null
  const label = level === 'confirmed' ? '外包' : '可能是外包'
  const matches = Array.isArray(job.outsourcing_matches)
    ? job.outsourcing_matches.filter((match): match is string => typeof match === 'string')
    : []
  const evidence = matches.length ? `命中：${matches.join('、')}` : '请核对岗位介绍和雇佣关系'
  return (
    <span
      className={`inline-flex shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-black ${level === 'confirmed'
        ? 'border-red-200 bg-red-50 text-red-700'
        : 'border-amber-200 bg-amber-50 text-amber-700'}`}
      title={`${evidence}。系统提醒，请你自己核对一下。`}
      aria-label={`${label}；${evidence}`}
    >
      {label}
    </span>
  )
}
