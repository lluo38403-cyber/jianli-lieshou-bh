import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { ModalPortal } from '@/components/ui/modal-portal'

interface ExitAppDialogProps {
  onStay: () => void
}

/**
 * “退出程序”确认弹窗（v25，v33 精简文案）。
 * 是 → 调 POST /api/exit（后端先停任务、停 runtime、停我们拉的 Chrome、
 * 再扫安装目录残留，最后退出进程），最后关掉本窗口。否 → 只关弹窗，
 * 什么都不做。
 * /api/exit 调不通（例如服务已经不在了）也照常关窗，不卡住。
 * v33：文案只讲“点‘是’会发生什么”——立即终止所有进行中的任务；
 * “之后安装文件夹就能直接删掉，不用重启”那句删了：它跟退出没关系，
 * 反而让人困惑（删文件夹是另一回事，退出程序不用提它）。
 * v34：窗口右上角 X 不走这个弹窗，也不弹任何框——前端不再注册
 * beforeunload，X 直接关窗，后端监视线程调同一套退出清理
 * （server._perform_exit_cleanup），做的事与这里点“是”完全一样，
 * 只是少一次确认。
 */
export function ExitAppDialog({ onStay }: ExitAppDialogProps) {
  const [exiting, setExiting] = useState(false)
  const [error, setError] = useState('')

  const handleExit = async () => {
    setExiting(true)
    setError('')
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
          点“是”会立即终止所有进行中的任务，关掉浏览器运行组件和程序自己拉起的浏览器窗口，然后退出程序。
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
