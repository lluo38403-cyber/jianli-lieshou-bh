import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { fetchWithRetry, isAbortError, readJsonWithTimeout } from '@/lib/api'
import { skipCloseConfirm } from '@/lib/closeConfirm'

export interface UpdateInfo {
  version: string
  size: number
  sha256: string
  notes: string
  current_version: string
  /** 失败原因（后端原话）：error 态的 info 也留着它，横幅和弹窗才有话可说。 */
  reason?: string
  /** 安装包是不是本地已验过的缓存包：是就不用重新下载，再试一次直接恢复。 */
  downloadCached?: boolean
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
  reason?: string
  download_cached?: boolean
  cached_version?: string | null
  /** 后端 run_check 报的检查失败：status="error" + reason_code="check_failed"。 */
  check_failed?: boolean
  /** 后端状态里的原因码：check_failed（只是没查到）或 stuck/nopkg…（上次更新换失败了）。 */
  reason_code?: string
  /** 上次检查失败的细分原因（timeout / network / bad_sha …），见 updater.py。 */
  check_reason?: string
}

/** 前端单次检查超时必须大于后端 10 秒（updater.py CHECK_TIMEOUT）：
 * 中转往返 8–10 秒的请求，8 秒超时每次都在后端返回前掐断、再重试，
 * 永远拿不到成功响应。12 秒留 2 秒余量；重试只剩 1 次兜底瞬断，
 * 最坏总耗时约 2×12 秒 + 退避 0.5 秒 ≈ 25 秒，不会一直转圈。
 * （另一条路是后端收紧到 5 秒快失败，没选：后端 10 秒是给中转慢
 * 往返留的，快失败会把 8–10 秒的正常慢响应也判成失败。） */
export const BACKEND_CHECK_TIMEOUT_MS = 10000
export const UPDATE_CHECK_TIMEOUT_MS = 12000
export const UPDATE_CHECK_RETRIES = 1
/** 自动复查间隔 30 分钟：见下面复查 effect 的注释。 */
export const UPDATE_AUTO_RECHECK_INTERVAL_MS = 30 * 60 * 1000
/** 读响应体也加超时：fetch 只管到响应头，body 卡住时 res.json()
 * 会一直等——请求层的超时管不到它。本地后端、包体很小，5 秒够了。 */
