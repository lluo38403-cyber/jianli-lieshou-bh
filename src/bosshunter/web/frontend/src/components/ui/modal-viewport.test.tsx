import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { ModalPortal } from './modal-portal'
import { UpdateModal } from '@/components/dashboard/UpdateModal'
import { VerificationModal } from '@/components/dashboard/VerificationModal'
import { ScoreJobsDialog } from '@/components/dashboard/ScoreJobsDialog'
import { CollectJobsDialog } from '@/components/dashboard/CollectJobsDialog'
import { UnsavedChangesDialog } from '@/components/layout/UnsavedChangesDialog'
import { ExitAppDialog } from '@/components/layout/ExitAppDialog'
import type { UpdateState } from '@/hooks/useUpdate'
import type { VerificationHold } from '@/hooks/useDashboard'

/**
 * 任务四（v32）回归：弹窗滚动到底部也必须在视口内。
 *
 * 根因见 modal-portal.tsx：页面外层 .animate-page-in 的入场动画终态保留
 * transform: translateY(0)，祖先一带 transform，fixed 弹窗的定位参照就从
 * 视口变成整页容器——弹窗被渲染到整页中部/底部，滚到底才看得见。
 * jsdom 没有真实布局，算不出像素位置，所以这里用结构断言做可核对的等价检查：
 * 弹窗的 fixed 根节点必须是 document.body 的直接子节点（在任何带 transform
 * 或可滚动的祖先之外），浏览器才会拿它相对视口定位；同时按钮行必须在滚动
 * 正文区之外，内容再长也不会把核心按钮顶出视口。
 */

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

function fakeHold(overrides: Partial<VerificationHold> = {}): VerificationHold {
  return {
    id: 3,
    platform: 'boss',
    url: 'https://www.zhipin.com/web/user/verify?x=1',
    evidence: 'captcha_url',
    created_at: '2026-11-19 13:00:00',
    ...overrides,
  }
}

/** 模拟真实页面结构：可滚动的 <main> 里包着带 transform 的 .animate-page-in，
 * 再在里面放一个很高的占位块并滚到底——弹窗就挂在这个容器里触发。 */
function ScrolledPage({ children }: { children: React.ReactNode }) {
  return (
    <div data-testid="scrolled-main" style={{ height: 600, overflowY: 'auto' }}>
      <div style={{ transform: 'translateY(0)' }}>
        <div style={{ height: 3000 }}>页面内容（滚到底）</div>
        {children}
      </div>
    </div>
  )
}

beforeEach(() => {
  window.localStorage.clear()
})

afterEach(() => {
  cleanup()
  window.localStorage.clear()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  document.body.style.overflow = ''
})

describe('ModalPortal：弹窗逃出带 transform 的祖先，fixed 重新相对视口', () => {
  it('把子节点直接挂到 body，不留在带 transform 的容器里', () => {
    render(
      <ScrolledPage>
        <ModalPortal>
          <div data-testid="portal-child" className="fixed inset-0">
            弹窗
          </div>
        </ModalPortal>
      </ScrolledPage>,
    )
    const child = screen.getByTestId('portal-child')
    expect(child.parentElement).toBe(document.body)
    expect(child.className).toContain('fixed')
  })

  it('滚动到底部后 UpdateModal 仍在视口内（挂在 body 上、贴视口右下角），按钮行在滚动区之外', async () => {
    render(
      <ScrolledPage>
        <UpdateModal update={fakeUpdate()} />
      </ScrolledPage>,
    )
    const modal = await screen.findByTestId('update-modal')
    expect(modal.parentElement).toBe(document.body)
    expect(modal.className).toContain('fixed')
    expect(modal.className).toContain('bottom-4')
    const body = screen.getByTestId('update-modal-body')
    const footer = screen.getByTestId('update-modal-footer')
    expect(body.className).toContain('overflow-y-auto')
    expect(body.contains(footer)).toBe(false)
    expect(footer.contains(screen.getByRole('button', { name: '前往下载' }))).toBe(true)
  })

  it('滚动到底部后验证弹窗仍在视口内居中，“继续投递”在滚动区之外', () => {
    render(
      <ScrolledPage>
        <VerificationModal hold={fakeHold()} onContinue={vi.fn()} onStop={vi.fn()} canStop />
      </ScrolledPage>,
    )
    const dialog = screen.getByRole('alertdialog', { name: '平台安全验证' })
    expect(dialog.parentElement).toBe(document.body)
    expect(dialog.className).toContain('fixed')
    const body = screen.getByTestId('verification-modal-body')
    const footer = screen.getByTestId('verification-modal-footer')
    expect(body.className).toContain('overflow-y-auto')
    expect(body.contains(footer)).toBe(false)
    expect(footer.contains(screen.getByRole('button', { name: '我已完成验证，继续投递' }))).toBe(true)
  })

  it('滚动到底部后打分弹窗仍在视口内居中，“开始打分”在滚动区之外', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        const data = url === '/api/scoring/runs'
          ? []
          : { eligible_jobs: 5, skipped_jobs: 1, first_attempt_requests: 5, max_attempts_per_job: 2, max_possible_requests: 10, note: '' }
        return new Response(JSON.stringify(data), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }),
    )
    render(
      <ScrolledPage>
        <ScoreJobsDialog open selectedJobIds={[]} onClose={vi.fn()} onStart={vi.fn()} />
      </ScrolledPage>,
    )
    const startButton = await screen.findByRole('button', { name: '开始打分' })
    const dialog = screen.getByRole('dialog', { name: '单独打分' })
    expect(dialog.parentElement).toBe(document.body)
    expect(dialog.className).toContain('fixed')
    const body = screen.getByTestId('score-dialog-body')
    const footer = screen.getByTestId('score-dialog-footer')
    expect(body.className).toContain('overflow-y-auto')
    expect(body.contains(footer)).toBe(false)
    expect(footer.contains(startButton)).toBe(true)
  })

  it('滚动到底部后岗位采集弹窗仍在视口内居中', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        const data = url === '/api/config'
          ? { search: { keywords: ['AI'], cities: ['北京'] } }
          : url.startsWith('/api/collection/runs')
            ? []
            : { ok: true, cities: [] }
        return new Response(JSON.stringify(data), { status: 200 })
      }),
    )
    render(
      <ScrolledPage>
        <CollectJobsDialog open activeTask={null} onClose={vi.fn()} onStart={vi.fn(async () => ({ ok: true }))} />
      </ScrolledPage>,
    )
    const dialog = await screen.findByRole('dialog', { name: '找新岗位' })
    expect(dialog.parentElement).toBe(document.body)
    expect(dialog.className).toContain('fixed')
  })

  it('滚动到底部后“设置还没保存”与“退出程序”确认框仍在视口内居中', () => {
    const { rerender } = render(
      <ScrolledPage>
        <UnsavedChangesDialog saving={false} error={null} onSaveAndLeave={vi.fn()} onDiscardAndLeave={vi.fn()} onStay={vi.fn()} />
      </ScrolledPage>,
    )
    const unsaved = screen.getByRole('dialog', { name: '设置还没保存' })
    expect(unsaved.parentElement).toBe(document.body)
    expect(unsaved.className).toContain('fixed')

    rerender(
      <ScrolledPage>
        <ExitAppDialog onStay={vi.fn()} />
      </ScrolledPage>,
    )
    const exit = screen.getByRole('dialog', { name: '退出应用程序' })
    expect(exit.parentElement).toBe(document.body)
    expect(exit.className).toContain('fixed')
  })
})
