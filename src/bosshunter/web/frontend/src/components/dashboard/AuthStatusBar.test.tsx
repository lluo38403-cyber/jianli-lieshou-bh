import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { AuthStatusBar } from './AuthStatusBar'
import type { AuthHook } from '@/hooks/useAuth'

afterEach(() => cleanup())

function fakeAuth(overrides: Partial<AuthHook> = {}): AuthHook {
  return {
    snapshot: {
      machineIdShort: 'a1b2c3d4e5f6',
      status: 'trial',
      remainingSeconds: 6300,
      offline: false,
      error: '',
    },
    display: {
      buyUrl: 'https://example.com/shop',
      wechatId: 'wx123',
      qrPath: '/wechat-qr.png',
    },
    remainingSeconds: 6300,
    loading: false,
    refresh: vi.fn(),
    activate: vi.fn(async () => ({ ok: true })),
    ...overrides,
  }
}

describe('AuthStatusBar', () => {
  it('试用中显示倒计时与机器码', () => {
    render(<AuthStatusBar auth={fakeAuth()} />)
    expect(screen.getByText('试用中（剩余 01:45:00）')).toBeTruthy()
    expect(screen.getByText(/机器码 a1b2c3d4e5f6/)).toBeTruthy()
    expect(screen.getByPlaceholderText(/输入卡密/)).toBeTruthy()
    expect(screen.getByText('激活')).toBeTruthy()
    expect(screen.getByText('购买天卡/周卡')).toBeTruthy()
    expect(screen.getByText('联系客服')).toBeTruthy()
  })

  it('过期显示已过期请激活', () => {
    const auth = fakeAuth({
      snapshot: {
        machineIdShort: 'a1b2c3d4e5f6',
        status: 'expired',
        remainingSeconds: 0,
        offline: false,
        error: '',
      },
      remainingSeconds: 0,
    })
    render(<AuthStatusBar auth={auth} />)
    expect(screen.getByText('已过期，请激活')).toBeTruthy()
  })

  it('断网时提示先用上次的状态顶着', () => {
    const auth = fakeAuth({
      snapshot: {
        machineIdShort: 'a1b2c3d4e5f6',
        status: 'active',
        remainingSeconds: 6 * 86400,
        offline: true,
        error: '',
      },
      remainingSeconds: 6 * 86400,
    })
    render(<AuthStatusBar auth={auth} />)
    expect(screen.getByText('VIP有效（剩余 6天 0小时）')).toBeTruthy()
    expect(screen.getByText('网络断了，先用上次的状态顶着')).toBeTruthy()
  })

  it('长期有效不数天数，直接显示长期有效', () => {
    const auth = fakeAuth({
      snapshot: {
        machineIdShort: 'a1b2c3d4e5f6',
        status: 'active',
        remainingSeconds: 315360000,
        offline: false,
        error: '',
      },
      remainingSeconds: 315360000,
    })
    render(<AuthStatusBar auth={auth} />)
    expect(screen.getByText('长期有效')).toBeTruthy()
    expect(screen.queryByText(/剩余/)).toBeNull()
  })

  it('点激活把卡密交过去，成功显示好了', async () => {
    const auth = fakeAuth()
    render(<AuthStatusBar auth={auth} />)
    fireEvent.change(screen.getByTestId('auth-card-input'), {
      target: { value: 'JL-D-AAAA-BBBB-CCCC' },
    })
    fireEvent.click(screen.getByTestId('auth-activate'))
    expect(auth.activate).toHaveBeenCalledWith('JL-D-AAAA-BBBB-CCCC')
    expect(await screen.findByText('好了，有效期加上了。')).toBeTruthy()
  })

  it('长期权限的卡激活成功显示已开通长期权限', async () => {
    const auth = fakeAuth({
      activate: vi.fn(async () => ({ ok: true, cardType: 'master' })),
    })
    render(<AuthStatusBar auth={auth} />)
    fireEvent.change(screen.getByTestId('auth-card-input'), {
      target: { value: 'JL-MASTER-TEST' },
    })
    fireEvent.click(screen.getByTestId('auth-activate'))
    expect(await screen.findByText('已开通长期权限。')).toBeTruthy()
  })

  it('激活失败显示后端的大白话错误', async () => {
    const auth = fakeAuth({
      activate: vi.fn(async () => ({ ok: false, error: '这个卡密不对，请检查有没有输错。' })),
    })
    render(<AuthStatusBar auth={auth} />)
    fireEvent.change(screen.getByTestId('auth-card-input'), {
      target: { value: 'JL-D-NOPE-NOPE-NOPE' },
    })
    fireEvent.click(screen.getByTestId('auth-activate'))
    expect(await screen.findByText('这个卡密不对，请检查有没有输错。')).toBeTruthy()
  })

  it('点购买在新窗口打开购买链接', () => {
    const open = vi.fn()
    Object.defineProperty(window, 'open', { configurable: true, value: open })
    render(<AuthStatusBar auth={fakeAuth()} />)
    fireEvent.click(screen.getByTestId('auth-buy'))
    expect(open).toHaveBeenCalledWith('https://example.com/shop', '_blank', 'noopener,noreferrer')
  })

  it('购买链接为空时不显示购买按钮', () => {
    const auth = fakeAuth({
      display: { buyUrl: '', wechatId: 'wx123', qrPath: '/wechat-qr.png' },
    })
    render(<AuthStatusBar auth={auth} />)
    expect(screen.queryByTestId('auth-buy')).toBeNull()
    expect(screen.queryByText('购买天卡/周卡')).toBeNull()
    // 其他入口不受影响
    expect(screen.getByText('联系客服')).toBeTruthy()
  })

  it('点联系客服弹出二维码与微信号，点关闭收起', () => {
    render(<AuthStatusBar auth={fakeAuth()} />)
    expect(screen.queryByTestId('auth-service-modal')).toBeNull()
    fireEvent.click(screen.getByTestId('auth-service-open'))
    expect(screen.getByTestId('auth-service-modal')).toBeTruthy()
    expect(screen.getByText('微信号：wx123')).toBeTruthy()
    expect(screen.getByText('一键复制微信号')).toBeTruthy()
    expect(screen.getByText('使用问题、购买咨询、售后答疑请添加微信')).toBeTruthy()
    fireEvent.click(screen.getByTestId('auth-service-close'))
    expect(screen.queryByTestId('auth-service-modal')).toBeNull()
  })

  it('弹窗用 Portal 挂到 body 上，不受页内动画容器 transform 影响', () => {
    render(<AuthStatusBar auth={fakeAuth()} />)
    fireEvent.click(screen.getByTestId('auth-service-open'))
    const modal = screen.getByTestId('auth-service-modal')
    // 整页外层有个带 transform 的 animate-page-in 容器：弹窗嵌在里面时
    // fixed 会以它为参照、跑偏到视口外；挂到 body 上才是真居中。
    expect(modal.parentElement).toBe(document.body)
  })

  it('二维码图加载失败也不空白，微信号与复制按钮还在', () => {
    render(<AuthStatusBar auth={fakeAuth()} />)
    fireEvent.click(screen.getByTestId('auth-service-open'))
    const img = screen.getByAltText('客服微信二维码')
    fireEvent.error(img)
    expect(screen.getByText('二维码图还没换好')).toBeTruthy()
    expect(screen.getByText('一键复制微信号')).toBeTruthy()
  })

  it('点复制机器码走剪贴板', async () => {
    const writeText = vi.fn(async () => {})
    Object.defineProperty(window.navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    })
    render(<AuthStatusBar auth={fakeAuth()} />)
    fireEvent.click(screen.getByTestId('auth-copy-machine'))
    expect(writeText).toHaveBeenCalledWith('a1b2c3d4e5f6')
    expect(await screen.findByText('已复制')).toBeTruthy()
  })
})
