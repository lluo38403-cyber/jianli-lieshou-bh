/**
 * 关窗确认（v25）：点 --app 窗口右上角 X 时先确认。
 *
 * 如实说明：--app 窗口的 X 是 Chrome 的窗口按钮，网页改不了它；
 * 现代 Chrome 的 beforeunload 只能弹出浏览器自带的通用离开确认框
 * （文案是浏览器写的，放不进自定义文案）。所以 X 路径是：
 * beforeunload 拦住关闭 → 浏览器通用确认框 → 点“离开”等同于“是”
 * （后端关窗监视线程走统一清理再退出进程），点“留下”等同于“否”
 * （什么都不做、程序保持运行）。
 *
 * 自定义文案（lucky 原话）只放在面板里的“退出程序”入口弹窗：
 * “是否结束该应用程序，这可能会导致当前进行中的任务结束”，
 * 是 → 调 POST /api/exit 全停并退出，否 → 关弹窗、什么都不做。
 *
 * 绕过标志 window.__jlSkipCloseConfirm：代码关窗（更新换版成功后
 * useUpdate 的 window.close()、“退出程序”点“是”后的 window.close()）
 * 必须先置位，否则 beforeunload 会拦住换版流程、卡住等用户点。
 * 标志只在当前页面会话内有效，不持久化。
 */

declare global {
  interface Window {
    __jlSkipCloseConfirm?: boolean
  }
}

/** 代码关窗前调：跳过这一次 beforeunload 确认。 */
export function skipCloseConfirm() {
  window.__jlSkipCloseConfirm = true
}

export function isCloseConfirmSkipped() {
  return window.__jlSkipCloseConfirm === true
}

/**
 * 装 beforeunload 拦截：返回取消注册的函数。
 * 标志位已置（代码关窗）时不拦；其余情况 preventDefault + returnValue
 * 让浏览器弹出通用离开确认框。注意：现代 Chrome 只在用户与页面有过
 * 交互后才弹这个框，刚打开面板、什么都没点就关窗时可能直接关掉、
 * 不弹确认——这是浏览器行为，改不了。
 */
export function installCloseConfirm() {
  const handler = (event: BeforeUnloadEvent) => {
    if (isCloseConfirmSkipped()) return
    event.preventDefault()
    // 必须给 returnValue 赋值，Chrome 才会弹通用确认框；
    // 这里写的字符串不会显示出来，框里是浏览器自己的通用文案。
    event.returnValue = ''
  }
  window.addEventListener('beforeunload', handler)
  return () => window.removeEventListener('beforeunload', handler)
}
