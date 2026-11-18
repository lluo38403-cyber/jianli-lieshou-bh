import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import type { Job } from '@/hooks/useDashboard'
import { jobForInterview, jobMarkdown, startInterviewHandoff, type HandoffState } from '@/lib/interviewHandoff'

const ADDRESS_KEY = 'jianli-lieshou.interview-workbench-url'
const messages: Record<HandoffState, string> = {
  waiting: '正在等本地的面试工具接过去（最多 15 秒）…',
  received: '岗位已经送过去了；去新打开的页面补上简历、核对好保存，再选面试怎么考。还没开始面试呢。',
  blocked: '浏览器拦住了新页面。请允许弹窗再试，或者下载岗位介绍手动导进去。',
  timeout: '没收到接过去的回音：请确认面试工具已经打开、能接岗位。如果新页面已经有草稿了，先核对一下，别重复弄；也可以下载岗位介绍手动导进去。',
}

export function InterviewPreparation({ job }: { job: Job }) {
  const [expanded, setExpanded] = useState(false)
  const [address, setAddress] = useState(() => {
    try { return localStorage.getItem(ADDRESS_KEY) || 'http://127.0.0.1:8800' } catch { return 'http://127.0.0.1:8800' }
  })
  const [confirmed, setConfirmed] = useState(false)
  const [state, setState] = useState<HandoffState | null>(null)
  const [notice, setNotice] = useState('')
  const cancel = useRef<(() => void) | null>(null)
  const waiting = state === 'waiting'
  let preview = '', dataError = ''
  try { preview = jobMarkdown(job) } catch { dataError = '岗位信息格式不对，请重新打开详情再试。' }
  useEffect(() => () => cancel.current?.(), [])

  const send = () => {
    if (!confirmed || waiting) return
    cancel.current?.()
    setNotice('')
    try {
      const payload = jobForInterview(job)
      cancel.current = startInterviewHandoff(window, address, payload, next => {
        setState(next)
        setNotice(messages[next])
      })
      try { localStorage.setItem(ADDRESS_KEY, address.trim()) } catch { /* Address persistence is optional. */ }
    } catch (error) {
      setState(null)
      setNotice(error instanceof Error ? error.message : '没送过去，请下载岗位介绍手动导进去。')
    }
  }

  const download = () => {
    try {
      const blob = new Blob([jobMarkdown(job)], { type: 'text/markdown;charset=utf-8' })
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = 'interview-job.md'
      link.click()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
      setNotice('JD 已经下载好了。可以交给别的 AI 工具，或者在面试工具的岗位导入里选这个文件。')
    } catch (error) {
      setNotice(error instanceof Error ? error.message : '没下载下来。')
    }
  }

  return (
    <section className="mt-4 border-t border-card-border pt-4" aria-label="面试准备">
      <Button type="button" variant="secondary" size="sm" aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>练练面试</Button>
      {expanded && (
        <div className="mt-3 space-y-3 text-sm">
          <p className="text-muted">拿这个岗位练模拟面试，不会投出去，也不会改岗位状态。不装面试工具也能下载岗位介绍，交给别的 AI 工具用。</p>
          <p className="text-muted">只会把公司、岗位、城市、工资、学历和岗位介绍原文送过去；不会带你的简历、登录信息、HR 字段、打招呼的话和聊天记录。岗位介绍里可能有联系方式，你先核对一下下面的内容。</p>
          <details className="rounded-lg border border-card-border p-3">
            <summary className="cursor-pointer font-bold">看看要送过去的岗位内容</summary>
            <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-words font-sans text-xs leading-5">{dataError || preview}</pre>
          </details>
          <label className="block font-bold">本地面试工具的地址
            <input className="mt-1 block w-full rounded-md border border-card-border bg-white px-3 py-2 font-normal focus-visible:outline-primary" value={address} disabled={waiting} onChange={event => { setAddress(event.target.value); setConfirmed(false) }} spellCheck={false} />
          </label>
          <p className="text-xs text-muted">先让 AI 工具打开面试模拟，再填它给你的地址。只能是同一台电脑上的 localhost / 127.0.0.1。第一次用、版本怎么配，看 面试工具的说明。</p>
          <label className="flex items-start gap-2"><input type="checkbox" className="mt-1 accent-primary" checked={confirmed} disabled={waiting} onChange={event => setConfirmed(event.target.checked)} /><span>我核对过岗位内容了，相信这个本地工具，同意把上面的招聘资料送过去。</span></label>
          <div className="flex flex-wrap gap-2">
            <Button type="button" size="sm" disabled={!confirmed || waiting || !!dataError} onClick={send}>{waiting ? '等它接过去…' : '打开并送过去'}</Button>
            <Button type="button" variant="secondary" size="sm" disabled={!!dataError} onClick={download}>下载岗位介绍</Button>
            {waiting && <Button type="button" variant="ghost" size="sm" onClick={() => { cancel.current?.(); setState(null); setNotice('不等了；已经打开的页面留着，你自己核对一下。') }}>不等了</Button>}
          </div>
        </div>
      )}
      {notice && <p className="mt-3 text-sm text-primary" role="status" aria-live="polite">{notice}</p>}
    </section>
  )
}
