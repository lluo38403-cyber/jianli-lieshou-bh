import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { ModalPortal } from '@/components/ui/modal-portal'
import { skipCloseConfirm } from '@/lib/closeConfirm'

interface ExitAppDialogProps {
  onStay: () => void
}

/**
 * “退出程序”确认弹窗（v25）：文案用 lucky 原话。
 * 是 → 先置位跳过 beforeunload（不然代码关窗会被自己的确认拦住），
 * 再调 POST /api/exit（后端先停任务、停 runtime、停我们拉的 Chrome
 * 再退出进程），最后关掉本窗口。否 → 只关弹窗，什么都不做。
 * /api/exit 调不通（例如服务已经不在了）也照常关窗，不卡住。
 */
export function ExitAppDialog({ onStay }: ExitAppDialogProps) {
  const [exiting, setExiting] = useState(false)
  const [error, setError] = useState('')

  const handleExit = async () => {
    setExiting(true)
    setError('')
    skipCloseConfirm()
    try {
      await fetch('/api/exit', { method: 'POST' })
    } catch {
      // 服务已经不在或调不通：照样关窗，不卡住。
    }
    try {
      window.close()
    } catch {
      // 普通浏览器标签页不让脚本关窗：留一句提示，用户自己关。
      setError('程序正在退出，窗口没能自动关掉，关掉这个窗口就行。')
      setExiting(false)
    }
  }

  // 同 UnsavedChangesDialog：Sidebar 本来就在 .animate-page-in 外面，定位原本
  // 是对的；走 ModalPortal 加固，并给面板补 max-h + 内部滚动防小屏溢出。
  return (
    <ModalPortal>
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/35 p-4"
      role="dialog"
      aria-modal="true"
      aria-label="退出应用程序"
    >
      <div className="max-h-[calc(100dvh-2rem)] w-full max-w-sm overflow-y-auto rounded-2xl border border-card-border bg-card p-5 shadow-xl">
        <p className="text-sm font-black text-foreground">是否结束该应用程序，这可能会导致当前进行中的任务结束</p>
        <p className="mt-2 text-xs leading-5 text-muted">
          点“是”会停掉进行中的任务，关掉浏览器运行组件和程序自己拉起的浏览器窗口，再退出程序；之后安装文件夹就能直接删掉，不用重启。
        </p>
        {error && (
          <p className="mt-3 rounded-lg bg-danger-soft px-3 py-2 text-xs text-danger">{error}</p>
        )}
        <div className="mt-4 flex flex-col gap-2">
          <Button onClick={() => void handleExit()} disabled={exiting}>
            {exiting ? '正在退出…' : '是'}
          </Button>
          <Button variant="ghost" onClick={onStay} disabled={exiting}>
            否
          </Button>
        </div>
      </div>
    </div>
    </ModalPortal>
  )
}