export const UPDATE_CHECK_JSON_TIMEOUT_MS = 5000

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
  /** 检查序号：每次 check() +1，只有最新一次检查的响应能碰状态，
   * 过期的自动检查回来也不许清 info、改 phase、关转圈。 */
  const checkSeqRef = useRef(0)
  /** 正在跑的检查：同一路（force 相同）连点共用同一个请求；另一路
   * 启动时把这路的请求停掉（AbortController），它已经过时了。 */
  const checkInFlightRef = useRef<{
    force: boolean
    seq: number
    promise: Promise<boolean | null>
    controller: AbortController
  } | null>(null)
  /** 转圈计数：两路检查并发时先结束的那路不许关转圈，等都完了才停。 */
  const checkingCountRef = useRef(0)

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

  const applyPayload = useCallback((data: CheckPayload, options?: { fromFreshCheck?: boolean }) => {
    if (!mountedRef.current) return
    if (typeof data?.current_version === 'string' && data.current_version) {
      setCurrentVersion(data.current_version)
    }
    const downloadCached = data?.download_cached === true
    const reason = typeof data?.reason === 'string' ? data.reason : ''
    const buildInfo = (): UpdateInfo => ({
      version: String(data.version),
      size: Number(data.size) || 0,
      sha256: String(data.sha256 || ''),
      notes: String(data.notes || ''),
      current_version: String(data.current_version || ''),
      reason: reason || undefined,
      downloadCached,
    })
    if (data?.update_available && typeof data?.version === 'string' && data.version) {
      setInfo(buildInfo())
      setError('')
      setErrorKind(null)
    } else if (data?.status === 'error' && typeof data?.version === 'string' && data.version) {
      // 失败也留着版本信息：横幅和弹窗要靠它显示原因和“再试一次”，不能置空。
      setInfo(buildInfo())
    } else if (
      options?.fromFreshCheck
      && phaseRef.current === 'idle'
      && data?.update_available === false
      && data?.status !== 'error'
      && !data?.check_failed
      && !data?.error
    ) {
      // 明确已是最新才清 info：只有最新一次检查亲口说的才算——
      // 轮询/重试的响应、失败的响应、过期响应都不能清，否则手动
      // 查到的新版会被随后返回的自动检查盖没（录屏终态就是这样）。
      setInfo(null)
    }
  }, [])

  /** 手动检查：true=有新版，false=已经是最新，null=没查到（网络问题）。
   * 竞态：自动检查（启动线程 + 挂载检查，无 force）与手动检查
   * （force=1）会并发——同一路连点共用同一个请求，不同路后启动的
   * 顶掉先启动的，过期响应只把结果交给最新那次，什么都不碰。 */
  const check = useCallback(async (force = false): Promise<boolean | null> => {
    const existing = checkInFlightRef.current
    if (existing && existing.force === force) return existing.promise
    const seq = ++checkSeqRef.current
    const prev = checkInFlightRef.current
    if (prev) prev.controller.abort()
    const controller = new AbortController()
    const promise = (async (): Promise<boolean | null> => {
      checkingCountRef.current += 1
      if (mountedRef.current) setChecking(true)
      try {
        const res = await fetchWithRetry(force ? '/api/update/check?force=1' : '/api/update/check', {}, { timeoutMs: UPDATE_CHECK_TIMEOUT_MS, retries: UPDATE_CHECK_RETRIES, signal: controller.signal })
        const data = await readJsonWithTimeout<CheckPayload>(res, UPDATE_CHECK_JSON_TIMEOUT_MS)
        if (seq !== checkSeqRef.current) {
          // 过期响应：什么都不碰，只把结果交给最新那次检查。
          const latest = checkInFlightRef.current
          if (latest && latest.seq !== seq) return latest.promise
          return null
        }
        applyPayload(data, { fromFreshCheck: true })
        if (data?.status === 'error') {
          if (data?.reason_code === 'check_failed' || data?.check_failed) {
            // 只是没查到（中转慢/断网），不是更新本身失败：自动检查
            // 静默，手动检查才进 error 态、给“再试一次”。
            if (!force) return null
            setPhaseBoth('error')
            setErrorBoth(String(data?.error || '没查到新版本，点“再试一次”就能再查一次。'))
            return null
          }
          // 上次更新换失败留下的报错（reason_code 是 stuck/nopkg…）：
          // 保持老行为，横幅和弹窗要显示原因和“再试一次”。
          setPhaseBoth('error')
          setErrorBoth(String(data?.error || '这次没换上，稍后再试。'), 'downloading')
          return Boolean(data?.version)
        }
        return Boolean(data?.update_available)
      } catch (err) {
        if (seq !== checkSeqRef.current) {
          const latest = checkInFlightRef.current
          if (latest && latest.seq !== seq) return latest.promise
          return null
        }
        // 被新检查顶掉的请求（abort）不是失败：最新那次会给结论。
        if (isAbortError(err)) return null
        // 真没查到：自动检查静默（不弹、不清 info、不改 phase），
        // 手动检查进 error 态——横幅有 info 时显示“再试一次”，
        // 没 info 时页面上的文字提示 + 再试一次按钮接住。
        if (!force) return null
        setPhaseBoth('error')
        setErrorBoth('没查到新版本，点“再试一次”就能再查一次。')
        return null
      } finally {
        checkingCountRef.current = Math.max(0, checkingCountRef.current - 1)
        if (checkingCountRef.current === 0 && mountedRef.current) setChecking(false)
        if (checkInFlightRef.current?.seq === seq) checkInFlightRef.current = null
      }
    })()
    checkInFlightRef.current = { force, seq, promise, controller }
    return promise
  }, [applyPayload, setPhaseBoth, setErrorBoth])

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
        // 每轮都把版本信息接回来：失败时 info 也留着，不置空。
        applyPayload(data)
        const status = String(data?.status || '')
        if (status === 'downloading' || status === 'verifying') {
          setPhaseBoth(status)
          if (mountedRef.current) setProgress(Number(data?.progress) || 0)
        } else if (status === 'ready') {
          setPhaseBoth('ready')
          if (mountedRef.current) setProgress(100)
          stopPolling()
        } else if (status === 'error') {
          // 并发检查失败（check_failed）不算下载失败：轮询继续，
          // 不停、不报错，等下一次轮询再看下载状态。
          if (data?.reason_code === 'check_failed' || data?.check_failed) {
            // 忽略这一轮，继续轮询。
          } else {
            setPhaseBoth('error')
            setErrorBoth(String(data?.error || '下载失败了，稍后再试。'), phaseRef.current === 'verifying' ? 'verifying' : 'downloading')
            stopPolling()
          }
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
  }, [applyPayload, setPhaseBoth, setErrorBoth, stopPolling])

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
          reason: typeof data?.reason === 'string' && data.reason ? data.reason : undefined,
          downloadCached: data?.download_cached === true,
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
        // 查新版失败（check_failed）不是下载失败：不进 error 态、
        // 不弹打扰，info 留着给横幅/弹窗；下载失败才按老样子报错。
        if (data?.reason_code !== 'check_failed' && !data?.check_failed) {
          setPhaseBoth('error')
          setErrorBoth(String(data?.error || '下载失败了，稍后再试。'), 'downloading')
        }
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

  /** 自动复查：面板一直开着时，中转上新发了版也要自己发现，不能只靠
   * 启动那一次检查。lucky 云电脑 2026-11-21 事故：面板从 03:47 开到
   * 12:45，中转 05:31 发了 1.1.16，启动检查当时说“已是最新”是对的，
   * 但之后 7 小时再没查过，横幅/弹窗一直不出现，手动点才出来。
   * 每 30 分钟自动查一次，走自动分支（失败静默、不清已有 info、
   * 不改 phase；和手动检查并发时照 v29 序号去重来）。只在 idle 时
   * 查：下载中 / 已就绪 / 换版中 / 报错时不碰状态，免得把进行中的
   * 更新流程打断、或把失败原因盖掉。后端 600 秒缓存已过期时才是
   * 真查，没过期直接回缓存，不给中转加压。 */
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (phaseRef.current !== 'idle') return
      void check()
    }, UPDATE_AUTO_RECHECK_INTERVAL_MS)
    return () => window.clearInterval(timer)
  }, [check])

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
    // 面板要重启了：新版会自己重新打开面板，旧窗口现在就关掉——
    // 之前是等新版起来再原地刷新，旧窗口复活、跟新窗口冲突（两个窗口
    // 同时存在，换文件还撞锁）。关窗和退进程是两回事：关窗只关这个
    // 页面，服务端 1.5 秒后才退出进程，bat 是等进程退出后才换文件，
    // 所以立即关窗不会打断换文件。
    // 关窗只在 --app 独立窗口里调：普通浏览器标签页不让脚本关窗
    // （调了会被忽略），display-mode: standalone 是 --app 窗口的标志。
    stopHealthPolling()
    const startFallbackPolling = () => {
      // 关不掉（普通标签页）才走这里：等新版起来再刷新本页（旧行为兜底）。
      let tries = 0
      healthTimer.current = window.setInterval(async () => {
        tries += 1
        if (window.closed) {
          stopHealthPolling()
          return
        }
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
    }
    const standalone =
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(display-mode: standalone)').matches
    if (standalone) {
      try {
        // v25：更新换版的代码关窗必须绕过 beforeunload 确认——先置位
        // 再 window.close()，否则换版会卡住等用户点确认。
        skipCloseConfirm()
        window.close()
      } catch {
        // 关不掉就走下面的兜底。
      }
      // 给关窗一点时间：真关掉了这个定时器随窗口一起消失；关不掉
      // （个别浏览器限制）就走兜底，不一直卡在“正在换上”。
      window.setTimeout(() => {
        if (!window.closed) startFallbackPolling()
      }, 1000)
      return
    }
    startFallbackPolling()
  }, [setPhaseBoth, setErrorBoth, stopHealthPolling])

  const dismiss = useCallback(() => {
    setDismissed(true)
  }, [])

  const retry = useCallback(async () => {
    // 先走缓存重试：安装包已经下好验过，后端直接恢复成 ready，不重新下载。
    // 失败信息留着，横幅上的“再试一次”还能点。
    setError('')
    setErrorKind(null)
    setProgress(0)
    try {
      const res = await fetch('/api/update/start', { method: 'POST' })
      const data = (await res.json().catch(() => ({}))) as CheckPayload
      if (res.ok) {
        applyPayload(data)
        if (String(data?.status) === 'ready') {
          setPhaseBoth('ready')
          setProgress(100)
          return
        }
        setPhaseBoth('downloading')
        startPolling()
        return
      }
      // 没有缓存可复用（或缓存对不上）：回退到重新检查新版本再下载。
    } catch {
      // 服务断了也别卡着：同样回退到重新检查。
    }
    if (phaseRef.current === 'error') {
      setPhaseBoth('idle')
    }
    await check(true)
  }, [applyPayload, check, setPhaseBoth, startPolling])

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
