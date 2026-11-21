/**
 * 关窗确认（v25，v33 核实定稿）：点 --app 窗口右上角 X 时先确认。
 *
 * 英文 "Leave app?" 弹窗的来源（v33 已核实）：X 是 Chrome 的窗口
 * 按钮，网页改不了它、也拦不住那次点击；网页唯一能挂的钩子是
 * beforeunload，而 Chrome 51 起就移除了自定义文案——returnValue 里
 * 写什么都会被忽略，只弹浏览器自带的通用离开确认框，文案由浏览器
 * 写、跟着 Chrome 界面语言走。所以“点 X 弹出与‘退出程序’相同的
 * 自定义弹窗”在网页层做不到，任何在 beforeunload 里弹 React 弹窗
 * 的写法都只会排在浏览器框后面（还多一次确认），不是替代。
 *
 * v33 的统一方式（退而求其次，但已是网页层能做到的最接近）：
 * - 语言统一：--app 窗口与工作浏览器共用一个 Chrome 实例（v27），
 *   实例由我们用专用配置目录拉起，启动参数带 --lang=zh-CN（见
 *   app_window.launch_app_window 与 runtime.launch_debuggable_browser，
 *   两处都要加：先拉起实例的那次决定语言）。Chromium 取界面语言的
 *   顺序是命令行 --lang > 配置偏好 > 系统语言，所以通用确认框从
 *   英文 "Leave app?" 变成中文，不再出现英文；只影响我们自己的
 *   专用配置目录实例，不动用户日常用的 Chrome。
 * - 语义统一：X 路径是 beforeunload 拦住关闭 → 浏览器通用确认框 →
 *   点“离开”等同于“退出程序”点“是”（后端关窗监视线程走与 /api/exit
 *   同一套退出清理 server._perform_exit_cleanup 再退出进程），
 *   点“留下”等同于“否”（什么都不做、程序保持运行）。
 * - 自定义文案（lucky 原话标题）仍只放在面板里的“退出程序”入口弹窗：
 *   “是否结束该应用程序，这可能会导致当前进行中的任务结束”，
 *   是 → 调 POST /api/exit 全停并退出，否 → 关弹窗、什么都不做。
 *
 * 绕过标志 window.__jlSkipCloseConfirm：代码关窗（更新换版成功后
 * useUpdate 的 window.close()、“退出程序”点“是”后的 window.close()）
 * 必须先置位，否则 beforeunload 会拦住换版流程、卡住等用户点。
 * 标志只在当前页面会话内有效，不持久化。
 *
 * 已知限制（浏览器行为，改不了）：现代 Chrome 只在用户与页面有过
 * 交互后才弹这个框，刚打开面板、什么都没点就关窗时可能直接关掉、
 * 不弹确认——那时关窗监视线程仍会走统一清理再退出，只是多少了一次
 * 确认。更新重开、程序自己重启时不弹（代码关窗先置位跳过，见上）。
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
