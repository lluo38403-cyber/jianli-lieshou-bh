import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { UpdateBanner } from './UpdateBanner'
import { SKIPPED_UPDATE_VERSION_KEY } from './UpdateModal'
import type { UpdateState } from '@/hooks/useUpdate'

beforeEach(() => {
  window.localStorage.clear()
})

afterEach(() => {
  cleanup()
  window.localStorage.clear()
})

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

  it('shows which step failed and a retry button when the download fails', () => {
    const update = fakeUpdate({ phase: 'error', error: '新版本没下载下来，请检查网络后重试。', errorKind: 'download' })
    render(<UpdateBanner update={update} />)
    expect(screen.getByText('新版本没下载下来')).toBeTruthy()
    expect(screen.getByText(/旧版本还能正常用/)).toBeTruthy()
    fireEvent.click(screen.getByText('再试一次'))
    expect(update.retry).toHaveBeenCalledTimes(1)
  })

  it('shows the verify failure copy when the file does not match', () => {
    render(<UpdateBanner update={fakeUpdate({ phase: 'error', error: '下载的文件对不上，怕文件坏了，这次先不换。', errorKind: 'verify' })} />)
    expect(screen.getByText('下载的文件对不上')).toBeTruthy()
    expect(screen.getByText(/怕文件坏了/)).toBeTruthy()
    expect(screen.getByText('再试一次')).toBeTruthy()
  })

  it('shows the apply failure copy and that settings were not touched', () => {
    render(<UpdateBanner update={fakeUpdate({ phase: 'error', error: '找不到现在用的程序文件，这次先不换。', errorKind: 'apply' })} />)
    expect(screen.getByText('这次没换上')).toBeTruthy()
    expect(screen.getByText(/设置和投递记录都没动/)).toBeTruthy()
  })

  it('shows the raw message when the failure kind is unknown', () => {
    render(<UpdateBanner update={fakeUpdate({ phase: 'error', error: '不知道出了什么问题', errorKind: 'other' })} />)
    expect(screen.getByText('不知道出了什么问题')).toBeTruthy()
    expect(screen.getByText('再试一次')).toBeTruthy()
  })

  it('offers manual download through the shop link when one is known', () => {
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null)
    render(<UpdateBanner update={fakeUpdate({
      phase: 'error',
      error: '新版本没下载下来，请检查网络后重试。',
      errorKind: 'download',
      manualDownloadUrl: 'https://example.com/shop',
    })} />)
    fireEvent.click(screen.getByTestId('update-manual-download'))
    expect(openSpy).toHaveBeenCalledWith('https://example.com/shop', '_blank', 'noopener,noreferrer')
    openSpy.mockRestore()
  })

  it('without a shop link, tells the user where to get the package instead of a dead link', () => {
    render(<UpdateBanner update={fakeUpdate({
      phase: 'error',
      error: '新版本没下载下来，请检查网络后重试。',
      errorKind: 'download',
      manualDownloadUrl: '',
    })} />)
    expect(screen.queryByTestId('update-manual-download')).toBeNull()
    expect(screen.getByText(/去群里或店铺拿最新安装包/)).toBeTruthy()
  })

  it('shows the applying state while the new version is being swapped in', () => {
    render(<UpdateBanner update={fakeUpdate({ phase: 'applying' })} />)
    expect(screen.getByText('正在换上新版本，面板马上重新打开…')).toBeTruthy()
  })

  it('stays hidden after 跳过此版本, like the modal does', () => {
    // 回归：之前只有弹窗查跳过记录，横幅还留着，跳过了还提醒同一件事。
    window.localStorage.setItem(SKIPPED_UPDATE_VERSION_KEY, '1.0.2')
    const { container } = render(<UpdateBanner update={fakeUpdate()} />)
    expect(container.firstChild).toBeNull()
  })

  it('never shows internal release wording: v19 notes become the default copy', () => {
    render(
      <UpdateBanner
        update={fakeUpdate({
          info: {
            version: '1.1.5',
            size: 66_549_810,
            sha256: 'a'.repeat(64),
            notes: 'v19 1.1.5 只升版号，验证 v18 的 update.bat 修复',
            current_version: '1.1.4',
          },
        })}
      />,
    )
    expect(screen.getByText(/修了一些小问题，用起来更稳/)).toBeTruthy()
    expect(screen.queryByText(/只升版号/)).toBeNull()
    expect(screen.queryByText(/update\.bat/)).toBeNull()
  })
})
