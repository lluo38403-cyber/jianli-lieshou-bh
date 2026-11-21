import { useEffect, useRef, useState } from 'react'
import { formatUpdateNotes, formatUpdateSize, getUpdateErrorCopy, type UpdateState } from '@/hooks/useUpdate'
import { Button } from '@/components/ui/button'
import { ModalPortal } from '@/components/ui/modal-portal'

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
  /** 弹窗开关变化时通知父级：首页用它在弹窗开着时收起横幅，
   * 免得同一件事同时出现两块（卡片 + 悬浮窗）。 */
  onOpenChange?: (open: boolean) => void
}

/**
 * 新版弹窗：右下角浮动卡片，不用全屏遮罩，页面其余部分随时能点、能继续用。
 * 与首页横幅共用同一份 useUpdate 状态，不另起检查/下载流程。
 */
export function UpdateModal({ update, manualCheckSignal = 0, onOpenChange }: UpdateModalProps) {
  const { info, currentVersion, phase, progress, error, errorKind, manualDownloadUrl, dismissed, checking } = update
  const [open, setOpen] = useState(false)
  const [closedForVersion, setClosedForVersion] = useState('')
  const autoOpenedForRef = useRef('')
  const manualHandledRef = useRef('')

  const version = info?.version || ''

  useEffect(() => {
    onOpenChange?.(open)
  }, [open, onOpenChange])

  // 启动自动检查出新版：没跳过、横幅没被“先不换”掉、这次没关过，就弹一次。
  // 下载好了（ready）才切回首页也弹一次：不然用户只看到横幅，不知道已经能换了。
  useEffect(() => {
    if (!info || !version) return
    if (phase !== 'idle' && phase !== 'checking' && phase !== 'ready') return
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

  // 失败按下载 / 校验 / 替换三步给说法；分不出来的才直接显示原话。
  const errorCopy = phase === 'error' ? getUpdateErrorCopy(errorKind ?? 'other') : null
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
              ? errorCopy?.title || error || '这次没换上，稍后再试。'
              : null

  // 定位：经 ModalPortal 挂到 body（见 modal-portal.tsx），fixed bottom-4
  // 才真正相对视口右下角，不会被 .animate-page-in 的 transform 拽到页底。
  // 布局：卡片 flex 纵向分栏，只有正文区滚动，按钮行固定在底部不跟内容走，
  // 内容再长“前往下载 / 立即更新”也始终可见可点。
  return (
    <ModalPortal>
    <div
      data-testid="update-modal"
      role="dialog"
      aria-label="有新版本了"
      className="fixed inset-x-4 bottom-4 z-50 flex max-h-[calc(100dvh-2rem)] flex-col overflow-hidden rounded-2xl border border-primary/30 bg-card p-4 shadow-xl sm:inset-x-auto sm:bottom-4 sm:right-4 sm:w-[26rem] sm:max-w-[calc(100vw-2rem)]"
    >
      <div data-testid="update-modal-body" className="min-h-0 flex-1 overflow-y-auto">
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
      <p className="mt-1 line-clamp-3 text-sm text-muted">
        {formatUpdateNotes(info.notes)}
        {sizeText ? `大概 ${sizeText}，` : ''}不用重新下载，点一下就能换上。
      </p>
      {phaseText ? (
        <p className="mt-2 text-sm font-medium text-foreground">{phaseText}</p>
      ) : null}
      {phase === 'error' && errorCopy?.reason ? (
        <p className="mt-1 text-sm text-muted">{errorCopy.reason}</p>
      ) : null}
      {phase === 'error' ? (
        <p className="mt-1 text-sm text-muted">
          {errorCopy?.tip || '点“再试一次”。'}
          {info.downloadCached ? '安装包已经下好了，再试一次不用重新下载。' : null}
          {manualDownloadUrl
            ? '也可以点“手动下载”去店铺拿最新安装包。'
            : '也可以去群里或店铺拿最新安装包。'}
        </p>
      ) : null}
      {phase === 'error' && !manualDownloadUrl ? (
        <p className="mt-1 text-xs text-muted">手动换：下载后解压，覆盖安装目录，别动 config.yaml 和 data 文件夹。</p>
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
      </div>

      <div data-testid="update-modal-footer" className="mt-4 flex shrink-0 flex-wrap items-center gap-2 border-t border-card-border pt-3">
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
          <>
            <Button variant="secondary" onClick={() => void update.retry()}>
              再试一次
            </Button>
            {manualDownloadUrl ? (
              <Button
                variant="secondary"
                data-testid="update-modal-manual-download"
                onClick={() => window.open(manualDownloadUrl, '_blank', 'noopener,noreferrer')}
              >
                手动下载
              </Button>
            ) : null}
          </>
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
        <p className="mt-1 w-full text-xs text-muted">更新时你的设置和投递记录都不会动，换完自动重新打开。</p>
      </div>
    </div>
    </ModalPortal>
  )
}
