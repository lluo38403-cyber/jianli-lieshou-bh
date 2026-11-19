import { useCallback, useEffect, useRef, useState } from 'react'

export type AuthStatus = 'trial' | 'active' | 'expired' | 'unknown'

export interface AuthDisplay {
  buyUrl: string
  wechatId: string
  qrPath: string
}

export interface AuthSnapshot {
  machineIdShort: string
  status: AuthStatus
  remainingSeconds: number
  offline: boolean
  error: string
}

export interface AuthHook {
  snapshot: AuthSnapshot | null
  display: AuthDisplay
  /** 展示用的剩余秒数：后端 30 秒同步一次，中间本地按单调时钟往下走。 */
  remainingSeconds: number
  loading: boolean
  refresh: () => Promise<void>
  activate: (cardCode: string) => Promise<{ ok: boolean; error?: string }>
}

function pad2(n: number): string {
  return String(n).padStart(2, '0')
}

/** 倒计时文案：试用中（剩余 01:45:20）/ VIP有效（剩余 6天 14小时）/ 已过期，请激活。 */
export function formatRemaining(seconds: number, status: AuthStatus): string {
  if (status === 'unknown') return '正在看授权状态…'
  if (status === 'expired' || seconds <= 0) return '已过期，请激活'
  const label = status === 'trial' ? '试用中' : 'VIP有效'
  if (seconds < 86400) {
    const h = Math.floor(seconds / 3600)
    const m = Math.floor((seconds % 3600) / 60)
    const s = Math.floor(seconds % 60)
    return `${label}（剩余 ${pad2(h)}:${pad2(m)}:${pad2(s)}）`
  }
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  return `${label}（剩余 ${days}天 ${hours}小时）`
}

/** 复制文本：优先用剪贴板接口，不行就用老办法兜底。 */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    // 掉到下面的兜底办法。
  }
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    return ok
  } catch {
    return false
  }
}

interface StatusPayload {
  machine_id_short?: string
  status?: string
  remaining_seconds?: number
  offline?: boolean
  error?: string
}

interface DisplayPayload {
  buy_url?: string
  wechat_id?: string
  wechat_qr_path?: string
}

const STATUS_POLL_MS = 30_000

export function useAuth(): AuthHook {
  const [snapshot, setSnapshot] = useState<AuthSnapshot | null>(null)
  const [display, setDisplay] = useState<AuthDisplay>({ buyUrl: '', wechatId: '', qrPath: '' })
  const [remainingSeconds, setRemainingSeconds] = useState(0)
  const [loading, setLoading] = useState(true)
  const anchorRef = useRef({ remaining: 0, at: 0 })
  const mountedRef = useRef(true)

  const applySnapshot = useCallback((data: StatusPayload) => {
    if (!mountedRef.current) return
    const status = (['trial', 'active', 'expired'].includes(String(data?.status))
      ? String(data.status)
      : 'unknown') as AuthStatus
    const remaining = Math.max(0, Number(data?.remaining_seconds) || 0)
    setSnapshot({
      machineIdShort: String(data?.machine_id_short || ''),
      status,
      remainingSeconds: remaining,
      offline: Boolean(data?.offline),
      error: String(data?.error || ''),
    })
    anchorRef.current = { remaining, at: performance.now() }
    setRemainingSeconds(remaining)
  }, [])

  const fetchStatus = useCallback(async () => {
    try {
      const res = await fetch('/api/auth/status')
      const data = (await res.json().catch(() => ({}))) as StatusPayload
      applySnapshot(data)
    } catch {
      // 查不到就留着上次的状态，不打扰正常用。
    } finally {
      if (mountedRef.current) setLoading(false)
    }
  }, [applySnapshot])

  const fetchDisplay = useCallback(async () => {
    try {
      const res = await fetch('/api/auth/config')
      const data = (await res.json().catch(() => ({}))) as DisplayPayload
      if (!mountedRef.current) return
      setDisplay({
        buyUrl: String(data?.buy_url || ''),
        wechatId: String(data?.wechat_id || ''),
        qrPath: String(data?.wechat_qr_path || ''),
      })
    } catch {
      // 展示配置拿不到就先空着，不挡主界面。
    }
  }, [])

  useEffect(() => {
    mountedRef.current = true
    void fetchStatus()
    void fetchDisplay()
    const poll = window.setInterval(() => void fetchStatus(), STATUS_POLL_MS)
    // 本地每秒往下走一格做展示；真正剩多少以后端 30 秒一次的同步为准。
    const tick = window.setInterval(() => {
      const anchor = anchorRef.current
      if (anchor.at === 0) return
      const left = Math.max(0, anchor.remaining - Math.floor((performance.now() - anchor.at) / 1000))
      setRemainingSeconds((prev) => (prev === left ? prev : left))
    }, 1000)
    return () => {
      mountedRef.current = false
      window.clearInterval(poll)
      window.clearInterval(tick)
    }
  }, [fetchStatus, fetchDisplay])

  const refresh = useCallback(async () => {
    try {
      const res = await fetch('/api/auth/refresh', { method: 'POST' })
      const data = (await res.json().catch(() => ({}))) as StatusPayload
      applySnapshot(data)
    } catch {
      await fetchStatus()
    }
  }, [applySnapshot, fetchStatus])

  const activate = useCallback(
    async (cardCode: string): Promise<{ ok: boolean; error?: string }> => {
      const code = cardCode.trim()
      if (!code) return { ok: false, error: '先把卡密填上。' }
      try {
        const res = await fetch('/api/auth/activate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ card_code: code }),
        })
        const data = (await res.json().catch(() => ({}))) as { error?: string }
        if (!res.ok) return { ok: false, error: String(data?.error || '这张卡用不了。') }
        await fetchStatus()
        return { ok: true }
      } catch {
        return { ok: false, error: '现在连不上，检查网络后重试。' }
      }
    },
    [fetchStatus],
  )

  return { snapshot, display, remainingSeconds, loading, refresh, activate }
}
