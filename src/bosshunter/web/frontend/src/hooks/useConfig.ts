import { useState, useEffect, useCallback, useRef } from 'react'
import { fetchWithRetry, isServiceNotReadyError, readJsonWithTimeout } from '@/lib/api'
import { validateConfigNumbers } from '@/lib/configValidation'

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

/**
 * 脏判定：当前值与“已保存快照”深比较。改了又改回原值不算脏；
 * 比较的是整份配置对象——快照与当前值都来自同一份服务端数据，
 * 服务端回填的字段两边都有，不会误判成脏。
 */
export function isConfigEqual(a: unknown, b: unknown): boolean {
  if (Object.is(a, b)) return true
  if (typeof a !== 'object' || typeof b !== 'object' || a === null || b === null) return false
  if (Array.isArray(a) !== Array.isArray(b)) return false
  if (Array.isArray(a) && Array.isArray(b)) {
    if (a.length !== b.length) return false
    return a.every((item, index) => isConfigEqual(item, b[index]))
  }
  const aKeys = Object.keys(a as Record<string, unknown>)
  const bKeys = Object.keys(b as Record<string, unknown>)
  if (aKeys.length !== bKeys.length) return false
  return aKeys.every(key => isConfigEqual(
    (a as Record<string, unknown>)[key],
    (b as Record<string, unknown>)[key],
  ))
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
  /** 已保存快照：最近一次从服务端拉到的配置，脏判定拿当前值跟它比。 */
  const savedSnapshotRef = useRef<Record<string, any> | null>(null)
  const retryTimerRef = useRef<number | null>(null)
  const mountedRef = useRef(true)
  const firstLoadRef = useRef(true)

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
      // 读体也加超时：fetch 只管到响应头，body 卡住时 json() 会一直等。
      const configData = await readJsonWithTimeout(configRes)
      const schemaData = await readJsonWithTimeout(schemaRes)
      configRef.current = configData
      savedSnapshotRef.current = JSON.parse(JSON.stringify(configData))
      setConfig(configData)
      setSchema(schemaData)
      setDirty(false)
      setWaitingForService(false)
    } catch (err) {
      console.error('Failed to fetch config:', err)
      configRef.current = null
      savedSnapshotRef.current = null
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
      firstLoadRef.current = false
    }
  }, [clearRetryTimer])

  useEffect(() => {
    mountedRef.current = true
    void fetchConfig()
    // 骨架屏兜底：首屏配置 20 秒还没回来，不再一直晾着骨架，
    // 改成可重试的提示；配置到了会自动补上。
    const watchdog = window.setTimeout(() => {
      if (firstLoadRef.current && mountedRef.current) {
        setLoading(false)
        setError('配置一直没回来，点“重试”再试。')
      }
    }, 20000)
    return () => {
      mountedRef.current = false
      window.clearTimeout(watchdog)
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
    // Server-persisted fields (e.g. resume upload) sync local state without dirtying:
    // the server already has this value, so the saved snapshot moves with it.
    if (options?.markDirty === false) {
      savedSnapshotRef.current = writeConfigPath(savedSnapshotRef.current ?? prev, path, value)
      setDirty(!isConfigEqual(next, savedSnapshotRef.current))
      return
    }
    setDirty(!isConfigEqual(next, savedSnapshotRef.current))
  }, [])

  /** 保存当前配置；返回保存是否成功，供“保存并离开”决定跳不跳。 */
  const saveConfig = async (): Promise<boolean> => {
    const snapshot = configRef.current
    if (!snapshot) return false
    // H-6：保存前集中校验全部数字区间与 min<=max——数字框清空变 0、
    // 输 e/- 变 NaN 的坏值不能等到启动任务时才暴露；有错不发请求。
    const numberErrors = validateConfigNumbers(snapshot)
    if (numberErrors.length) {
      const first = numberErrors[0]
      setMessage({
        type: 'error',
        text: numberErrors.length > 1
          ? `${first.message}（另有 ${numberErrors.length - 1} 项数字也有问题）`
          : first.message,
      })
      setTimeout(() => setMessage(null), 3000)
      return false
    }
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
        setMessage({
          type: 'success',
          text: data.reclassified
            ? `设置已经保存了；分数线改成 ${data.threshold} 分，${data.reclassified} 个岗位的状态跟着变了。`
            : '设置已经保存了',
        })
        setDirty(false)
        return true
      } else {
        setMessage({ type: 'error', text: data.error || '保存失败' })
        return false
      }
    } catch (err) {
      setMessage({ type: 'error', text: '网络错误' })
      return false
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
