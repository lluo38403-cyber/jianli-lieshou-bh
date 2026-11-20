import { PLATFORM_IDS, PLATFORM_LABELS, openPlatformLogin, type PlatformId } from '@/lib/platforms'
import { getPlatformLoginStatusLabel, type PlatformLoginStatus } from '@/lib/status'
import type { PlatformLoginInfo } from '@/hooks/useDashboard'
import { cn } from '@/lib/utils'

function statusOf(info: PlatformLoginInfo | undefined): PlatformLoginStatus {
  if (!info) return 'unknown'
  if (info.status === 'logged_in' || info.status === 'logged_out') return info.status
  return 'unknown'
}

export function PlatformStatusCard({ platformLogin, browserName = '浏览器' }: {
  platformLogin?: Partial<Record<string, PlatformLoginInfo>> | null
  browserName?: string
}) {
  const loggedOut = PLATFORM_IDS.filter(platform => statusOf(platformLogin?.[platform]) === 'logged_out')
  // 四个平台提示完全一样时（比如都没连上浏览器），只在卡片顶部显示一次。
  const allDetails = PLATFORM_IDS.map(platform => platformLogin?.[platform]?.detail)
  const sharedDetail = allDetails.every(detail => detail && detail === allDetails[0]) ? allDetails[0] as string : null
  return (
    <section aria-label="各平台登录状态" className="animate-fade-up rounded-2xl border border-card-border bg-card p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-black text-foreground">各平台登录状态</h2>
        {loggedOut.length > 0 && (
          <span className="rounded-full bg-danger-soft px-2 py-0.5 text-xs font-bold text-danger">
            {loggedOut.length} 个平台掉线了，先重新登录再跑
          </span>
        )}
      </div>
      {sharedDetail && <p className="mt-2 text-[11px] leading-4 text-muted">{sharedDetail}</p>}
      <div className="mt-2 grid grid-cols-2 gap-2 md:grid-cols-4">
        {PLATFORM_IDS.map(platform => {
          const info = platformLogin?.[platform]
          const status = statusOf(info)
          const isOut = status === 'logged_out'
          return (
            <div
              key={platform}
              className={cn(
                'rounded-xl border p-2.5',
                isOut ? 'border-danger-border bg-danger-soft' : 'border-card-border bg-surface',
              )}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs font-black text-foreground">{PLATFORM_LABELS[platform]}</span>
                <span className={cn(
                  'shrink-0 rounded-full px-2 py-0.5 text-[11px] font-bold',
                  status === 'logged_in' && 'bg-success-soft text-success',
                  isOut && 'bg-danger px-2 py-0.5 text-white',
                  status === 'unknown' && 'bg-card text-muted',
                )}>
                  {getPlatformLoginStatusLabel(status)}
                </span>
              </div>
              {!sharedDetail && info?.detail && <p className="mt-1 text-[11px] leading-4 text-muted">{info.detail}</p>}
              {info?.checked_at && status !== 'unknown' && (
                <p className="mt-1 text-[11px] text-muted">
                  {new Date(info.checked_at).toLocaleString('zh-CN', { hour12: false })}查的
                </p>
              )}
              {isOut && (
                <button
                  type="button"
                  onClick={() => { void openPlatformLogin(platform as PlatformId) }}
                  className="mt-2 w-full rounded-lg bg-danger px-2 py-1.5 text-xs font-bold text-white hover:opacity-90"
                >
                  重新登录{PLATFORM_LABELS[platform]}
                </button>
              )}
              {status === 'unknown' && (
                <button
                  type="button"
                  onClick={() => { void openPlatformLogin(platform as PlatformId) }}
                  className="mt-2 w-full rounded-lg border border-card-border bg-card px-2 py-1.5 text-xs font-bold text-primary hover:border-primary/40"
                >
                  去{PLATFORM_LABELS[platform]}登录
                </button>
              )}
            </div>
          )
        })}
      </div>
      {loggedOut.length > 0 && (
        <p className="mt-2 text-xs leading-5 text-danger">
          掉线的平台跑起来会失败，点“重新登录”在{browserName}里登好，再回来点“检查能不能跑”。
        </p>
      )}
    </section>
  )
}
