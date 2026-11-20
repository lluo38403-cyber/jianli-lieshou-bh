import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { Sidebar } from './Sidebar'
import { CHECK_UPDATE_EVENT } from '@/lib/updateEvents'

function jsonResponse(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

function stubFetch(version: string) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/health') return jsonResponse({ status: 'ok', version })
      if (url === '/api/history/unresolved-replies/count') return jsonResponse({ count: 0 })
      return jsonResponse({})
    }),
  )
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('Sidebar version display', () => {
  it('shows the version from /api/health, prefixed with v', async () => {
    stubFetch('1.0.5')
    render(
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>,
    )
    expect(await screen.findByTestId('sidebar-version')).toBeTruthy()
    expect(screen.getByText(/v1\.0\.5/)).toBeTruthy()
  })

  it('follows the backend version instead of a hard-coded one', async () => {
    stubFetch('9.9.9')
    render(
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>,
    )
    expect(await screen.findByText(/v9\.9\.9/)).toBeTruthy()
  })

  it('clicking the version asks the current page to check for updates', async () => {
    stubFetch('1.0.5')
    const onCheck = vi.fn()
    window.addEventListener(CHECK_UPDATE_EVENT, onCheck)
    try {
      render(
        <MemoryRouter>
          <Sidebar />
        </MemoryRouter>,
      )
      fireEvent.click(await screen.findByTestId('sidebar-version'))
      expect(onCheck).toHaveBeenCalledTimes(1)
    } finally {
      window.removeEventListener(CHECK_UPDATE_EVENT, onCheck)
    }
  })
})

describe('Sidebar exit app', () => {
  it('点“退出程序”弹出原话确认，点“否”只关弹窗、什么都不做', async () => {
    stubFetch('1.1.11')
    const fetchMock = vi.mocked(fetch)
    render(
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>,
    )
    fireEvent.click(await screen.findByTestId('sidebar-exit-app'))
    expect(
      screen.getByText('是否结束该应用程序，这可能会导致当前进行中的任务结束'),
    ).toBeTruthy()
    fireEvent.click(screen.getByText('否'))
    expect(
      screen.queryByText('是否结束该应用程序，这可能会导致当前进行中的任务结束'),
    ).toBeNull()
    expect(fetchMock.mock.calls.some(call => String(call[0]) === '/api/exit')).toBe(false)
  })

  it('点“是”调 POST /api/exit 并关窗', async () => {
    stubFetch('1.1.11')
    const fetchMock = vi.mocked(fetch)
    const closeSpy = vi.fn()
    Object.defineProperty(window, 'close', { value: closeSpy, configurable: true })
    render(
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>,
    )
    fireEvent.click(await screen.findByTestId('sidebar-exit-app'))
    fireEvent.click(screen.getByText('是'))
    await vi.waitFor(() => {
      expect(fetchMock.mock.calls.some(
        call => String(call[0]) === '/api/exit' && (call[1] as RequestInit)?.method === 'POST',
      )).toBe(true)
    })
    await vi.waitFor(() => expect(closeSpy).toHaveBeenCalled())
    expect(window.__jlSkipCloseConfirm).toBe(true)
  })
})
