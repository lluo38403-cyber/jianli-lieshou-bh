import { describe, expect, it } from 'vitest'
import type { PlatformLoginInfo } from '@/hooks/useDashboard'
import {
  derivePlatformLoginStates,
  getEffectiveLoginStatus,
  isPlatformLoginStale,
  mergePlatformLoginStates,
  PLATFORM_LOGIN_STALE_MS,
} from './platformLogin'

// 与后端 tests/test_platform_login_state.py 同一套判定向量：前端推导
// 必须和后端 derive_platform_login_states 说一样的话。
function diagnosticsResult(overrides: Record<string, unknown> = {}) {
  return {
    chrome: true,
    boss_tab: { targetId: '1', url: 'https://www.zhipin.com/web/geek/job' },
    zhilian_tab: { targetId: '2', url: 'https://www.zhaopin.com/' },
    zhilian_page: { status: 'ready', message: '已发现智联搜索框，未发现登录墙' },
    job51_tab: { targetId: '3', url: 'https://we.51job.com/pc/search' },
    job51_page: { status: 'ready', message: '已发现前程无忧搜索框，未发现登录墙' },
    liepin_tab: { targetId: '4', url: 'https://www.liepin.com/zhaopin/' },
    liepin_page: { status: 'ready', message: '已发现猎聘搜索框，未发现登录墙' },
    ...overrides,
  }
}

describe('derivePlatformLoginStates', () => {
  it('marks every platform logged in when the pages are ready', () => {
    const states = derivePlatformLoginStates(diagnosticsResult())
    expect(states && Object.fromEntries(Object.entries(states).map(([k, v]) => [k, v.status]))).toEqual({
      boss: 'logged_in',
      zhilian: 'logged_in',
      '51job': 'logged_in',
      liepin: 'logged_in',
    })
    expect(states?.boss?.detail).toBe('已发现 BOSS 直聘页面，未见登录页')
  })

  it('marks BOSS logged out when its tab sits on the login page', () => {
    const states = derivePlatformLoginStates(diagnosticsResult({
      boss_tab: { targetId: '1', url: 'https://www.zhipin.com/web/user/login' },
    }))
    expect(states?.boss?.status).toBe('logged_out')
    expect(states?.boss?.detail).toBe('BOSS 直聘页面停在登录页')
  })

  it('marks login_required pages logged out', () => {
    const states = derivePlatformLoginStates(diagnosticsResult({
      zhilian_page: { status: 'login_required', message: '智联页面要求登录' },
      job51_page: { status: 'login_required', message: '前程无忧页面要求登录' },
      liepin_page: { status: 'login_required', message: '猎聘页面要求登录' },
    }))
    expect(states?.zhilian?.status).toBe('logged_out')
    expect(states?.['51job']?.status).toBe('logged_out')
    expect(states?.liepin?.status).toBe('logged_out')
  })

  it('marks blocked or missing pages unknown', () => {
    const states = derivePlatformLoginStates(diagnosticsResult({
      boss_tab: null,
      zhilian_page: { status: 'blocked', message: '智联页面受到拦截' },
      job51_tab: null,
      job51_page: null,
      liepin_page: { status: 'selector_changed', message: '未识别到猎聘搜索页结构' },
    }))
    expect(states?.boss?.status).toBe('unknown')
    expect(states?.zhilian?.status).toBe('unknown')
    expect(states?.['51job']?.status).toBe('unknown')
    expect(states?.liepin?.status).toBe('unknown')
    expect(states?.zhilian?.detail).toBe('智联页面受到拦截')
  })

  it('returns null when the browser is not connected, so the old values stay', () => {
    expect(derivePlatformLoginStates({ chrome: false })).toBeNull()
    expect(derivePlatformLoginStates({})).toBeNull()
    expect(derivePlatformLoginStates(null)).toBeNull()
  })

  it('matches the backend loose BOSS marker: any /user/ URL reads as logged out', () => {
    // 与后端口径一致（包括它宽松的那一面）：BOSS 标签页地址里只要有
    // /user/ 就判掉线。采集侧 boss.py 用的是精确正则（只认
    // /web/user/login 和 ?ka=header-login），这里先跟后端预检口径走，
    // 不擅自收紧，否则卡片和“检查能不能跑”会各说各话。
    const states = derivePlatformLoginStates(diagnosticsResult({
      boss_tab: { targetId: '1', url: 'https://www.zhipin.com/web/user/?ka=header-login' },
    }))
    expect(states?.boss?.status).toBe('logged_out')
  })
})

describe('isPlatformLoginStale / getEffectiveLoginStatus', () => {
  const now = Date.now()
  const fresh = (status: PlatformLoginInfo['status']): PlatformLoginInfo => ({
    status,
    checked_at: new Date(now - 60_000).toISOString(),
    detail: '',
  })
  const stale = (status: PlatformLoginInfo['status']): PlatformLoginInfo => ({
    status,
    checked_at: new Date(now - PLATFORM_LOGIN_STALE_MS - 60_000).toISOString(),
    detail: '',
  })

  it('treats missing or unparsable checked_at as stale', () => {
    expect(isPlatformLoginStale(undefined, now)).toBe(true)
    expect(isPlatformLoginStale({ status: 'logged_in' }, now)).toBe(true)
    expect(isPlatformLoginStale({ status: 'logged_in', checked_at: 'not-a-date' }, now)).toBe(true)
    expect(isPlatformLoginStale(fresh('logged_in'), now)).toBe(false)
    expect(isPlatformLoginStale(stale('logged_in'), now)).toBe(true)
  })

  it('shows a stale logged-out row as unknown, never as dropped', () => {
    expect(getEffectiveLoginStatus(stale('logged_out'), now)).toBe('unknown')
    expect(getEffectiveLoginStatus(stale('logged_in'), now)).toBe('unknown')
    expect(getEffectiveLoginStatus(fresh('logged_out'), now)).toBe('logged_out')
    expect(getEffectiveLoginStatus(fresh('logged_in'), now)).toBe('logged_in')
    expect(getEffectiveLoginStatus(undefined, now)).toBe('unknown')
  })

  it('shows checking only for stale or missing rows while a check is running', () => {
    expect(getEffectiveLoginStatus(stale('logged_out'), now, true)).toBe('checking')
    expect(getEffectiveLoginStatus(undefined, now, true)).toBe('checking')
    // 新鲜结论在刷新进行中也不闪回“正在确认”。
    expect(getEffectiveLoginStatus(fresh('logged_out'), now, true)).toBe('logged_out')
    expect(getEffectiveLoginStatus(fresh('logged_in'), now, true)).toBe('logged_in')
  })
})

describe('mergePlatformLoginStates', () => {
  it('keeps the newer checked_at per platform', () => {
    const oldRow: PlatformLoginInfo = { status: 'logged_out', checked_at: '2026-11-19T08:00:00', detail: '旧' }
    const newRow: PlatformLoginInfo = { status: 'logged_in', checked_at: new Date().toISOString(), detail: '新' }
    expect(mergePlatformLoginStates({ boss: oldRow }, { boss: newRow }).boss).toBe(newRow)
    expect(mergePlatformLoginStates({ boss: newRow }, { boss: oldRow }).boss).toBe(newRow)
    expect(mergePlatformLoginStates({ boss: oldRow }, {}).boss).toBe(oldRow)
    expect(mergePlatformLoginStates({}, { boss: newRow }).boss).toBe(newRow)
    expect(mergePlatformLoginStates(null, null)).toEqual({})
  })
})
