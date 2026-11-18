import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import type { HistoryItem } from '@/hooks/useDashboard'

interface RecentActivityProps {
  data: HistoryItem[]
}

const ACTION_LABELS: Record<string, string> = {
  scrape: '找到的',
  scored: '打分',
  sent: '投出去',
  manual_sent: '你手动投的',
  replied: '回了消息',
  hr_reply_detected: 'HR 回消息了',
  auto_replied: '自动回了',
  error: '出问题了',
  approved: '确认了',
  filtered: '筛掉的',
  resume_sent: '简历',
}

export function RecentActivity({ data }: RecentActivityProps) {
  const formatTime = (dateStr: string) => {
    if (!dateStr) return ''
    const d = new Date(dateStr)
    return `${(d.getMonth() + 1).toString().padStart(2, '0')}-${d.getDate().toString().padStart(2, '0')} ${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`
  }

  if (!data.length) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="font-black text-foreground">最近干了啥</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted">最近还没动静</p>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="font-black text-foreground">最近活动</CardTitle>
      </CardHeader>
      <CardContent className="p-4 pt-0">
        <div className="space-y-2">
          {data.slice(0, 3).map(item => (
            <div key={item.id} className="flex items-start gap-2">
              <div className="w-1.5 h-1.5 rounded-full bg-primary mt-2 shrink-0" />
              <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-3 gap-y-0.5">
                <div className="flex min-w-0 flex-1 items-center gap-2">
                  <Badge variant={item.action as any} className="text-[10px] px-1.5 py-0">
                    {ACTION_LABELS[item.action] || item.action}
                  </Badge>
                  <span className="text-xs font-bold text-foreground truncate">
                    {item.company} · {item.title}
                  </span>
                </div>
                <time className="shrink-0 text-[11px] text-muted" dateTime={item.created_at}>{formatTime(item.created_at)}</time>
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  )
}
