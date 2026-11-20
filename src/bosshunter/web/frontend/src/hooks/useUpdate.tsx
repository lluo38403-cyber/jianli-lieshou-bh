import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { fetchWithRetry } from '@/lib/api'

export interface UpdateInfo {
  version: string
  size: number
  sha256: string
  notes: string
  current_version: string
}

export type UpdatePhase = 'idle' | 'checking' | 'downloading' | 'verifying' | 'ready' | 'applying' | 'error'

/** 更新失败分三步：哪步失败就说哪步，原因和下一步都不一样。 */
export type UpdateErrorKind = 'download' | 'verify' | 'apply' | 'other'

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

/**
 * 按后端原话（见后端 updater.py 的 UpdateError 文案）加当前阶段，
 * 把更新失败分到下载 / 校验 / 替换三步。匹配不到就按阶段兜底，都没有
 * 才是 other——这时直接显示后端原话，不瞎猜。
 */
export function classifyUpdateError(message: string, phase?: UpdatePhase): UpdateErrorKind {
  const text = message || ''
  if (/对不上|文件路径不对|校验/.test(text)) return 'verify'
  if (/只在正式安装|还没下好|找不到现在用的程序文件|没重新打开|没换上/.test(text)) return 'apply'
  if (/没下载下来|下载出问题|没有找到新版本|没有配好下载地址|断开|下载失败|没开始下载/.test(text)) return 'download'
  if (phase === 'verifying') return 'verify'
  if (phase === 'applying') return 'apply'
  if (phase === 'downloading' || phase === 'checking') return 'download'
  return 'other'
}

export interface UpdateErrorCopy {
  /** 哪一步失败了。 */
  title: string
  /** 大概原因 + 现状（旧版本能不能用、设置动没动）。 */
  reason: string
  /** 下一步怎么办。 */
  tip: string
}

export function getUpdateErrorCopy(kind: UpdateErrorKind): UpdateErrorCopy {
  switch (kind) {
    case 'download':
      return {
        title: '新版本没下载下来',
        reason: '可能是网络断了，或者服务器上暂时没有这个版本。旧版本还能正常用。',
        tip: '点“再试一次”就行；一直不行就手动下载。',
      }
    case 'verify':
      return {
        title: '下载的文件对不上',
        reason: '怕文件坏了，这次先不换，旧版本还能正常用。',
        tip: '点“再试一次”重新下载；一直这样就手动下载。',
      }
    case 'apply':
      return {
        title: '这次没换上',
        reason: '旧版本还在，能正常用；设置和投递记录都没动。',
        tip: '点“再试一次”，或手动下载后自己换上。',
      }
    default:
      return { title: '', reason: '', tip: '点“再试一次”。' }
  }
}

/** 下载状态轮询连续多少次都连不上本地服务，就当这次下载断了，不一直转圈。 */
const STATUS_POLL_GIVE_UP = 10

// 中继发过来的更新说明有时是内部测试口径（比如“只升版号，验证
// update.bat 修复”），直接显示给用户会看不懂。统一洗成一句面向
// 用户的中文：去掉版本号前缀，命中内部口径就换成默认文案，太长截断。
export const DEFAULT_UPDATE_NOTES = '修了一些小问题，用起来更稳。';

const INTERNAL_UPDATE_NOTE_MARKERS = [
	'只升版号',
	'只升版本号',
	'update.bat',
	'update-flow',
	'验证 v',
	'验证 update',
	'验证更新',
	'CI',
	'SMOKE',
	'回归测试',
	'全流程',
];

export function formatUpdateNotes(notes: string | null | undefined): string {
	const text = (notes ?? '').trim();
	if (!text) return DEFAULT_UPDATE_NOTES;
	// 去掉开头的版本号：先剥 "v19 "（v19 1.1.5 这种两段式），
	// 再剥 "1.1.5 " / "v1.1.5 " / "（v1.1.5）"。第一步只认带 v 的，
	// 免得把“3 个问题已修复”开头的 3 也剥掉。
	const stripped = text
		.replace(/^v\d+\s+(?=\d)/i, '')
		.replace(/^[（(]?v?\d+(?:\.\d+){1,2}\s*[）)]?/, '')
		.replace(/^[：:、，,。\s]+/, '')
		.trim();
	if (!stripped) return DEFAULT_UPDATE_NOTES;
	if (INTERNAL_UPDATE_NOTE_MARKERS.some((marker) => stripped.includes(marker))) {
		return DEFAULT_UPDATE_NOTES;
	}
	return stripped.length > 120 ? `${stripped.slice(0, 117)}…` : stripped;
}

