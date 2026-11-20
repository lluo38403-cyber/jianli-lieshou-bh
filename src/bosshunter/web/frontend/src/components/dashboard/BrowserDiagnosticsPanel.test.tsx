import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { BrowserDiagnosticsPanel, type BrowserDiagnostics } from './BrowserDiagnosticsPanel'

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

function jsonResponse(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

const failed: BrowserDiagnostics = {
  ok: false,
  error_kind: 'connection_failed',
  message: '浏览器已经装好了，但是现在连不上。',
  next_steps: ['点任意一个平台的“去登录”，程序会自动打开一个能连接的浏览器窗口。'],
  discovered: { chrome: 'C:/Program Files/Google/Chrome/Application/chrome.exe', edge: null },
  diagnostics: { chrome: false, discovered: { chrome: 'C:/Chrome/chrome.exe' } },
}

it('shows a short Chinese message for connection failure, never raw JSON or code logs', () => {
  render(<BrowserDiagnosticsPanel diagnostics={failed} onRefresh={() => {}} />)
  expect(screen.getByText('浏览器连不上了')).toBeTruthy()
  expect(screen.getByText('浏览器已经装好了，但是现在连不上。')).toBeTruthy()
  expect(screen.getByText('点任意一个平台的“去登录”，程序会自动打开一个能连接的浏览器窗口。')).toBeTruthy()
  // v27：“一键启动浏览器”按钮已删；连接失败也只给“重新检测”，拉起走各平台的“去登录”。
  expect(screen.queryByRole('button', { name: '一键启动浏览器' })).toBeNull()
  expect(screen.getByRole('button', { name: '重新检测' })).toBeTruthy()
  expect(screen.getByRole('button', { name: '一键复制诊断信息' })).toBeTruthy()
  // 页面上不出现任何代码：没有 JSON 块、没有展开器、没有浏览器路径。
  expect(document.querySelector('pre')).toBeNull()
  expect(screen.queryByText(/看看诊断详情/)).toBeNull()
  expect(screen.queryByText(/chrome\.exe/)).toBeNull()
  expect(screen.queryByText(/"chrome"/)).toBeNull()
})

it('shows the recheck button and the manual browser picker only when no browser is found', () => {
  const notFound: BrowserDiagnostics = {
    ok: false,
    error_kind: 'not_found',
    message: '电脑上没找到 Chrome 或 Edge。',
    next_steps: ['先装一个 Chrome 再回来。'],
    discovered: { chrome: null, edge: null },
    diagnostics: {},
  }
  render(<BrowserDiagnosticsPanel diagnostics={notFound} onRefresh={() => {}} />)
  expect(screen.getByText('没找到能用的浏览器')).toBeTruthy()
  expect(screen.getByRole('button', { name: '重新检测' })).toBeTruthy()
  expect(screen.queryByRole('button', { name: '一键启动浏览器' })).toBeNull()
  expect(screen.getByPlaceholderText('填入浏览器程序的完整路径')).toBeTruthy()
  expect(document.querySelector('pre')).toBeNull()
})

it('the copy button copies the full diagnostics JSON in the background', async () => {
  const writeText = vi.fn().mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText },
    configurable: true,
  })
  render(<BrowserDiagnosticsPanel diagnostics={failed} onRefresh={() => {}} />)
  fireEvent.click(screen.getByRole('button', { name: '一键复制诊断信息' }))
  await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1))
  expect(writeText).toHaveBeenCalledWith(JSON.stringify(failed, null, 2))
  await waitFor(() => expect(screen.getByRole('button', { name: '已复制' })).toBeTruthy())
})

it('recheck posts to the diagnostics endpoint and never launches the browser', async () => {
  // v27：“一键启动浏览器”按钮已删，拉起统一走各平台的“去登录”；这里只检测。
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }))
  vi.stubGlobal('fetch', fetchMock)
  const onRefresh = vi.fn()
  render(<BrowserDiagnosticsPanel diagnostics={failed} onRefresh={onRefresh} />)
  expect(screen.queryByRole('button', { name: '一键启动浏览器' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '重新检测' }))
  await waitFor(() => expect(onRefresh).toHaveBeenCalledTimes(1))
  expect(fetchMock).toHaveBeenCalledWith('/api/browser/diagnostics', expect.objectContaining({ method: 'POST' }))
  expect(fetchMock).not.toHaveBeenCalledWith('/api/browser/launch', expect.anything())
})

it('recheck posts to the diagnostics endpoint only when no browser is found', async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }))
  vi.stubGlobal('fetch', fetchMock)
  const onRefresh = vi.fn()
  const notFound: BrowserDiagnostics = {
    ok: false,
    error_kind: 'not_found',
    message: '电脑上没找到浏览器。',
    next_steps: ['先装一个浏览器再回来。'],
    discovered: { chrome: null, edge: null },
    diagnostics: {},
  }
  render(<BrowserDiagnosticsPanel diagnostics={notFound} onRefresh={onRefresh} />)
  fireEvent.click(screen.getByRole('button', { name: '重新检测' }))
  await waitFor(() => expect(onRefresh).toHaveBeenCalledTimes(1))
  expect(fetchMock).toHaveBeenCalledWith('/api/browser/diagnostics', expect.objectContaining({ method: 'POST' }))
  expect(fetchMock).not.toHaveBeenCalledWith('/api/browser/launch', expect.anything())
})

it('renders nothing when the browser is connected', () => {
  const { container } = render(
    <BrowserDiagnosticsPanel
      diagnostics={{ ok: true, error_kind: null, message: '已连接', next_steps: [], discovered: {}, diagnostics: {} }}
      onRefresh={() => {}}
    />,
  )
  expect(container.firstChild).toBeNull()
})
