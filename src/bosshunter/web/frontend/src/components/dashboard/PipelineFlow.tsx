import { Search, Bot, MessageSquare, CheckCircle, Send, Eye } from 'lucide-react'

const steps = [
  { icon: Search, label: '找岗位', desc: '去平台搜' },
  { icon: Bot, label: '打分', desc: '看看合不合适' },
  { icon: MessageSquare, label: '写打招呼', desc: '按岗位现写' },
  { icon: CheckCircle, label: '自动把关', desc: '够分就投' },
  { icon: Send, label: '投出去', desc: '自动投递' },
  { icon: Eye, label: '盯回复', desc: 'HR 回了就提醒' },
]

export function PipelineFlow() {
  return (
    <div className="rounded-3xl border border-card-border bg-surface p-6 shadow-[0_2px_16px_-6px_hsl(var(--primary)/0.12)]">
      <h3 className="text-sm font-black text-foreground mb-4">从找岗到拿到回复，一共 6 步</h3>
      <div className="grid grid-cols-3 gap-x-3 gap-y-5 lg:grid-cols-6">
        {steps.map((step, i) => (
          <div key={step.label} className="relative min-w-0 animate-pop" style={{ animationDelay: `${i * 90}ms` } as React.CSSProperties}>
            <div className="flex flex-col items-center">
              <div className="mb-2 flex h-12 w-12 items-center justify-center rounded-2xl border border-card-border bg-card shadow-sm transition-all duration-200 hover:-translate-y-1 hover:border-primary/60 hover:shadow-lg hover:shadow-primary/20">
                <step.icon className="h-5 w-5 text-primary" />
              </div>
              <span className="text-xs font-black text-foreground">{step.label}</span>
              <span className="mt-0.5 text-[10px] text-muted">{step.desc}</span>
            </div>
            {i < steps.length - 1 && (
              <div className="flow-line absolute left-[calc(50%+28px)] right-[calc(-50%+16px)] top-6 hidden h-0.5 lg:block" />
            )}
          </div>
        ))}
      </div>
      <p className="mt-4 text-xs text-muted">
        点上面“开始自动找工作”，够分的岗位会自动投出去，不用你一个个点。
      </p>
    </div>
  )
}
