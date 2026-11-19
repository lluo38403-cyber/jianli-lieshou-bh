import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { UpdateModal, SKIPPED_UPDATE_VERSION_KEY } from './UpdateModal'
import type { UpdateState } from '@/hooks/useUpdate'

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
    errorKind: null,
    manualDownloadUrl: '',
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

beforeEach(() => {
  window.localStorage.clear()
})

afterEach(() => {
  cleanup()
  window.localStorage.clear()
  vi.restoreAllMocks()
})

describe('UpdateModal', () => {
  it('pops up on its own when a new version is found, showing current -> latest, notes and size', async () => {
    render(<UpdateModal update={fakeUpdate()} />)
    expect(await screen.findByTestId('update-modal')).toBeTruthy()
    expect(screen.getByText(/现在是 v1\.0\.1，最新是 v1\.0\.2/)).toBeTruthy()
    expect(screen.getByText(/更快更稳/)).toBeTruthy()
    expect(screen.getByText(/63 MB/)).toBeTruthy()
    expect(screen.getByText('前往下载')).toBeTruthy()
    expect(screen.getByText('跳过此版本')).toBeTruthy()
    expect(screen.getByText('稍后再说')).toBeTruthy()
  })

  it('does not pop up when already on the latest version', () => {
    render(<UpdateModal update={fakeUpdate({ info: null })} />)
    expect(screen.queryByTestId('update-modal')).toBeNull()
  })

  it('does not pop up after a manual check finds no new version', () => {
    render(<UpdateModal update={fakeUpdate({ info: null })} manualCheckSignal={123} />)
    expect(screen.queryByTestId('update-modal')).toBeNull()
  })

  it('does not pop up when the banner was dismissed with 先不换', () => {
    render(<UpdateModal update={fakeUpdate({ dismissed: true })} />)
    expect(screen.queryByTestId('update-modal')).toBeNull()
  })

  it('skip writes the version to localStorage and never pops for it again', async () => {
    const { unmount } = render(<UpdateModal update={fakeUpdate()} />)
    expect(await screen.findByTestId('update-modal')).toBeTruthy()
    fireEvent.click(screen.getByText('跳过此版本'))
    expect(screen.queryByTestId('update-modal')).toBeNull()
    expect(window.localStorage.getItem(SKIPPED_UPDATE_VERSION_KEY)).toBe('1.0.2')
    unmount()
    // 模拟下次启动：同一版不再弹。
    render(<UpdateModal update={fakeUpdate()} />)
    expect(screen.queryByTestId('update-modal')).toBeNull()
  })

  it('a manual check still pops the modal for a skipped version', async () => {
    window.localStorage.setItem(SKIPPED_UPDATE_VERSION_KEY, '1.0.2')
    const { rerender } = render(<UpdateModal update={fakeUpdate()} manualCheckSignal={0} />)
    expect(screen.queryByTestId('update-modal')).toBeNull()
    rerender(<UpdateModal update={fakeUpdate()} manualCheckSignal={999} />)
    expect(await screen.findByTestId('update-modal')).toBeTruthy()
  })

  it('稍后再说 only closes this time and writes nothing to localStorage', async () => {
    render(<UpdateModal update={fakeUpdate()} />)
    expect(await screen.findByTestId('update-modal')).toBeTruthy()
    fireEvent.click(screen.getByText('稍后再说'))
    expect(screen.queryByTestId('update-modal')).toBeNull()
    expect(window.localStorage.getItem(SKIPPED_UPDATE_VERSION_KEY)).toBeNull()
  })

  it('runs the existing download flow and offers 立即更新 when ready', async () => {
    const update = fakeUpdate()
    const { rerender } = render(<UpdateModal update={update} />)
    expect(await screen.findByTestId('update-modal')).toBeTruthy()
    fireEvent.click(screen.getByText('前往下载'))
    expect(update.start).toHaveBeenCalledTimes(1)

    rerender(<UpdateModal update={{ ...update, phase: 'downloading', progress: 42 }} />)
    expect(screen.getByText(/正在下载新版本… 42%/)).toBeTruthy()
    expect((screen.getByTestId('update-modal-progress') as HTMLElement).style.width).toBe('42%')

    rerender(<UpdateModal update={{ ...update, phase: 'ready', progress: 100 }} />)
    expect(screen.getByText('新版本下载好了，点一下马上换上')).toBeTruthy()
    fireEvent.click(screen.getByText('立即更新'))
    expect(update.apply).toHaveBeenCalledTimes(1)
  })

  it('shows which step failed and a retry button when the update fails', async () => {
    const update = fakeUpdate({ phase: 'error', error: '新版本没下载下来，请检查网络后重试。', errorKind: 'download' })
    const { rerender } = render(<UpdateModal update={fakeUpdate()} />)
    // 弹窗开着时下载出错：留在弹窗里给重试按钮，不闪退。
    expect(await screen.findByTestId('update-modal')).toBeTruthy()
    rerender(<UpdateModal update={update} />)
    expect(screen.getByText('新版本没下载下来')).toBeTruthy()
    expect(screen.getByText(/旧版本还能正常用/)).toBeTruthy()
    fireEvent.click(screen.getByText('再试一次'))
    expect(update.retry).toHaveBeenCalledTimes(1)
  })

  it('shows the verify / apply copy for those failures', async () => {
    const { rerender } = render(<UpdateModal update={fakeUpdate()} />)
    expect(await screen.findByTestId('update-modal')).toBeTruthy()
    rerender(<UpdateModal update={fakeUpdate({ phase: 'error', error: '下载的文件对不上，怕文件坏了，这次先不换。', errorKind: 'verify' })} />)
    expect(screen.getByText('下载的文件对不上')).toBeTruthy()
    rerender(<UpdateModal update={fakeUpdate({ phase: 'error', error: '找不到现在用的程序文件，这次先不换。', errorKind: 'apply' })} />)
    expect(screen.getByText('这次没换上')).toBeTruthy()
    expect(screen.getByText(/设置和投递记录都没动/)).toBeTruthy()
  })

  it('offers manual download through the shop link when one is known', async () => {
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null)
    const { rerender } = render(<UpdateModal update={fakeUpdate()} />)
    expect(await screen.findByTestId('update-modal')).toBeTruthy()
    rerender(<UpdateModal update={fakeUpdate({
      phase: 'error',
      error: '新版本没下载下来，请检查网络后重试。',
      errorKind: 'download',
      manualDownloadUrl: 'https://example.com/shop',
    })} />)
    fireEvent.click(screen.getByTestId('update-modal-manual-download'))
    expect(openSpy).toHaveBeenCalledWith('https://example.com/shop', '_blank', 'noopener,noreferrer')
    openSpy.mockRestore()
  })

  it('never blocks the page: no full-screen overlay, other content stays clickable', async () => {
    const onOtherClick = vi.fn()
    render(
      <div>
        <button type="button" onClick={onOtherClick}>
          页面上的别的按钮
        </button>
        <UpdateModal update={fakeUpdate()} />
      </div>,
    )
    expect(await screen.findByTestId('update-modal')).toBeTruthy()
    // 没有 fixed inset-0 的全屏遮罩。
    expect(document.querySelector('.fixed.inset-0')).toBeNull()
    const modal = screen.getByTestId('update-modal')
    expect(modal.className).not.toContain('inset-0')
    // 弹窗开着，页面上的按钮照样能点。
    fireEvent.click(screen.getByText('页面上的别的按钮'))
    expect(onOtherClick).toHaveBeenCalledTimes(1)
  })
})
