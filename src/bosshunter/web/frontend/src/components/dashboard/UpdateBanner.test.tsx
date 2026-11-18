import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { UpdateBanner } from './UpdateBanner'
import type { UpdateState } from '@/hooks/useUpdate'

afterEach(() => cleanup())

function fakeUpdate(overrides: Partial<UpdateState> = {}): UpdateState {
  return {
    info: {
      version: '1.0.2',
      size: 66_549_810,
      sha256: 'a'.repeat(64),
      notes: '更快更稳',
      current_version: '1.0.1',
    },
    currentVersion: '1.0.1',
    phase: 'idle',
    progress: 0,
    error: '',
    dismissed: false,
    checking: false,
    check: vi.fn(),
    start: vi.fn(),
    apply: vi.fn(),
    dismiss: vi.fn(),
    retry: vi.fn(),
    ...overrides,
  }
}

describe('UpdateBanner', () => {
  it('shows nothing when there is no update', () => {
    const { container } = render(<UpdateBanner update={fakeUpdate({ info: null })} />)
    expect(container.firstChild).toBeNull()
  })

  it('shows nothing after the user dismisses it', () => {
    const { container } = render(<UpdateBanner update={fakeUpdate({ dismissed: true })} />)
    expect(container.firstChild).toBeNull()
  })

  it('shows plain-language copy, version and notes when an update exists', () => {
    render(<UpdateBanner update={fakeUpdate()} />)
    expect(screen.getByText('有新版本了，点一下就能换上，不用重新下载')).toBeTruthy()
    expect(screen.getByText(/新版本 1\.0\.2/)).toBeTruthy()
    expect(screen.getByText(/更快更稳/)).toBeTruthy()
    expect(screen.getByText('立即更新')).toBeTruthy()
    expect(screen.getByText('先不换')).toBeTruthy()
  })

  it('calls start when 立即更新 is clicked', () => {
    const update = fakeUpdate()
    render(<UpdateBanner update={update} />)
    fireEvent.click(screen.getByText('立即更新'))
    expect(update.start).toHaveBeenCalledTimes(1)
  })

  it('shows download progress while downloading', () => {
    render(<UpdateBanner update={fakeUpdate({ phase: 'downloading', progress: 42 })} />)
    expect(screen.getByText(/正在下载新版本… 42%/)).toBeTruthy()
    expect((screen.getByTestId('update-progress') as HTMLElement).style.width).toBe('42%')
  })

  it('shows 马上换上 when the download is ready', () => {
    const update = fakeUpdate({ phase: 'ready', progress: 100 })
    render(<UpdateBanner update={update} />)
    expect(screen.getByText('新版本下载好了，点一下马上换上')).toBeTruthy()
    fireEvent.click(screen.getByText('马上换上'))
    expect(update.apply).toHaveBeenCalledTimes(1)
  })

  it('shows the error and a retry button when the update fails', () => {
    const update = fakeUpdate({ phase: 'error', error: '下载失败了，稍后再试。' })
    render(<UpdateBanner update={update} />)
    expect(screen.getByText('下载失败了，稍后再试。')).toBeTruthy()
    fireEvent.click(screen.getByText('再试一次'))
    expect(update.retry).toHaveBeenCalledTimes(1)
  })

  it('shows the applying state while the new version is being swapped in', () => {
    render(<UpdateBanner update={fakeUpdate({ phase: 'applying' })} />)
    expect(screen.getByText('正在换上新版本，面板马上重新打开…')).toBeTruthy()
  })
})
