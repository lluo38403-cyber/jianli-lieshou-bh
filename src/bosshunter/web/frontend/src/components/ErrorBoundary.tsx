import { Component, type ReactNode } from 'react'

/**
 * 顶层兜底：某个页面渲染抛错时，整棵树不会全白——只挡这一个页面，
 * 侧边栏和标题还在，点“重新打开”就能恢复；其他页面不受影响。
 * 以前没有这个边界，一处字段取错整个应用就只剩白屏。
 */
export class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  componentDidCatch(error: Error) {
    console.error('页面渲染出错：', error)
  }

  render() {
    if (this.state.error) {
      return (
        <div className="flex h-full items-center justify-center p-4">
          <div className="max-w-md rounded-2xl border border-card-border bg-card p-6 text-center">
            <p className="text-base font-black text-foreground">这个页面打不开了</p>
            <p className="mt-2 text-sm leading-6 text-muted">
              不用担心，设置和数据都没动，重新打开一般就好了；其他页面不受影响。
            </p>
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="mt-4 rounded-xl bg-primary px-4 py-2 text-sm font-bold text-white hover:opacity-90"
            >
              重新打开
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}
