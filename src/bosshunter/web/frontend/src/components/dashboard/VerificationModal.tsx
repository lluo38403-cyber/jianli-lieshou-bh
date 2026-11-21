import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { PLATFORM_LABELS } from '@/lib/platforms'
import type { VerificationHold } from '@/hooks/useDashboard'

interface VerificationModalProps {
  hold: VerificationHold | null
  onContinue: (holdId: number) => Promise<void>
  onStop: () => Promise<void>
  canStop: boolean
}

/** 验证码接管弹窗：投递撞上平台安全验证时停下来，等用户在浏览器里手动验证完点继续。 */
export function VerificationModal({ hold, onContinue, onStop, canStop }: VerificationModalProps) {
  const [busy, setBusy] = useState(false)
  const [stopping, setStopping] = useState(false)
  // H-7：后端失败不再静默吞掉——弹窗内红字显示原因，并告诉用户下一步。
  const [error, setError] = useState<string | null>(null)
  if (!hold) return null
  const platformName = PLATFORM_LABELS[hold.platform] ?? hold.platform

  const handleContinue = async () => {
    if (busy) return
    setBusy(true)
    setError(null)
    try {
      await onContinue(hold.id)
    } catch (cause) {
      // resolveVerification 在后端 !ok 时抛错（hold 已过期、网络断开等）：
      // 弹窗原样停留、按钮恢复可点是不够的，必须把原因写出来。
      setError(cause instanceof Error ? cause.message : '没能继续投递')
    } finally {
      setBusy(false)
    }
  }

  const handleStop = async () => {
    if (stopping) return
    setStopping(true)
    setError(null)
    try {
      await onStop()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '没能停下来')
    } finally {
      setStopping(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-4" role="alertdialog" aria-modal="true" aria-label="平台安全验证">
      <div className="w-full max-w-md rounded-2xl border border-border bg-card p-5 shadow-2xl">
        <h2 className="text-base font-bold">触发平台安全验证</h2>
        <p className="mt-2 text-sm leading-6 text-muted">
          {platformName}弹出了安全验证，投递已经停下来了，不会继续硬投。请在浏览器里手动完成验证（滑块、短信都行），验证通过后点下面的“继续投递”。
        </p>
        {hold.url ? (
          <p className="mt-3 break-all rounded-lg bg-muted/60 px-3 py-2 text-xs leading-5 text-muted">
            验证页面：{hold.url}
          </p>
        ) : null}
        {error ? (
          <p role="alert" className="mt-3 rounded-lg bg-danger-soft px-3 py-2 text-xs leading-5 text-danger">
            没成功：{error}。可以再点一次；如果一直不行，先去浏览器里确认验证是不是真的通过了。
          </p>
        ) : null}
        <div className="mt-5 flex flex-col gap-2">
          <Button onClick={() => void handleContinue()} disabled={busy}>
            {busy ? '正在继续…' : '我已完成验证，继续投递'}
          </Button>
          {canStop ? (
            <Button variant="secondary" onClick={() => void handleStop()} disabled={stopping}>
              {stopping ? '正在停止…' : '停止投递'}
            </Button>
          ) : null}
        </div>
      </div>
    </div>
  )
}
