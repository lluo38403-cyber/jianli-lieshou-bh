import { Button } from '@/components/ui/button'
import { ModalPortal } from '@/components/ui/modal-portal'

interface UnsavedChangesDialogProps {
  saving: boolean
  error: string | null
  onSaveAndLeave: () => void
  onDiscardAndLeave: () => void
  onStay: () => void
}

/** 离开有未保存修改的设置页时的确认提醒：三个操作，文案按 lucky 原话。 */
export function UnsavedChangesDialog({ saving, error, onSaveAndLeave, onDiscardAndLeave, onStay }: UnsavedChangesDialogProps) {
  // 本身挂在 Sidebar 里，本来就在 .animate-page-in 外面，fixed 定位原本就是对的；
  // 这里同样走 ModalPortal 加固：以后布局怎么变，弹窗都相对视口居中。
  // 面板补 max-h + 内部滚动，窗口很矮时按钮也不会被顶出视口。
  return (
    <ModalPortal>
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/35 p-4"
      role="dialog"
      aria-modal="true"
      aria-label="设置还没保存"
    >
      <div className="max-h-[calc(100dvh-2rem)] w-full max-w-sm overflow-y-auto rounded-2xl border border-card-border bg-card p-5 shadow-xl">
        <p className="text-sm font-black text-foreground">当前设置有未保存的修改，是否保存？</p>
        <p className="mt-2 text-xs leading-5 text-muted">
          走了之后，没保存的修改就丢了；先保存再走，或者直接走、不保存这次的改动。
        </p>
        {error && (
          <p className="mt-3 rounded-lg bg-danger-soft px-3 py-2 text-xs text-danger">{error}</p>
        )}
        <div className="mt-4 flex flex-col gap-2">
          <Button onClick={onSaveAndLeave} disabled={saving}>
            {saving ? '保存中…' : '保存并离开'}
          </Button>
          <Button variant="secondary" onClick={onDiscardAndLeave} disabled={saving}>
            不保存并离开
          </Button>
          <Button variant="ghost" onClick={onStay} disabled={saving}>
            留在本页
          </Button>
        </div>
      </div>
    </div>
    </ModalPortal>
  )
}
