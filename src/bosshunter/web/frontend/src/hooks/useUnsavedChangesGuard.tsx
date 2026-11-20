import { createContext, useContext, useEffect, useMemo, useRef, type MutableRefObject, type ReactNode } from 'react'

/**
 * 未保存修改守卫：设置页（ConfigPage）注册，侧边栏（Sidebar）切页前检查。
 * 拦截点只放在侧边栏——它是四个页面入口的唯一切页处；浏览器刷新/关闭
 * 不在此项范围内。离开设置页时设置页会卸载，本地改动随之丢弃，所以
 * “不保存并离开”只需跳转，不用额外清理。
 */
export interface UnsavedChangesGuard {
  /** 当前有没有未保存的修改（以实际改动为准，改回原值不算）。 */
  isDirty: () => boolean
  /** 保存当前修改，返回是否保存成功；失败时调用方留在本页。 */
  save: () => Promise<boolean>
  /** 放弃未保存的改动（恢复成服务端那份）。 */
  discard: () => void
}

interface UnsavedChangesGuardContextValue {
  guardRef: MutableRefObject<UnsavedChangesGuard | null>
}

const UnsavedChangesGuardContext = createContext<UnsavedChangesGuardContextValue | null>(null)

/** 没包 Provider 时的兜底：守卫永远为空，切页不受影响（老测试也照常跑）。 */
const fallbackGuardRef: MutableRefObject<UnsavedChangesGuard | null> = { current: null }

export function UnsavedChangesProvider({ children }: { children: ReactNode }) {
  const guardRef = useRef<UnsavedChangesGuard | null>(null)
  const value = useMemo<UnsavedChangesGuardContextValue>(() => ({ guardRef }), [])
  return (
    <UnsavedChangesGuardContext.Provider value={value}>
      {children}
    </UnsavedChangesGuardContext.Provider>
  )
}

export function useUnsavedChangesGuard(): UnsavedChangesGuardContextValue {
  return useContext(UnsavedChangesGuardContext) ?? { guardRef: fallbackGuardRef }
}

/**
 * 在设置页挂载时注册守卫、卸载时注销。传进来的 guard 每次渲染都换新
 * 对象也没关系：这里只留一份稳定外壳，调用时永远读最新的那份。
 */
export function useRegisterUnsavedChangesGuard(guard: UnsavedChangesGuard | null) {
  const ctx = useContext(UnsavedChangesGuardContext)
  const latestRef = useRef(guard)
  latestRef.current = guard
  const stableGuard = useMemo<UnsavedChangesGuard>(
    () => ({
      isDirty: () => latestRef.current?.isDirty() ?? false,
      save: () => latestRef.current?.save() ?? Promise.resolve(false),
      discard: () => latestRef.current?.discard(),
    }),
    [],
  )
  useEffect(() => {
    if (!ctx) return
    ctx.guardRef.current = stableGuard
    return () => {
      if (ctx.guardRef.current === stableGuard) ctx.guardRef.current = null
    }
  }, [ctx, stableGuard])
}
