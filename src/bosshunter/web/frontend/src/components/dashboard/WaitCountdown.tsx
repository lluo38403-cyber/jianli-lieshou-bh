import { useEffect, useState} from 'react'
import { Hourglass} from 'lucide-react'

/** v54 图10：当前等待的倒计时状态，后端 task.progress["wait"] 里来。
* ends_at 是秒时间戳（与后端同一台机器，时钟一致）；归零/跳过后
* 后端清掉这个字段，前端就不再画倒计时。 */
export interface TaskWait {
kind: string
label?: string
ends_at: number
total_seconds: number
}

/** 秒数 → MM:SS，分钟不封顶（05:00、12:34 都能对齐显示）。 */
export function formatWaitRemaining(totalSeconds: number): string {
const seconds = Math.max(0, Math.ceil(totalSeconds))
const minutes = Math.floor(seconds / 60)
const rest = seconds % 60
return `${String(minutes).padStart(2, '0')}:${String(rest).padStart(2, '0')}`
}

const KIND_TEXT: Record<string, string> = {
interval: '距离投递下一个岗位还有',
round_rest: '投完一轮后的风控等待',
batch_rest: '风控等待中，稍后继续投递',
stagger: '切换平台，稍等一下',
cooldown: '采集到投递的安全冷却',
}

function waitKindText(kind: string): string {
return KIND_TEXT[kind]?? '等待中'
}

function waitEndsAtMs(wait: TaskWait): number {
const endsAt = wait.ends_at
return typeof endsAt === 'number' && Number.isFinite(endsAt)? endsAt * 1000: NaN
}

/**
* 投递等待倒计时卡片：大字 MM:SS + 细进度条 + 。
* 每秒自己 tick 一次，不靠后端轮询推；倒计时归零就自己消失（后端
* 也会在同一轮清掉 wait 字段）。点只跳过当前这一轮
* 等待，不停任务；新一轮等待开始后后端重新下发新的倒计时。
*/
export function WaitCountdown({
wait,
onSkip,
skipping,
}: {
wait: TaskWait
onSkip: () => void
skipping?: boolean
}) {
const [nowMs, setNowMs] = useState(() => Date.now())

useEffect(() => {
const timer = window.setInterval(() => setNowMs(Date.now()), 1000)
return () => window.clearInterval(timer)
}, [])

const endsAtMs = waitEndsAtMs(wait)
const remainingSeconds = Number.isFinite(endsAtMs)? Math.max(0, (endsAtMs - nowMs) / 1000): 0
if (remainingSeconds <= 0) {
return null
}
const totalSeconds = Math.max(1, wait.total_seconds || 0)
const remainRatio = Math.min(1, remainingSeconds / totalSeconds)
const label = (wait.label || '').trim()

return (
<div
className="rounded-2xl border border-primary/25 bg-primary/5 px-4 py-3"
role="status"
aria-label={`${waitKindText(wait.kind)} ${formatWaitRemaining(remainingSeconds)}`}
>
<div className="flex flex-wrap items-center justify-between gap-3">
<div className="flex items-center gap-3">
<span className="flex h-9 w-9 items-center justify-center rounded-full bg-primary/10 text-primary">
<Hourglass className="h-5 w-5" />
</span>
<div>
<p className="text-sm font-bold text-primary">{waitKindText(wait.kind)}</p>
{label && <p className="mt-0.5 text-xs text-muted">{label}</p>}
</div>
</div>
<div className="flex items-center gap-3">
<span className="font-mono text-3xl font-black tabular-nums tracking-wide text-primary">
{formatWaitRemaining(remainingSeconds)}
</span>
<button
type="button"
className="rounded-full bg-primary px-4 py-2 text-sm font-bold text-primary-foreground transition hover:opacity-90 disabled:opacity-50"
onClick={onSkip}
disabled={skipping}
>
{skipping? '正在跳过…': '跳过等待'}
</button>
</div>
</div>
<div className="mt-3 h-1.5 overflow-hidden rounded-full bg-primary/15" aria-hidden="true">
<div
className="h-full rounded-full bg-primary transition-[width] duration-1000 ease-linear"
style={{ width: `${Math.round(remainRatio * 100)}%`}}
/>
</div>
</div>
)
}
