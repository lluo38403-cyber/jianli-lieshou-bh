/**
 * 本地面板接口的统一请求封装。
 *
 * 根因：面板启动、更新重启后，后端还没起来时浏览器先打开，前端各处直接
 * fetch，联不上就报 Failed to fetch；有的地方直接停在报错状态，有的连
 * 报错都没有（静默吞掉），用户只看到黑屏/骨架屏。
 *
 * 约定：
 * - 只重试读请求（GET/HEAD 这类幂等请求）。写请求（POST 等）默认只发一次，
 *   免得重复写；调用方想重试写请求要自己显式处理。
 * - 只重试“本地服务没起来”：联不上（fetch 直接抛错）、超时、5xx。
 *   4xx 是业务错误，不重试，直接把响应交回给调用方。
 * - 每次尝试单独计时超时，尝试之间指数退避（500ms → 1s → 2s → 4s 上限），
 *   带一点随机抖动，免得多个请求同时撞回来。
 * - 调用方取消（卸载、切页）时立即停，不再重试。
 */

export interface FetchWithRetryOptions {
  /** 失败后最多再试几次，默认 3（连第一次一共 4 次尝试）。 */
  retries?: number
  /** 首次退避毫秒数，默认 500。 */
  baseDelayMs?: number
  /** 退避上限毫秒数，默认 4000。 */
  maxDelayMs?: number
  /** 每次尝试单独的超时毫秒数，默认 10000。 */
  timeoutMs?: number
  /** 调用方的取消信号：abort 后立即停，不再重试。 */
  signal?: AbortSignal
  /** 每次失败、准备再试之前回调一次（attempt 从 1 开始：已失败次数）。 */
  onRetry?: (attempt: number, error: unknown) => void
}

const DEFAULT_RETRIES = 3
const DEFAULT_BASE_DELAY_MS = 500
const DEFAULT_MAX_DELAY_MS = 4000
const DEFAULT_TIMEOUT_MS = 10000

/** 5xx：服务端那边的问题，本地服务没起来时常见，可重试。 */
export function isRetryableStatus(status: number): boolean {
  return status >= 500
}

export function isAbortError(error: unknown): boolean {
  if (error instanceof DOMException) return error.name === 'AbortError'
  return (error as { name?: string } | null)?.name === 'AbortError'
}

/**
 * 读响应体也加超时：fetch 只管到响应头，body 卡住时 res.json() 会一直
 * 等——请求层的超时管不到它，骨架屏就一直晾着。超时按失败处理，
 * 调用方才能撤掉骨架屏、给重试入口。
 */
export async function readJsonWithTimeout<T = any>(res: Response, timeoutMs = 10000): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined
  try {
    const timeout = new Promise<never>((_resolve, reject) => {
      timer = setTimeout(() => reject(new Error('读取数据超时了')), timeoutMs)
    })
    return await Promise.race([res.json() as Promise<T>, timeout])
  } finally {
    if (timer !== undefined) clearTimeout(timer)
  }
}

/**
 * 这次失败是不是“本地服务没起来”：联不上、超时、5xx。
 * 后端下线、端口被占、更新重启中，都是这个样子。
 */
export function isServiceNotReadyError(error: unknown): boolean {
  if (error instanceof TypeError) return true
  if (isAbortError(error)) return true
  if (error instanceof Error) return error.message.startsWith('本地服务返回了 ')
  return false
}

function isGetLike(init?: RequestInit): boolean {
  const method = (init?.method || 'GET').toUpperCase()
  return method === 'GET' || method === 'HEAD'
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException('Aborted', 'AbortError'))
      return
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort)
      resolve()
    }, ms)
    const onAbort = () => {
      clearTimeout(timer)
      reject(new DOMException('Aborted', 'AbortError'))
    }
    signal?.addEventListener('abort', onAbort)
  })
}

export async function fetchWithRetry(
  input: RequestInfo | URL,
  init: RequestInit = {},
  options: FetchWithRetryOptions = {},
): Promise<Response> {
  const retries = options.retries ?? DEFAULT_RETRIES
  const baseDelayMs = options.baseDelayMs ?? DEFAULT_BASE_DELAY_MS
  const maxDelayMs = options.maxDelayMs ?? DEFAULT_MAX_DELAY_MS
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS
  const outerSignal = options.signal
  const retryableMethod = isGetLike(init)

  let lastError: unknown = null
  for (let attempt = 0; attempt <= retries; attempt++) {
    if (outerSignal?.aborted) throw new DOMException('Aborted', 'AbortError')
    const controller = new AbortController()
    const onOuterAbort = () => controller.abort()
    if (outerSignal) outerSignal.addEventListener('abort', onOuterAbort)
    const timer = setTimeout(() => controller.abort(), timeoutMs)
    // 取消要立刻生效，不能只靠 fetch 自己响应 abort（有的实现/测试替身不响应）。
    const abortPromise = new Promise<never>((_resolve, reject) => {
      controller.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true })
    })
    let response: Response | null = null
    let failure: unknown = null
    try {
      const fetchPromise = fetch(input, { ...init, signal: controller.signal })
      // 兜底：万一这路请求后来才失败，不要变成没人接的异常。
      void fetchPromise.catch(() => {})
      response = await Promise.race([fetchPromise, abortPromise])
    } catch (err) {
      failure = err
    } finally {
      clearTimeout(timer)
      if (outerSignal) outerSignal.removeEventListener('abort', onOuterAbort)
    }
    // 调用方取消了：直接停，不重试。
    if (outerSignal?.aborted) throw failure ?? new DOMException('Aborted', 'AbortError')
    if (failure !== null) {
      // 写请求只发一次；读请求到次数了也停。
      if (!retryableMethod || attempt === retries) throw failure
      lastError = failure
    } else if (response !== null) {
      // 4xx 直接交回；5xx 且还能重试就等一等再试；到次数了把最后一次响应交回。
      if (!isRetryableStatus(response.status) || !retryableMethod || attempt === retries) return response
      lastError = new Error(`本地服务返回了 ${response.status}`)
    }
    options.onRetry?.(attempt + 1, lastError)
    const delay = Math.min(maxDelayMs, baseDelayMs * 2 ** attempt) + Math.floor(Math.random() * 100)
    try {
      await sleep(delay, outerSignal)
    } catch {
      throw new DOMException('Aborted', 'AbortError')
    }
  }
  throw lastError ?? new Error('请求失败')
}