export function useUpdate() {
  const [info, setInfo] = useState<UpdateInfo | null>(null)
  const [currentVersion, setCurrentVersion] = useState('')
  const [phase, setPhase] = useState<UpdatePhase>('idle')
  const [progress, setProgress] = useState(0)
  const [error, setError] = useState('')
  const [errorKind, setErrorKind] = useState<UpdateErrorKind | null>(null)
  const [dismissed, setDismissed] = useState(false)
  const [checking, setChecking] = useState(false)
  /** 手动下载入口：店铺链接（/api/auth/config 的 buy_url）。代码里没有
   * 写死的安装包直链，下载地址只来自中转配置，所以手动下载统一指到店铺；
   * 拿不到就只给文字指引，不写死链接。 */
  const [manualDownloadUrl, setManualDownloadUrl] = useState('')
  const pollTimer = useRef<number | null>(null)
  const healthTimer = useRef<number | null>(null)
  const mountedRef = useRef(true)
  const phaseRef = useRef<UpdatePhase>('idle')

  const setPhaseBoth = useCallback((next: UpdatePhase) => {
    phaseRef.current = next
    if (mountedRef.current) setPhase(next)
  }, [])

  /** 报错定稿：文案 + 失败种类一起记，页面按种类给重试和手动下载。 */
  const setErrorBoth = useCallback((message: string, phaseForKind?: UpdatePhase) => {
    const kind = classifyUpdateError(message, phaseForKind ?? phaseRef.current)
    if (mountedRef.current) {
      setError(message)
      setErrorKind(kind)
    }
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
      setErrorKind(null)
    } else if (phaseRef.current === 'idle') {
      setInfo(null)
    }
  }, [])

  /** 手动检查：true=有新版，false=已经是最新，null=没查到（网络问题）。 */
  const check = useCallback(async (force = false): Promise<boolean | null> => {
    setChecking(true)
    try {
      const res = await fetchWithRetry(force ? '/api/update/check?force=1' : '/api/update/check', {}, { timeoutMs: 8000, retries: 3 })
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
      const res = await fetchWithRetry('/api/health', {}, { timeoutMs: 5000, retries: 2 })
      const data = (await res.json().catch(() => ({}))) as { version?: string }
      if (mountedRef.current && typeof data?.version === 'string' && data.version) {
        setCurrentVersion(data.version)
      }
    } catch {
      // 健康接口本地一定通，失败也不影响。
    }
  }, [])

  /** 手动下载入口只读店铺链接，失败就空着，页面给文字指引。 */
  const fetchManualDownloadUrl = useCallback(async () => {
    try {
      const res = await fetchWithRetry('/api/auth/config', {}, { timeoutMs: 5000, retries: 2 })
      const data = (await res.json().catch(() => ({}))) as { buy_url?: string }
      if (mountedRef.current && typeof data?.buy_url === 'string' && data.buy_url) {
        setManualDownloadUrl(data.buy_url)
      }
    } catch {
      // 拿不到就不显示手动下载按钮，不挡正常用。
    }
  }, [])

  /** 状态轮询：1 秒问一次服务端下载到哪了。下载中切页面也不停——
   * 轮询挂在全局的 UpdateProvider 里，页面卸载清不掉它。 */
  const startPolling = useCallback(() => {
    stopPolling()
    // 轮询本身不重试：后端下载中途挂掉，状态接口会连续失败；
    // 连失败 10 次就定稿为下载失败，不让页面一直卡在“下载中”。
    let consecutiveFailures = 0
    pollTimer.current = window.setInterval(async () => {
      try {
        const res = await fetch('/api/update/status')
        const data = (await res.json().catch(() => ({}))) as CheckPayload
        consecutiveFailures = 0
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
          setErrorBoth(String(data?.error || '下载失败了，稍后再试。'), phaseRef.current === 'verifying' ? 'verifying' : 'downloading')
          stopPolling()
        }
      } catch {
        consecutiveFailures += 1
        if (consecutiveFailures >= STATUS_POLL_GIVE_UP) {
          stopPolling()
          setPhaseBoth('error')
          setErrorBoth('下载时和本地服务断开了，请检查网络后重试。', 'downloading')
        }
        // 没到次数就下次轮询再试。
      }
    }, 1000)
  }, [setPhaseBoth, setErrorBoth, stopPolling])

  /** 启动时从服务端把下载状态接回来：刷新页面、或上次打开时下载已在
   * 进行中，进度不从 0 重来——服务端一直在下，问它现在到哪了就行。 */
  const resume = useCallback(async () => {
    try {
      const res = await fetch('/api/update/status')
      const data = (await res.json().catch(() => ({}))) as CheckPayload
      if (!mountedRef.current) return
      const status = String(data?.status || '')
      if (typeof data?.version === 'string' && data.version) {
        setInfo({
          version: data.version,
          size: Number(data.size) || 0,
          sha256: String(data.sha256 || ''),
          notes: String(data.notes || ''),
          current_version: String(data.current_version || ''),
        })
        setError('')
        setErrorKind(null)
      }
      if (status === 'downloading' || status === 'verifying') {
        setPhaseBoth(status)
        setProgress(Number(data?.progress) || 0)
        startPolling()
      } else if (status === 'ready') {
        setPhaseBoth('ready')
        setProgress(100)
      } else if (status === 'error') {
        setPhaseBoth('error')
        setErrorBoth(String(data?.error || '下载失败了，稍后再试。'), 'downloading')
      }
    } catch {
      // 状态读不到就不管：check() 那边会照常查有没有新版。
    }
  }, [setPhaseBoth, setErrorBoth, startPolling])

  const start = useCallback(async () => {
    if (!info) return
    setError('')
    setErrorKind(null)
    setPhaseBoth('downloading')
    setProgress(0)
    try {
      const res = await fetch('/api/update/start', { method: 'POST' })
      const data = (await res.json().catch(() => ({}))) as { error?: string }
      if (!res.ok) throw new Error(String(data?.error || '没开始下载，稍后再试。'))
    } catch (err) {
      setPhaseBoth('error')
      setErrorBoth(err instanceof Error ? err.message : '没开始下载，稍后再试。', 'downloading')
      return
    }
    startPolling()
  }, [info, setPhaseBoth, setErrorBoth, startPolling])

  // 挂载时启动检查 + 从服务端接回下载状态：必须放在 resume 定义之后，
  // 依赖数组在渲染时就求值，写在前面会撞上 const 的暂时性死区。
  useEffect(() => {
    void check()
    void fetchHealth()
    void fetchManualDownloadUrl()
    void resume()
  }, [check, fetchHealth, fetchManualDownloadUrl, resume])

  const apply = useCallback(async () => {
    setError('')
    setErrorKind(null)
    setPhaseBoth('applying')
    try {
      const res = await fetch('/api/update/apply', { method: 'POST' })
      const data = (await res.json().catch(() => ({}))) as { error?: string }
      if (!res.ok) throw new Error(String(data?.error || '这次没换上，稍后再试。'))
    } catch (err) {
      setPhaseBoth('error')
      setErrorBoth(err instanceof Error ? err.message : '这次没换上，稍后再试。', 'applying')
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
        setErrorBoth('新版本应该换好了，但面板没重新打开，关掉再双击打开一次就行。', 'applying')
      }
    }, 2000)
  }, [setPhaseBoth, setErrorBoth, stopHealthPolling])

  const dismiss = useCallback(() => {
    setDismissed(true)
  }, [])

  const retry = useCallback(async () => {
    // 失败后回到能重试的状态：清掉错误和进度，再查一次有没有新版。
    // 查不到也不卡着：旧的版本信息留着，横幅上的“再试一次”还能点。
    setError('')
    setErrorKind(null)
    setProgress(0)
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
    errorKind,
    manualDownloadUrl,
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

const UpdateContext = createContext<UpdateState | null>(null)

/**
 * 更新状态全局化（v22 修复）：下载任务与进度只活一份，挂在 App 顶层。
 * 以前每个页面各自 useUpdate()，路由一切换组件卸载、轮询被 cleanup
 * 停掉，回来就是全新的 idle——按钮变回“马上更新”，进度也没了。
 * 现在 Provider 常驻，切页面只换页面组件，轮询与进度不断。
 */
export function UpdateProvider({ children }: { children: ReactNode }) {
  const update = useUpdate()
  return <UpdateContext.Provider value={update}>{children}</UpdateContext.Provider>
}

/** 读全局更新状态：必须在 <UpdateProvider> 里面用。 */
export function useUpdateContext(): UpdateState {
  const update = useContext(UpdateContext)
  if (!update) throw new Error('useUpdateContext 必须在 <UpdateProvider> 里面用')
  return update
}
