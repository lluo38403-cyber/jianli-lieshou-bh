import { formatUpdateSize, type UpdateState } from '@/hooks/useUpdate'
import { Button } from '@/components/ui/button'

interface UpdateBannerProps {
  update: UpdateState
}

/** 首页顶部的新版横幅：有新版才出现，大白话，不用重新下载。 */
export function UpdateBanner({ update }: UpdateBannerProps) {
  const { info, phase, progress, error, dismissed, checking } = update

  if (dismissed || !info) return null

  const sizeText = formatUpdateSize(info.size)
  const phaseText =
    phase === 'downloading'
      ? `正在下载新版本…${progress > 0 ? ` ${progress}%` : ''}`
      : phase === 'verifying'
        ? '正在检查下载对不对，等一下…'
        : phase === 'ready'
          ? '新版本下载好了，点一下马上换上'
          : phase === 'applying'
            ? '正在换上新版本，面板马上重新打开…'
            : phase === 'error'
              ? error || '这次没换上，稍后再试。'
              : null

  return (
    <div
      data-testid="update-banner"
      role="status"
      className="animate-fade-up rounded-2xl border border-primary/30 bg-gradient-to-r from-primary/15 via-card to-card p-4 shadow-sm sm:p-5"
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <p className="text-base font-semibold text-foreground">
            有新版本了，点一下就能换上，不用重新下载
          </p>
          <p className="mt-1 text-sm text-muted">
            新版本 {info.version}
            {sizeText ? `，大概 ${sizeText}` : ''}
            {info.notes ? ` — ${info.notes}` : ''}
            {phaseText ? (
              <>
                <br />
                <span className="font-medium text-foreground">{phaseText}</span>
              </>
            ) : null}
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          {phase === 'idle' || phase === 'checking' ? (
            <>
              <Button data-testid="update-start" onClick={() => void update.start()}>
                立即更新
              </Button>
              <Button variant="secondary" onClick={update.dismiss}>
                先不换
              </Button>
            </>
          ) : null}
          {phase === 'downloading' || phase === 'verifying' ? (
            <span className="inline-flex items-center gap-2 rounded-full bg-surface px-3 py-1.5 text-sm text-muted">
              <span className="h-2 w-2 animate-pulse rounded-full bg-primary" />
              {phase === 'downloading' ? '下载中…' : '检查中…'}
            </span>
          ) : null}
          {phase === 'ready' ? (
            <Button data-testid="update-apply" onClick={() => void update.apply()}>
              马上换上
            </Button>
          ) : null}
          {phase === 'applying' ? (
            <span className="inline-flex items-center gap-2 rounded-full bg-surface px-3 py-1.5 text-sm text-muted">
              <span className="h-2 w-2 animate-pulse rounded-full bg-primary" />
              换新版本中…
            </span>
          ) : null}
          {phase === 'error' ? (
            <>
              <Button variant="secondary" onClick={() => void update.retry()}>
                再试一次
              </Button>
              <Button variant="ghost" onClick={update.dismiss}>
                先不管
              </Button>
            </>
          ) : null}
        </div>
      </div>
      {(phase === 'downloading' || phase === 'verifying') && (
        <div className="mt-3 h-2 overflow-hidden rounded-full bg-surface">
          <div
            data-testid="update-progress"
            className="h-full rounded-full bg-primary transition-all duration-300"
            style={{ width: `${Math.max(2, Math.min(100, progress))}%` }}
          />
        </div>
      )}
      {checking && phase === 'idle' ? (
        <p className="mt-2 text-xs text-muted">正在看有没有新版本…</p>
      ) : null}
    </div>
  )
}
