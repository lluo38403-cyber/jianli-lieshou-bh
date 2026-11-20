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

/**
 * 各平台沟通/聊天页默认地址（v27：原来只有一个 monitor.chat_url，
 * 默认是 BOSS 的链接；现在按平台分开配）。
 * 核实情况：
 * - BOSS：https://www.zhipin.com/web/geek/chat 是查 HR 回复一直在用的
 *   沟通页直链，属实。
 * - 智联 / 前程无忧 / 猎聘：沿用后端投递流程的各平台消息中心地址
 *  （platform_delivery.DEFAULT_INBOX_URLS）：智联、前程无忧是个人
 *   中心首页，猎聘是消息中心；不是专用聊天页直链，用户可在设置里
 *   改成自己核实过的地址。以后核实到直链再换。
 */
export const MONITOR_CHAT_URL_DEFAULTS: Record<PlatformId, string> = {
  boss: 'https://www.zhipin.com/web/geek/chat',
  zhilian: 'https://i.zhaopin.com/',
  '51job': 'https://i.51job.com/',
  liepin: 'https://www.liepin.com/msg/',
}

export interface OpenLoginResult {
  ok: boolean
  /** 后端是否刚拉起了工作浏览器（true = 这次是现拉的）。 */
  browserStarted?: boolean
  /** 是否复用了已打开的标签页（true = 切过去，没新开）。 */
  reusedTab?: boolean
  /** 给用户的状态/失败说明。 */
  message?: string
}

export async function openPlatformLogin(platform: PlatformId): Promise<OpenLoginResult> {
  const url = PLATFORM_LOGIN_URLS[platform]
  try {
    // 只走后端：在运行组件连着的浏览器（程序拉起的专用配置目录）里开
    // 登录页，登录态才能被检测到。v27 起不再回退到 window.open：回退
    // 会把登录页开在面板自己的浏览器里，登录态落在另一个配置目录，
    // 程序永远检测不到——就是之前“浏览器没连上，登录查不出来”的根因。
    // 后端没接通就把失败原因交回给界面，由界面显示并让用户再点一次。
    const res = await fetch('/api/browser/open', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    })
    const data = await res.json().catch(() => ({})) as { ok?: boolean; browser_started?: boolean; reused_tab?: boolean; message?: string; error?: string }
    if (res.ok && data.ok) {
      return {
        ok: true,
        browserStarted: Boolean(data.browser_started),
        reusedTab: Boolean(data.reused_tab),
        message: data.message || '已在工作浏览器打开。',
      }
    }
    return { ok: false, message: data.error || '浏览器没连上，再点一次试试。' }
  } catch {
    return { ok: false, message: '没连上程序的浏览器，请确认简历猎手还在运行，再点一次。' }
  }
}
