import { createPortal } from 'react-dom'
import type { ReactNode } from 'react'

/**
 * 所有 fixed 弹窗的统一挂载点：直接挂到 document.body。
 *
 * 根因：App.tsx 里页面包了一层 `.animate-page-in`，入场动画用
 * `animation: jl-fade-up … both`，终态 `transform: translateY(0)` 会一直留着。
 * 只要祖先带非 none 的 transform，它就成了 fixed 后代的定位参照——弹窗的
 * `fixed inset-0` / `fixed bottom-4` 不再相对视口，而是相对整页容器：
 * 居中弹窗被顶到整页中部、右下角更新卡片沉到页底，滚到底才看得见。
 * （联系客服弹窗之前就是这么修的，见 AuthStatusBar 的注释。）
 *
 * 走 Portal 后弹窗彻底脱离动画祖先，fixed 重新相对视口，滚动到哪都居中可见。
 * 只改挂载位置，不动弹窗自身的开关、触发与业务逻辑。
 */
export function ModalPortal({ children }: { children: ReactNode }) {
  return createPortal(children, document.body)
}
