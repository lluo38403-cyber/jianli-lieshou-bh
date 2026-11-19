import { useEffect, useRef, useState } from 'react'
import { formatUpdateSize, type UpdateState } from '@/hooks/useUpdate'
import { Button } from '@/components/ui/button'

/** “跳过此版本”记在这里：只存版本号字符串，下次启动见到同一版就不再弹。 */
export const SKIPPED_UPDATE_VERSION_KEY = 'jianli-lieshou-skipped-update-version'

export function getSkippedUpdateVersion(): string {
  try {
    return window.localStorage.getItem(SKIPPED_UPDATE_VERSION_KEY) || ''
  } catch {
    return ''
  }
}

export function setSkippedUpdateVersion(version: string) {
  try {
    window.localStorage.setItem(SKIPPED_UPDATE_VERSION_KEY, version)
  } catch {
    // 记不住就算了，下次启动再弹一次，不影响正常用。
  }
}

interface UpdateModalProps {
  update: UpdateState
  /** 手动点了“检查更新”的信号（时间戳）：变一次就代表手动查了一次，
   * 有新版时忽略跳过记录也要弹；没新版时不弹，轻提示由页面自己显示。 */
  manualCheckSignal?: number
}

/**
 * 新版弹窗：右下角浮动卡片，不用全屏遮罩，页面其余部分随时能点、能继续用。
 * 与首页横幅共用同一份 useUpdate 状态，不另起检查/下载流程。
 */
export function UpdateModal({ update, manualCheckSignal = 0 }: UpdateModalProps) {
  const { info, currentVersion, phase, progress, error, dismissed, checking } = update
  const [open, setOpen] = useState(false)
  const [closedForVersion, setClosedForVersion] = useState('')
  const autoOpenedForRef = useRef('')
  const manualHandledRef = useRef('')

  const version = info?.version || ''

  // 启动自动检查出新版：没跳过、横幅没被“先不换”掉、这次没关过，就弹一次。
  useEffect(() => {
    if (!info || !version) return
    if (phase !== 'idle' && phase !== 'checking') return
    if (dismissed) return
    if (closedForVersion === version) return
    if (autoOpenedForRef.current === version) return
    if (getSkippedUpdateVersion() === version) return
    autoOpenedForRef.current = version
    setOpen(true)
  }, [info, version, phase, dismissed, closedForVersion])

  // 手动检查出新版：忽略跳过记录和“先不换”，也要弹。
  useEffect(() => {
    if (!manualCheckSignal || !info || !version) return
    const key = `${manualCheckSignal}:${version}`
    if (manualHandledRef.current === key) return
    manualHandledRef.current = key
    setClosedForVersion('')
    setOpen(true)
  }, [manualCheckSignal, info, version])

  if (!open || !info) return null

  const sizeText = formatUpdateSize(info.size)
  const closeForNow = () => {
    // “稍后再说”：只关这一次，不记任何东西，下次启动该弹还弹。
    setClosedForVersion(version)
    setOpen(false)
  }
  const skipVersion = () => {
    setSkippedUpdateVersion(version)
    setClosedForVersion(version)
    setOpen(false)
  }

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
      data-testid="update-modal"
      role="dialog"
      aria-label="有新版本了"
      className="fixed bottom-4 right-4 z-50 w-[calc(100vw-2rem)] max-w-sm rounded-2xl border border-primary/30 bg-card p-4 shadow-xl"
    >
      <div className="flex items-start justify-between gap-2">
        <p className="text-base font-semibold text-foreground">有新版本了</p>
        <button
          type="button"
          aria-label="关掉"
          onClick={closeForNow}
          className="shrink-0 rounded-lg px-2 py-1 text-sm text-muted hover:bg-secondary hover:text-foreground"
        >
          ✕
        </button>
      </div>

      <p className="mt-1 text-sm text-foreground">
        现在是 v{currentVersion || '…'}，最新是 v{info.version}
      </p>
      <p className="mt-1 text-sm text-muted">
        {info.notes ? info.notes : '修了一些小问题，用起来更稳。'}
        {sizeText ? `大概 ${sizeText}，` : ''}不用重新下载，点一下就能换上。
      </p>
      {phaseText ? (
        <p className="mt-2 text-sm font-medium text-foreground">{phaseText}</p>
      ) : null}
      {(phase === 'downloading' || phase === 'verifying') && (
        <div className="mt-3 h-2 overflow-hidden rounded-full bg-surface">
          <div
            data-testid="update-modal-progress"
            className="h-full rounded-full bg-primary transition-all duration-300"
            style={{ width: `${Math.max(2, Math.min(100, progress))}%` }}
          />
        </div>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-2">
        {phase === 'idle' || phase === 'checking' ? (
          <Button data-testid="update-modal-download" onClick={() => void update.start()} disabled={checking}>
            前往下载
          </Button>
        ) : null}
        {phase === 'downloading' || phase === 'verifying' ? (
          <span className="inline-flex items-center gap-2 rounded-full bg-surface px-3 py-1.5 text-sm text-muted">
            <span className="h-2 w-2 animate-pulse rounded-full bg-primary" />
            {phase === 'downloading' ? '下载中…' : '检查中…'}
          </span>
        ) : null}
        {phase === 'ready' ? (
          <Button data-testid="update-modal-apply" onClick={() => void update.apply()}>
            立即更新
          </Button>
        ) : null}
        {phase === 'applying' ? (
          <span className="inline-flex items-center gap-2 rounded-full bg-surface px-3 py-1.5 text-sm text-muted">
            <span className="h-2 w-2 animate-pulse rounded-full bg-primary" />
            换新版本中…
          </span>
        ) : null}
        {phase === 'error' ? (
          <Button variant="secondary" onClick={() => void update.retry()}>
            再试一次
          </Button>
        ) : null}
        {(phase === 'idle' || phase === 'checking') && (
          <Button variant="secondary" onClick={skipVersion}>
            跳过此版本
          </Button>
        )}
        {phase !== 'applying' && (
          <Button variant="ghost" onClick={closeForNow}>
            稍后再说
          </Button>
        )}
      </div>
      <p className="mt-3 text-xs text-muted">更新时你的设置和投递记录都不会动，换完自动重新打开。</p>
    </div>
  )
}
