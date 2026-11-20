import { useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { PLATFORM_IDS, PLATFORM_LABELS, openPlatformLogin, type PlatformId } from '@/lib/platforms'
import { getPlatformLoginStatusLabel } from '@/lib/status'
import { getEffectiveLoginStatus, isPlatformLoginStale, type EffectiveLoginStatus } from '@/lib/platformLogin'
import type { PlatformLoginInfo } from '@/hooks/useDashboard'
import { cn } from '@/lib/utils'

const EFFECTIVE_LABELS: Record<EffectiveLoginStatus, string> = {
  logged_in: getPlatformLoginStatusLabel('logged_in'),
  logged_out: getPlatformLoginStatusLabel('logged_out'),
  unknown: getPlatformLoginStatusLabel('unknown'),
  checking: '正在确认',
}

export function PlatformStatusCard({ platformLogin, browserName = '浏览器', onRefresh, refreshing = false, refreshedAt = null, refreshError = null, checking = false }: {
  platformLogin?: Partial<Record<string, PlatformLoginInfo>> | null
  browserName?: string
  /** 只刷新各平台登录状态（不整页重载）；没传时按钮置灰。 */
  onRefresh?: () => void
  /** 手动刷新进行中：按钮置灰防重复点。 */
  refreshing?: boolean
  /** 最近一次手动刷新成功的时间。 */
  refreshedAt?: Date | null
  /** 最近一次手动刷新的失败原因（白话一句），有值时优先显示。 */
  refreshError?: string | null
  /** 正在等一次检查结果（挂载诊断 / 手动刷新进行中）：过期/没查过的
   * 行先显示“正在确认”，新鲜结论不动，不来回闪。 */
  checking?: boolean
}) {
  // “重新登录”点开的登录页落在了当前浏览器（后端 /api/browser/open
  // 没接通才回退到这一步）：登录态在程序的浏览器里检测不到，登好
  // 后要点右上角“刷新”再查一次；提示在下次刷新成功后消失。
  const [loginOpenedElsewhere, setLoginOpenedElsewhere] = useState(false)
  useEffect(() => { if (refreshedAt) setLoginOpenedElsewhere(false) }, [refreshedAt])

  const nowMs = Date.now()
  const tiles = PLATFORM_IDS.map(platform => {
    const info = platformLogin?.[platform]
    // 只有新鲜的行才配展示 detail 和“几点查的”：过期行是上次启动
    // 留下的旧值，文案和时间都不再当成事实。
    const freshInfo = info && !isPlatformLoginStale(info, nowMs) ? info : undefined
    return { platform, info, freshInfo, status: getEffectiveLoginStatus(info, nowMs, checking) }
  })
  const loggedOut = tiles.filter(tile => tile.status === 'logged_out')
  // 四个平台提示完全一样时（比如都没连上浏览器），只在卡片顶部显示一次。
  const allDetails = tiles.map(tile => tile.freshInfo?.detail)
  const sharedDetail = allDetails.every(detail => detail && detail === allDetails[0]) ? allDetails[0] as string : null
  const allUnknown = tiles.every(tile => tile.status === 'unknown' || tile.status === 'checking')
  const refreshDisabled = refreshing || checking || !onRefresh

  const handleOpenLogin = (platform: PlatformId) => {
    void openPlatformLogin(platform).then(opened => { if (!opened) setLoginOpenedElsewhere(true) })
  }

  return (
    <section aria-label="各平台登录状态" className="animate-fade-up rounded-2xl border border-card-border bg-card p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-black text-foreground">各平台登录状态</h2>
        <div className="flex flex-wrap items-center gap-2">
          {loggedOut.length > 0 && (
            <span className="rounded-full bg-danger-soft px-2 py-0.5 text-xs font-bold text-danger">
              {loggedOut.length} 个平台掉线了，先重新登录再跑
            </span>
          )}
          <button
            type="button"
            onClick={() => { onRefresh?.() }}
            disabled={refreshDisabled}
            aria-label="刷新登录状态"
            title="只重新查一遍各平台登录状态，不会重跑任务"
            className="flex h-7 items-center gap-1 rounded-lg border border-card-border bg-surface px-2 text-xs font-bold text-primary hover:border-primary/40 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <RefreshCw className={cn('h-3.5 w-3.5', refreshing && 'animate-spin')} />
            {refreshing ? '正在刷新…' : '刷新'}
          </button>
        </div>
      </div>
      {(refreshing || refreshedAt || refreshError) && (
        <p role="status" className={cn('mt-1 text-[11px] leading-4', refreshError ? 'text-danger' : 'text-muted')}>
          {refreshing
            ? '正在刷新登录状态…'
            : refreshError
              ? refreshError
              : refreshedAt
                ? `已刷新 ${refreshedAt.toLocaleTimeString('zh-CN', { hour12: false })}`
                : null}
        </p>
      )}
      {loginOpenedElsewhere && (
        <p className="mt-1 text-[11px] leading-4 text-muted">
          登录页在当前浏览器里打开了（程序的浏览器没连上），登好后点右上角“刷新”再查一次。
        </p>
      )}
      {sharedDetail && <p className="mt-2 text-[11px] leading-4 text-muted">{sharedDetail}</p>}
      <div className="mt-2 grid grid-cols-2 gap-2 md:grid-cols-4">
        {tiles.map(({ platform, freshInfo, status }) => {
          const isOut = status === 'logged_out'
          const isChecking = status === 'checking'
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
                  (status === 'unknown' || isChecking) && 'bg-card text-muted',
                )}>
                  {EFFECTIVE_LABELS[status]}
                </span>
              </div>
              {!sharedDetail && freshInfo?.detail && <p className="mt-1 text-[11px] leading-4 text-muted">{freshInfo.detail}</p>}
              {freshInfo?.checked_at && status !== 'unknown' && !isChecking && (
                <p className="mt-1 text-[11px] text-muted">
                  {new Date(freshInfo.checked_at).toLocaleString('zh-CN', { hour12: false })}查的
                </p>
              )}
              {isOut && (
                <button
                  type="button"
                  onClick={() => handleOpenLogin(platform as PlatformId)}
                  className="mt-2 w-full rounded-lg bg-danger px-2 py-1.5 text-xs font-bold text-white hover:opacity-90"
                >
                  重新登录{PLATFORM_LABELS[platform]}
                </button>
              )}
              {(status === 'unknown' || isChecking) && (
                <button
                  type="button"
                  onClick={() => handleOpenLogin(platform as PlatformId)}
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
      {loggedOut.length === 0 && allUnknown && !checking && !refreshError && (
        <p className="mt-2 text-xs leading-5 text-muted">
          还没确认登录状态，点右上角“刷新”查一次。
        </p>
      )}
    </section>
  )
}
