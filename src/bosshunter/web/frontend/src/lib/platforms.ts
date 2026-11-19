export const PLATFORM_LABELS: Record<string, string> = {
  boss: 'BOSS 直聘',
  zhilian: '智联招聘',
  '51job': '前程无忧',
  liepin: '猎聘',
}

export const PLATFORM_SHORT_LABELS: Record<string, string> = {
  boss: 'BOSS',
  zhilian: '智联',
  '51job': '51job',
  liepin: '猎聘',
}

export type PlatformId = 'boss' | 'zhilian' | '51job' | 'liepin'

export const PLATFORM_IDS: PlatformId[] = ['boss', 'zhilian', '51job', 'liepin']

/**
 * 各平台登录入口（新窗口打开）。
 * 核实情况（2026-11-19）：
 * - BOSS：https://www.zhipin.com/web/user/login 是任务给定的登录页，
 *   后端 boss.py 的登录页判断也用 /web/user/login 这一路径，属实。
 * - 智联 / 前程无忧 / 猎聘：公开搜索和官网首页抓取都没能确认到一个
 *   专用的登录页直链，项目后端里也没有这类 URL（只有官网首页和
 *   登录后的个人中心页）。按要求不凭空编 URL，这里先用已验证的
 *   官网首页作入口，点开后在首页点“登录”即可；以后核实到专用
 *   登录页再把对应项换成直链。
 */
export const PLATFORM_LOGIN_URLS: Record<PlatformId, string> = {
  boss: 'https://www.zhipin.com/web/user/login',
  zhilian: 'https://www.zhaopin.com/',
  '51job': 'https://www.51job.com/',
  liepin: 'https://www.liepin.com/',
}

/** 值为 true 表示该项是官网首页入口（不是专用登录页直链）。 */
export const PLATFORM_LOGIN_URL_IS_HOMEPAGE: Record<PlatformId, boolean> = {
  boss: false,
  zhilian: true,
  '51job': true,
  liepin: true,
}

export function openPlatformLogin(platform: PlatformId) {
  window.open(PLATFORM_LOGIN_URLS[platform], '_blank', 'noopener,noreferrer')
}
