import { useCallback, useEffect, useRef, useState } from 'react'

export interface UpdateInfo {
  version: string
  size: number
  sha256: string
  notes: string
  current_version: string
}

export type UpdatePhase = 'idle' | 'checking' | 'downloading' | 'verifying' | 'ready' | 'applying' | 'error'

export function formatUpdateSize(bytes: number): string {
  if (!bytes || bytes <= 0) return ''
  const mb = bytes / (1024 * 1024)
  return mb >= 10 ? `${Math.round(mb)} MB` : `${mb.toFixed(1)} MB`
}

interface CheckPayload {
  update_available?: boolean
  version?: string
  size?: number
  sha256?: string
  notes?: string
  current_version?: string
  status?: string
  progress?: number
  error?: string
}

export function useUpdate() {
  const [info, setInfo] = useState<UpdateInfo | null>(null)
  const [currentVersion, setCurrentVersion] = useState('')
  const [phase, setPhase] = useState<UpdatePhase>('idle')
  const [progress, setProgress] = useState(0)
  const [error, setError] = useState('')
  const [dismissed, setDismissed] = useState(false)
  const [checking, setChecking] = useState(false)
  const pollTimer = useRef<number | null>(null)
  const healthTimer = useRef<number | null>(null)
  const mountedRef = useRef(true)
  const phaseRef = useRef<UpdatePhase>('idle')

  const setPhaseBoth = useCallback((next: UpdatePhase) => {
    phaseRef.current = next
    if (mountedRef.current) setPhase(next)
  }, [])

  const stopPolling = useCallback(() => {
    if (pollTimer.current !== null) {
      window.clearInterval(pollTimer.current)
      pollTimer.current = null
    }
  }, [])

  const stopHealthPolling = useCallback(() => {
    if (healthTimer.current !== null) {
      window.clearInterval(healthTimer.current)
      healthTimer.current = null
    }
  }, [])

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      stopPolling()
      stopHealthPolling()
    }
  }, [stopPolling, stopHealthPolling])

  const applyPayload = useCallback((data: CheckPayload) => {
    if (!mountedRef.current) return
    if (typeof data?.current_version === 'string' && data.current_version) {
      setCurrentVersion(data.current_version)
    }
    if (data?.update_available && typeof data?.version === 'string' && data.version) {
      setInfo({
        version: data.version,
        size: Number(data.size) || 0,
        sha256: String(data.sha256 || ''),
        notes: String(data.notes || ''),
        current_version: String(data.current_version || ''),
      })
      setError('')
    } else if (phaseRef.current === 'idle') {
      setInfo(null)
    }
  }, [])

  /** 手动检查：true=有新版，false=已经是最新，null=没查到（网络问题）。 */
  const check = useCallback(async (force = false): Promise<boolean | null> => {
    setChecking(true)
    try {
      const res = await fetch(force ? '/api/update/check?force=1' : '/api/update/check')
      const data = (await res.json().catch(() => ({}))) as CheckPayload
      applyPayload(data)
      return Boolean(data?.update_available)
    } catch {
      // 查不到就当没有新版，不打扰正常用。
      return null
    } finally {
      if (mountedRef.current) setChecking(false)
    }
  }, [applyPayload])

  const fetchHealth = useCallback(async () => {
    try {
      const res = await fetch('/api/health')
      const data = (await res.json().catch(() => ({}))) as { version?: string }
      if (mountedRef.current && typeof data?.version === 'string' && data.version) {
        setCurrentVersion(data.version)
      }
    } catch {
      // 健康接口本地一定通，失败也不影响。
    }
  }, [])

  useEffect(() => {
    void check()
    void fetchHealth()
  }, [check, fetchHealth])

  const start = useCallback(async () => {
    if (!info) return
    setError('')
    setPhaseBoth('downloading')
    setProgress(0)
    try {
      const res = await fetch('/api/update/start', { method: 'POST' })
      const data = (await res.json().catch(() => ({}))) as { error?: string }
      if (!res.ok) throw new Error(String(data?.error || '没开始下载，稍后再试。'))
    } catch (err) {
      setPhaseBoth('error')
      if (mountedRef.current) setError(err instanceof Error ? err.message : '没开始下载，稍后再试。')
      return
    }
    stopPolling()
    pollTimer.current = window.setInterval(async () => {
      try {
        const res = await fetch('/api/update/status')
        const data = (await res.json().catch(() => ({}))) as CheckPayload
        const status = String(data?.status || '')
        if (status === 'downloading' || status === 'verifying') {
          setPhaseBoth(status)
          if (mountedRef.current) setProgress(Number(data?.progress) || 0)
        } else if (status === 'ready') {
          setPhaseBoth('ready')
          if (mountedRef.current) setProgress(100)
          stopPolling()
        } else if (status === 'error') {
          setPhaseBoth('error')
          if (mountedRef.current) setError(String(data?.error || '下载失败了，稍后再试。'))
          stopPolling()
        }
      } catch {
        // 下次轮询再试。
      }
    }, 1000)
  }, [info, setPhaseBoth, stopPolling])

  const apply = useCallback(async () => {
    setError('')
    setPhaseBoth('applying')
    try {
      const res = await fetch('/api/update/apply', { method: 'POST' })
      const data = (await res.json().catch(() => ({}))) as { error?: string }
      if (!res.ok) throw new Error(String(data?.error || '这次没换上，稍后再试。'))
    } catch (err) {
      setPhaseBoth('error')
      if (mountedRef.current) setError(err instanceof Error ? err.message : '这次没换上，稍后再试。')
      return
    }
    // 面板要重启了：等它重新打开再刷新页面。
    let tries = 0
    stopHealthPolling()
    healthTimer.current = window.setInterval(async () => {
      tries += 1
      try {
        const res = await fetch('/api/health')
        if (res.ok) {
          stopHealthPolling()
          window.location.reload()
          return
        }
      } catch {
        // 还没起来，继续等。
      }
      if (tries >= 45) {
        stopHealthPolling()
        setPhaseBoth('error')
        if (mountedRef.current) setError('新版本应该换好了，但面板没重新打开，关掉再双击打开一次就行。')
      }
    }, 2000)
  }, [setPhaseBoth, stopHealthPolling])

  const dismiss = useCallback(() => {
    setDismissed(true)
  }, [])

  const retry = useCallback(async () => {
    setError('')
    if (phaseRef.current === 'error') {
      setPhaseBoth('idle')
    }
    await check(true)
  }, [check, setPhaseBoth])

  return {
    info,
    currentVersion,
    phase,
    progress,
    error,
    dismissed,
    checking,
    check,
    start,
    apply,
    dismiss,
    retry,
  }
}

export type UpdateState = ReturnType<typeof useUpdate>
