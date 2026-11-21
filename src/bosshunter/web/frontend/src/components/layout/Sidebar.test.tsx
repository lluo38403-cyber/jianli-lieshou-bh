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
