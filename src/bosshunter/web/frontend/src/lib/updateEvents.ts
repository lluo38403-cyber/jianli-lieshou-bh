/** 侧边栏“点版本号检查更新”用的站内事件：侧边栏没有自己的更新实例，
 * 点一下就发这个事件，当前打开的页面（首页或设置页）收到后用自己那份
 * useUpdate 做一次手动检查并弹出新版弹窗。 */
export const CHECK_UPDATE_EVENT = 'jianli-lieshou:check-update'

export function requestManualUpdateCheck() {
  window.dispatchEvent(new Event(CHECK_UPDATE_EVENT))
}
