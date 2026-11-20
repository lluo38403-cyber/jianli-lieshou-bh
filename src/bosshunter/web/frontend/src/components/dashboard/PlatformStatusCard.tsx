import { useState } from 'react'
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
  // v27：登录按钮点一次就给明确反馈（正在打开 / 已打开 / 已切到打开的
  // 标签页 / 失败原因）。以前后端没接通会回退到 window.open，登录页
  // 落在面板自己的浏览器里、程序检测不到，还要用户手动点“刷新”；
  // 现在不回退了，失败就直接显示原因、让用户再点一次。
  const [loginBusy, setLoginBusy] = useState<PlatformId | null>(null)
  const [loginFeedback, setLoginFeedback] = useState<{ platform: PlatformId; message: string; ok: boolean } | null>(null)

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

  const handleOpenLogin = async (platform: PlatformId) => {
    if (loginBusy) return
    setLoginBusy(platform)
    setLoginFeedback({ platform, message: `正在打开工作浏览器，准备去${PLATFORM_LABELS[platform]}登录…`, ok: true })
    try {
      const result = await openPlatformLogin(platform)
      setLoginFeedback({
        platform,
        message: result.ok
          ? result.reusedTab
            ? `已经打开了${PLATFORM_LABELS[platform]}的标签页，直接切过去了。`
            : result.browserStarted
              ? `工作浏览器已启动，正在打开${PLATFORM_LABELS[platform]}登录页。`
              : `已在工作浏览器打开${PLATFORM_LABELS[platform]}登录页。`
          : result.message || '浏览器没连上，再点一次试试。',
        ok: result.ok,
      })
    } finally {
      setLoginBusy(null)
    }
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
      {loginFeedback && (
        <p role="status" className={cn('mt-1 text-[11px] leading-4', loginFeedback.ok ? 'text-muted' : 'text-danger')}>
          {loginFeedback.message}
        </p>
      )}
      {sharedDetail && <p className="mt-2 text-[11px] leading-4 text-muted">{sharedDetail}</p>}
      <p className="mt-2 text-[11px] leading-4 text-muted">点击下面任意一个“去登录”，会打开工作浏览器并跳转到对应招聘网站登录页。</p>
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
                  onClick={() => { void handleOpenLogin(platform as PlatformId) }}
                  disabled={loginBusy !== null}
                  className="mt-2 w-full rounded-lg bg-danger px-2 py-1.5 text-xs font-bold text-white hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {loginBusy === platform ? '正在打开…' : `重新登录${PLATFORM_LABELS[platform]}`}
                </button>
              )}
              {(status === 'unknown' || isChecking) && (
                <button
                  type="button"
                  onClick={() => { void handleOpenLogin(platform as PlatformId) }}
                  disabled={loginBusy !== null}
                  className="mt-2 w-full rounded-lg border border-card-border bg-card px-2 py-1.5 text-xs font-bold text-primary hover:border-primary/40 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {loginBusy === platform ? '正在打开…' : `去${PLATFORM_LABELS[platform]}登录`}
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
