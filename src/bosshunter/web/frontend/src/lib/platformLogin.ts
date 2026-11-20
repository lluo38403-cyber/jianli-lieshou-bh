import type { PlatformLoginInfo } from '@/hooks/useDashboard'
import { PLATFORM_IDS, type PlatformId } from '@/lib/platforms'

/**
 * 各平台登录态的前端推导：与后端 preflight.derive_platform_login_states
 * 同一份判定口径（BOSS 看标签页地址是否停在登录页，其余三平台看页面
 * 检测结果；浏览器没连上时无法判断，返回 null、沿用旧值——与后台
 * 巡检“连不上不写库”的口径一致）。后端改了判定口径时这里要同步改，
 * 否则卡片和“检查能不能跑”会各说各话。
 *
 * BOSS 登录页是精确判定，与采集侧 boss.py 同一正则：
 * /\/web\/user\/(?:login|\?ka=header-login)/i —— 只有登录页才算掉线，
 * 个人中心页 https://www.zhipin.com/web/user/ 是已登录，绝不能再用
 * '/user/' 宽松子串判掉线。
 */
export function isBossLoginUrl(url: string): boolean {
  return /\/web\/user\/(?:login|\?ka=header-login)/i.test(url || '')
}
export function derivePlatformLoginStates(
  diagnostics: unknown,
): Partial<Record<PlatformId, PlatformLoginInfo>> | null {
  if (!diagnostics || typeof diagnostics !== 'object') return null
  const result = diagnostics as Record<string, unknown>
  if (!result.chrome) return null
  const now = new Date().toISOString()
  const isRecord = (value: unknown): value is Record<string, unknown> =>
    typeof value === 'object' && value !== null
  const tabUrl = (tab: unknown): string =>
    isRecord(tab) ? String(tab.url || '') : ''
  const pageState = (page: unknown): Record<string, unknown> =>
    isRecord(page) ? page : {}

  const states: Partial<Record<PlatformId, PlatformLoginInfo>> = {}
  const bossUrl = tabUrl(result.boss_tab).toLowerCase()
  if (!isRecord(result.boss_tab)) {
    states.boss = { status: 'unknown', checked_at: now, detail: '未发现已打开的 BOSS 直聘页面' }
  } else if (isBossLoginUrl(bossUrl)) {
    states.boss = { status: 'logged_out', checked_at: now, detail: 'BOSS 直聘页面停在登录页' }
  } else {
    states.boss = { status: 'logged_in', checked_at: now, detail: '已发现 BOSS 直聘页面，未见登录页' }
  }
  const displayNames: Record<'zhilian' | '51job' | 'liepin', string> = {
    zhilian: '智联',
    '51job': '前程无忧',
    liepin: '猎聘',
  }
  for (const [platform, tabKey, pageKey] of [
    ['zhilian', 'zhilian_tab', 'zhilian_page'],
    ['51job', 'job51_tab', 'job51_page'],
    ['liepin', 'liepin_tab', 'liepin_page'],
  ] as const) {
    const display = displayNames[platform]
    const page = pageState(result[pageKey])
    const pageStatus = String(page.status || 'unknown')
    const message = String(page.message || '')
    if (!isRecord(result[tabKey])) {
      states[platform] = { status: 'unknown', checked_at: now, detail: `未发现已打开的${display}页面` }
    } else if (pageStatus === 'ready') {
      states[platform] = { status: 'logged_in', checked_at: now, detail: message || `已发现${display}搜索页，未发现登录墙` }
    } else if (pageStatus === 'login_required') {
      states[platform] = { status: 'logged_out', checked_at: now, detail: message || `${display}页面要求登录` }
    } else {
      states[platform] = { status: 'unknown', checked_at: now, detail: message || `${display}页面状态无法判断` }
    }
  }
  return states
}

/** 后台巡检每 4 分钟写一次登录态；超过 10 分钟没更新的行视为过期——
 * 很可能是上次启动留下的旧值（登录态表重启后仍在库里），不再当成
 * 事实展示，先显示“还没查过/正在确认”，等一次新鲜检查再定。 */
export const PLATFORM_LOGIN_STALE_MS = 10 * 60 * 1000

export function isPlatformLoginStale(
  info: PlatformLoginInfo | undefined | null,
  nowMs = Date.now(),
): boolean {
  if (!info?.checked_at) return true
  const checkedMs = Date.parse(info.checked_at)
  if (Number.isNaN(checkedMs)) return true
  return nowMs - checkedMs > PLATFORM_LOGIN_STALE_MS
}

export type EffectiveLoginStatus = 'logged_in' | 'logged_out' | 'unknown' | 'checking'

/** 卡片上实际展示的状态：没查完（checking）时只遮住过期/没查过的行，
 * 新鲜的结论不动，避免刷新时已确认的徽标来回闪；过期行不论原来是
 * 已登录还是已掉线，一律先按“不知道”处理。 */
export function getEffectiveLoginStatus(
  info: PlatformLoginInfo | undefined | null,
  nowMs = Date.now(),
  checking = false,
): EffectiveLoginStatus {
  const stale = !info || isPlatformLoginStale(info, nowMs)
  if (checking && stale) return 'checking'
  if (stale) return 'unknown'
  if (info.status === 'logged_in' || info.status === 'logged_out') return info.status
  return 'unknown'
}

/** 合并两份登录态：每平台取 checked_at 更新的那份。
 * 手动刷新/挂载诊断刚算出的新值，在后台巡检把同样结果写库之前，
 * 不能被 5 秒一轮的 workbench 轮询用旧库值盖回去。 */
export function mergePlatformLoginStates(
  fromWorkbench: Partial<Record<string, PlatformLoginInfo>> | null | undefined,
  fromCheck: Partial<Record<string, PlatformLoginInfo>> | null | undefined,
): Partial<Record<string, PlatformLoginInfo>> {
  const merged: Partial<Record<string, PlatformLoginInfo>> = {}
  for (const platform of PLATFORM_IDS) {
    const a = fromWorkbench?.[platform]
    const b = fromCheck?.[platform]
    if (!a) {
      if (b) merged[platform] = b
      continue
    }
    if (!b) {
      merged[platform] = a
      continue
    }
    const aMs = a.checked_at ? Date.parse(a.checked_at) : NaN
    const bMs = b.checked_at ? Date.parse(b.checked_at) : NaN
    merged[platform] = (Number.isNaN(bMs) ? -1 : bMs) >= (Number.isNaN(aMs) ? -1 : aMs) ? b : a
  }
  return merged
}
