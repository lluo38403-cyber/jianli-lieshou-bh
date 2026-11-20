import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { RefreshCw } from 'lucide-react'
import { cn } from '@/lib/utils'

export interface BrowserDiagnostics {
  ok: boolean
  diagnostics?: Record<string, unknown>
  discovered?: { chrome?: string | null; edge?: string | null }
  browser_choice?: string
  selected_browser_name?: string
  error_kind?: 'not_found' | 'connection_failed' | null
  message?: string
  next_steps?: string[]
}

export function BrowserDiagnosticsPanel({ diagnostics, onRefresh }: {
  diagnostics: BrowserDiagnostics | null
  onRefresh: () => void
}) {
  const [pathInput, setPathInput] = useState('')
  const [selecting, setSelecting] = useState(false)
  const [selectMessage, setSelectMessage] = useState('')
  const [copied, setCopied] = useState(false)
  const [checking, setChecking] = useState(false)

  if (!diagnostics) return null
  // 没问题就不占地方，只在找不到浏览器或连不上时出现。
  if (diagnostics.ok && !diagnostics.error_kind) return null

  const isNotFound = diagnostics.error_kind === 'not_found'
  const title = isNotFound ? '没找到能用的浏览器' : '浏览器连不上了'
  const fallbackMessage = isNotFound
    ? '电脑上没找到能用的浏览器，简历猎手要用你的浏览器打开招聘网站。'
    : '浏览器已经装好了，但是现在连不上，点“一键启动浏览器”就行。'
  // 连不上时主按钮就是一键启动：后端会拉起所选浏览器（带调试端口），
  // 不用用户自己排错；没找到浏览器时只能重新检测（检测不拉起浏览器）。
  const primaryLabel = checking ? '正在启动…' : isNotFound ? '重新检测' : '一键启动浏览器'

  const handleFilePick = (file: File | undefined) => {
    if (!file) return
    // 桌面端里文件对象自带完整路径；普通网页拿不到路径时改用下面手动填。
    const maybePath = (file as File & { path?: string }).path
    if (maybePath) {
      setPathInput(maybePath)
      setSelectMessage('')
    } else {
      setSelectMessage('这个文件没读到路径，请把浏览器的完整路径粘贴到下面的框里。')
    }
  }

  const handleSelect = async () => {
    const path = pathInput.trim()
    if (!path) {
      setSelectMessage('先填一下浏览器程序的完整路径。')
      return
    }
    setSelecting(true)
    setSelectMessage('')
    try {
      const res = await fetch('/api/browser/select', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(data.error || data.message || '没保存上')
      setSelectMessage('已经记住了，下次就用这个浏览器。点“重新检测”确认一下。')
      onRefresh()
    } catch (err) {
      setSelectMessage(err instanceof Error ? err.message : '没保存上，再试一次。')
    } finally {
      setSelecting(false)
    }
  }

  const handleRecheck = async () => {
    setChecking(true)
    try {
      // 没找到浏览器：只重新检测，不拉起；连不上：一键启动才拉起。
      const endpoint = isNotFound ? '/api/browser/diagnostics' : '/api/browser/launch'
      await fetch(endpoint, { method: 'POST' })
    } catch {
      // 重新检测失败也刷新一次，让界面显示最新结果。
    } finally {
      setChecking(false)
      onRefresh()
    }
  }

  const handleCopy = async () => {
    try {
      // 完整诊断只在后台复制，不在页面上平铺代码。
      await navigator.clipboard.writeText(JSON.stringify(diagnostics, null, 2))
      setCopied(true)
      window.setTimeout(() => setCopied(false), 2000)
    } catch {
      setCopied(false)
      setSelectMessage('没复制上，再点一次试试。')
    }
  }

  return (
    <div role="region" aria-label="浏览器诊断" className={cn(
      'mt-2 rounded-lg border px-2 text-xs',
      isNotFound ? 'border-warning-border bg-warning-soft' : 'border-danger-border bg-danger-soft',
    )}>
      <div className="flex min-w-0 flex-wrap items-center gap-2 py-2">
        <span className="font-black text-foreground">{title}</span>
        <span className="min-w-0 flex-1 text-muted">{diagnostics.message || fallbackMessage}</span>
        <Button variant="ghost" size="sm" className="h-7 shrink-0 px-1" onClick={handleRecheck} disabled={checking}>
          <RefreshCw className={cn('mr-1 h-3 w-3', checking && 'animate-spin')} />
          {primaryLabel}
        </Button>
        <Button variant="ghost" size="sm" className="h-7 shrink-0 px-1" onClick={handleCopy}>
          {copied ? '已复制' : '一键复制诊断信息'}
        </Button>
      </div>
      {diagnostics.next_steps?.length ? (
        <ul className="list-disc space-y-1 border-t border-current/10 py-2 pl-5 leading-5 text-foreground">
          {diagnostics.next_steps.map((step, index) => <li key={index}>{step}</li>)}
        </ul>
      ) : null}
      {selectMessage && <p className="border-t border-current/10 py-2 text-xs leading-5 text-muted">{selectMessage}</p>}
      {isNotFound && (
        <div className="space-y-2 border-t border-current/10 py-2">
          <label className="block text-xs font-bold text-foreground">
            手动选一下浏览器（chrome.exe / msedge.exe）
            <span className="mt-1 flex gap-2">
              <Input value={pathInput} onChange={event => setPathInput(event.target.value)} placeholder="填入浏览器程序的完整路径" />
              <label className="flex h-9 shrink-0 cursor-pointer items-center rounded-md border border-card-border bg-card px-3 text-xs font-bold text-primary hover:border-primary/40">
                选文件
                <input
                  type="file"
                  accept=".exe"
                  className="hidden"
                  aria-label="选择浏览器文件"
                  onChange={event => handleFilePick(event.target.files?.[0])}
                />
              </label>
              <Button variant="secondary" size="sm" className="h-9 shrink-0" onClick={handleSelect} disabled={selecting}>
                {selecting ? '保存中…' : '就用这个'}
              </Button>
            </span>
          </label>
        </div>
      )}
    </div>
  )
}
