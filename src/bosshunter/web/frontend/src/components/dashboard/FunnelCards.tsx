import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { AnimatedNumber } from '@/components/AnimatedNumber'
import type { FunnelData } from '@/hooks/useDashboard'

interface FunnelCardsProps {
  data: FunnelData
}

const funnelSteps = [
  { key: '采集总数', label: '找到的岗位', color: 'text-info', bg: 'bg-info-soft' },
  { key: '初筛通过', label: '筛出来的', color: 'text-cyan', bg: 'bg-cyan-soft' },
  { key: 'AI评分', label: '打过分的', color: 'text-success', bg: 'bg-success-soft' },
  { key: '自动确认', label: '够格能投的', color: 'text-warning', bg: 'bg-warning-soft' },
]

export function FunnelCards({ data }: FunnelCardsProps) {
  return (
    <div className="grid grid-cols-4 gap-3">
      {funnelSteps.map((step, i) => {
        const count = data[step.key] || 0
        const prevCount = i === 0 ? count : (data[funnelSteps[i - 1].key] || 0)
        const rate = prevCount > 0 && i > 0 ? ((count / prevCount) * 100).toFixed(1) + '%' : ''

        return (
          <Card key={step.key} className="hover-lift animate-fade-up" style={{ animationDelay: `${i * 70}ms` } as React.CSSProperties}>
            <CardHeader className="pb-2 p-4">
              <CardTitle className="text-xs text-muted font-bold">{step.label}</CardTitle>
            </CardHeader>
            <CardContent className="p-4 pt-0">
              <div className={`text-2xl font-black tabular-nums ${step.color}`}>
                <AnimatedNumber value={count} />
              </div>
              {rate && <p className="text-xs text-muted mt-1">上一步留下来 {rate}</p>}
            </CardContent>
          </Card>
        )
      })}
    </div>
  )
}
