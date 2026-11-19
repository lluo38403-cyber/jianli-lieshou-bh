import { useState, useEffect, useCallback, useRef } from 'react'
import { fetchWithRetry, isServiceNotReadyError } from '@/lib/api'

/** 本地服务没起来时，隔多久自动再拉一次配置。 */
const CONFIG_RETRY_MS = 10_000

function readConfigPath(config: Record<string, any>, path: string): unknown {
  const keys = path.split('.')
  let existing: any = config
  for (const key of keys) {
    if (existing == null || typeof existing !== 'object') {
      return undefined
    }
    existing = existing[key]
  }
  return existing
}

function writeConfigPath(config: Record<string, any>, path: string, value: unknown): Record<string, any> {
  const keys = path.split('.')
  const next = JSON.parse(JSON.stringify(config))
  let obj = next
  for (let i = 0; i < keys.length - 1; i++) {
    if (!obj[keys[i]]) obj[keys[i]] = {}
    obj = obj[keys[i]]
  }
  obj[keys[keys.length - 1]] = value
  return next
}

export function useConfig() {
  const [config, setConfig] = useState<Record<string, any> | null>(null)
  const [schema, setSchema] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null)
  /** 本地服务没起来、正在等它自动再试（耗尽后页面上的“重试”也能手动再试）。 */
  const [waitingForService, setWaitingForService] = useState(false)
  const configRef = useRef<Record<string, any> | null>(null)
  const retryTimerRef = useRef<number | null>(null)
  const mountedRef = useRef(true)

  const clearRetryTimer = useCallback(() => {
    if (retryTimerRef.current !== null) {
      window.clearTimeout(retryTimerRef.current)
      retryTimerRef.current = null
    }
  }, [])

  const fetchConfig = useCallback(async () => {
    clearRetryTimer()
    try {
      setError(null)
      // 读请求走统一重试：本地服务没起来时先在请求层退避再试几次。
      const [configRes, schemaRes] = await Promise.all([
        fetchWithRetry('/api/config', {}, { timeoutMs: 8000, retries: 3 }),
        fetchWithRetry('/api/config/schema', {}, { timeoutMs: 8000, retries: 3 }),
      ])
      if (!configRes.ok) throw new Error('配置接口请求失败')
      if (!schemaRes.ok) throw new Error('配置结构接口请求失败')
      const configData = await configRes.json()
      const schemaData = await schemaRes.json()
      configRef.current = configData
      setConfig(configData)
      setSchema(schemaData)
      setDirty(false)
      setWaitingForService(false)
    } catch (err) {
      console.error('Failed to fetch config:', err)
      configRef.current = null
      setConfig(null)
      // 服务没起来：先报等服务的提示，10 秒后自动再拉一次，起来了
      // 页面自己就好了；不是服务问题才报原来的失败文案。
      const notReady = isServiceNotReadyError(err)
      if (mountedRef.current) {
        setWaitingForService(notReady)
        setError(
          notReady
            ? '本地服务还没起来，正在等它启动，好了会自动打开，点“重试”也能马上再试。'
            : err instanceof Error ? err.message : '配置加载失败',
        )
        if (notReady) {
          retryTimerRef.current = window.setTimeout(() => {
            void fetchConfig()
          }, CONFIG_RETRY_MS)
        }
      }
    } finally {
      setLoading(false)
    }
  }, [clearRetryTimer])

  useEffect(() => {
    mountedRef.current = true
    void fetchConfig()
    return () => {
      mountedRef.current = false
      clearRetryTimer()
    }
  }, [fetchConfig, clearRetryTimer])

  const updateConfig = useCallback((path: string, value: any, options?: { markDirty?: boolean }) => {
    const prev = configRef.current
    if (!prev) return
    if (Object.is(readConfigPath(prev, path), value)) return
    const next = writeConfigPath(prev, path, value)
    configRef.current = next
    setConfig(next)
    // Server-persisted fields (e.g. resume upload) sync local state without dirtying.
    if (options?.markDirty !== false) {
      setDirty(true)
    }
  }, [])

  const saveConfig = async () => {
    const snapshot = configRef.current
    if (!snapshot) return
    setSaving(true)
    setMessage(null)
    try {
      const res = await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(snapshot),
      })
      const data = await res.json()
      if (data.success) {
        await fetchConfig()
        window.dispatchEvent(new Event('jianli-lieshou-config-saved'))
        setMessage({ type: 'success', text: '设置已经保存了' })
        setDirty(false)
      } else {
        setMessage({ type: 'error', text: data.error || '保存失败' })
      }
    } catch (err) {
      setMessage({ type: 'error', text: '网络错误' })
    } finally {
      setSaving(false)
      setTimeout(() => setMessage(null), 3000)
    }
  }

  const resetConfig = () => {
    setLoading(true)
    void fetchConfig()
  }

  return { config, schema, loading, saving, dirty, error, waitingForService, message, updateConfig, saveConfig, resetConfig }
}
