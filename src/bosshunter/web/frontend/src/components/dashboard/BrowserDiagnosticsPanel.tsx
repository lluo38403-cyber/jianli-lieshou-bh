import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { ChevronDown, RefreshCw } from 'lucide-react'
import { cn } from '@/lib/utils'

export interface BrowserDiagnostics {
  ok: boolean
  diagnostics?: Record<string, unknown>
  discovered?: { chrome?: string | null; edge?: string | null }
  error_kind?: 'not_found' | 'connection_failed' | null
  message?: string
  next_steps?: string[]
}

export function BrowserDiagnosticsPanel({ diagnostics, onRefresh }: {
  diagnostics: BrowserDiagnostics | null
  onRefresh: () => void
}) {
  const [expanded, setExpanded] = useState(false)
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
    ? '电脑上没找到 Chrome 或 Edge，简历猎手要用你的 Chrome 打开招聘网站。'
    : '浏览器在运行，但简历猎手连不上它，可能是远程调试没开，或者浏览器刚被关掉。'

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
      setSelectMessage('先填一下浏览器在哪，比如 Chrome 或 Edge 的完整路径。')
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
      await fetch('/api/browser/diagnostics', { method: 'POST' })
    } catch {
      // 重新检测失败也刷新一次，让界面显示最新结果。
    } finally {
      setChecking(false)
      onRefresh()
    }
  }

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(JSON.stringify(diagnostics, null, 2))
      setCopied(true)
      window.setTimeout(() => setCopied(false), 2000)
    } catch {
      setCopied(false)
      setSelectMessage('复制失败了，点开“看看诊断详情”手动复制。')
    }
  }

  const discovered = diagnostics.discovered || {}
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
          {checking ? '检测中' : '重新检测'}
        </Button>
        <Button variant="ghost" size="sm" className="h-7 shrink-0 px-1" onClick={handleCopy}>
          {copied ? '已复制' : '一键复制诊断日志'}
        </Button>
        <button
          type="button"
          aria-expanded={expanded}
          onClick={() => setExpanded(value => !value)}
          className="flex h-7 shrink-0 items-center gap-1 rounded px-1 text-muted hover:text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
        >
          {expanded ? '收起来' : '看看诊断详情'}<ChevronDown className={cn('h-3 w-3', expanded && 'rotate-180')} />
        </button>
      </div>
      {diagnostics.next_steps?.length ? (
        <ul className="list-disc space-y-1 border-t border-current/10 py-2 pl-5 leading-5 text-foreground">
          {diagnostics.next_steps.map((step, index) => <li key={index}>{step}</li>)}
        </ul>
      ) : (
        <p className="border-t border-current/10 py-2 leading-5 text-foreground">
          {isNotFound
            ? '下一步：先确认电脑上装了 Chrome 或 Edge；装好了还报这个，就点下面“手动选一下浏览器”。'
            : '下一步：把 Chrome 完全关掉再重新打开，确认它能正常上网，再点“重新检测”。'}
        </p>
      )}
      <div hidden={!expanded} className="space-y-2 border-t border-current/10 py-2">
        {(discovered.chrome || discovered.edge) && (
          <p className="leading-5 text-muted">
            找到的浏览器：{discovered.chrome ? `Chrome：${discovered.chrome}` : '没找到 Chrome'}
            {discovered.edge ? `；Edge：${discovered.edge}` : '；没找到 Edge'}
          </p>
        )}
        <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-card p-2 text-[11px] leading-4 text-muted">
          {JSON.stringify(diagnostics.diagnostics ?? diagnostics, null, 2)}
        </pre>
        <div className="space-y-2">
          <label className="block text-xs font-bold text-foreground">
            手动选一下浏览器（chrome.exe / msedge.exe）
            <span className="mt-1 flex gap-2">
              <Input value={pathInput} onChange={event => setPathInput(event.target.value)} placeholder="比如：C:\Program Files\Google\Chrome\Application\chrome.exe" />
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
          {selectMessage && <p className="text-xs leading-5 text-muted">{selectMessage}</p>}
        </div>
      </div>
    </div>
  )
}
