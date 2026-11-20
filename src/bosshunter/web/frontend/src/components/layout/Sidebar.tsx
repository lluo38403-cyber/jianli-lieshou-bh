import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import { Briefcase, Home, LogOut, Radar, Settings } from 'lucide-react'
import { useEffect, useState, type MouseEvent as ReactMouseEvent } from 'react'
import { requestManualUpdateCheck } from '@/lib/updateEvents'
import { useUnsavedChangesGuard } from '@/hooks/useUnsavedChangesGuard'
import { UnsavedChangesDialog } from './UnsavedChangesDialog'
import { ExitAppDialog } from './ExitAppDialog'

const navItems = [
  { to: '/', icon: Home, label: '首页' },
  { to: '/jobs', icon: Briefcase, label: '找工作' },
  { to: '/monitor', icon: Radar, label: '投递跟进' },
  { to: '/config', icon: Settings, label: '我的设置' },
]

interface SidebarProps {
  pendingReplies?: number
}

export function Sidebar({ pendingReplies: pendingRepliesProp }: SidebarProps) {
  const [pendingReplies, setPendingReplies] = useState(pendingRepliesProp ?? 0)
  // 版本号从后端取，不写死：后端 __version__ 一升，这里跟着变。
  const [currentVersion, setCurrentVersion] = useState('')
  const location = useLocation()
  const navigate = useNavigate()
  const { guardRef } = useUnsavedChangesGuard()
  /** 拦下来的目标页：有值时弹出未保存提醒，没值时不弹。 */
  const [pendingTo, setPendingTo] = useState<string | null>(null)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [savingBeforeLeave, setSavingBeforeLeave] = useState(false)
  /** “退出程序”确认弹窗（v25）：只在侧边栏底部设这一处入口。 */
  const [showExitDialog, setShowExitDialog] = useState(false)

  // 切页拦截：只有“在设置页、有未保存修改、要去别的页”时才拦；
  // 没改动、去的就是当前页、或根本不在设置页时，直接正常跳转。
  const handleNavClick = (event: ReactMouseEvent<HTMLAnchorElement>, to: string) => {
    if (to === location.pathname) return
    if (!guardRef.current?.isDirty()) return
    event.preventDefault()
    setSaveError(null)
    setPendingTo(to)
  }

  const handleSaveAndLeave = async () => {
    if (!pendingTo) return
    setSavingBeforeLeave(true)
    const ok = await guardRef.current?.save() ?? false
    setSavingBeforeLeave(false)
    if (!ok) {
      // 保存失败：留在本页，提醒还在，改动也还在，可再点一次或选别的。
      setSaveError('保存失败，还在这一页，改动还在；检查一下网络再点一次。')
      return
    }
    const to = pendingTo
    setPendingTo(null)
    setSaveError(null)
    navigate(to)
  }

  const handleDiscardAndLeave = () => {
    if (!pendingTo) return
    guardRef.current?.discard()
    const to = pendingTo
    setPendingTo(null)
    setSaveError(null)
    navigate(to)
  }

  const handleStay = () => {
    setPendingTo(null)
    setSaveError(null)
  }

  useEffect(() => {
    let alive = true
    fetch('/api/health')
      .then(r => r.json())
      .then(data => {
        if (alive && typeof data?.version === 'string' && data.version) {
          setCurrentVersion(data.version)
        }
      })
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [])

  useEffect(() => {
    if (pendingRepliesProp !== undefined) {
      setPendingReplies(pendingRepliesProp)
      return
    }

    const fetchPendingReplies = async () => {
      try {
        const res = await fetch('/api/history/unresolved-replies/count')
        const data = await res.json()
        setPendingReplies(Number(data.count) || 0)
      } catch {
        setPendingReplies(0)
      }
    }

    fetchPendingReplies()
    const interval = setInterval(fetchPendingReplies, 30000)
    return () => clearInterval(interval)
  }, [pendingRepliesProp])

  return (
    <aside className="app-sidebar flex w-16 shrink-0 flex-col md:w-60">
      <div className="flex h-16 shrink-0 items-center justify-center border-b border-white/10 md:justify-start md:px-5">
        <div className="flex items-center gap-3">
          <img src="/logo.png" alt="简历猎手" className="h-10 w-10 shrink-0 rounded-2xl object-cover shadow-lg" />
          <div className="hidden md:block">
            <div className="text-[15px] font-black tracking-tight text-white">简历猎手</div>
            <div className="text-[11px] text-white/60">自动找工作 · 本地运行</div>
            {currentVersion ? (
              <button
                type="button"
                data-testid="sidebar-version"
                onClick={requestManualUpdateCheck}
                title="点一下检查有没有新版本"
                className="mt-0.5 rounded px-1 py-0.5 text-left text-[11px] text-white/60 transition-colors hover:bg-white/10 hover:text-white"
              >
                v{currentVersion} · 检查更新
              </button>
            ) : null}
          </div>
        </div>
      </div>

      <nav className="flex-1 space-y-1 px-1.5 py-4 md:px-3">
        {navItems.map(item => (
          <NavLink
            key={item.to}
            to={item.to}
            onClick={event => handleNavClick(event, item.to)}
            data-tour={item.to === '/config' ? 'nav-config' : item.to === '/' ? 'nav-workbench' : undefined}
            className={({ isActive }) =>
              `relative flex items-center justify-center gap-3 rounded-xl px-1 py-3 text-[10px] transition-all duration-200 hover:-translate-y-px md:justify-between md:px-3 md:text-sm ${
                isActive
                  ? 'sidebar-nav-active font-black'
                  : 'sidebar-nav-idle'
              }`
            }
          >
            <span className="flex flex-col items-center gap-1 md:flex-row md:gap-3">
              <item.icon className="h-4 w-4" />
              {item.label}
            </span>
            {item.to === '/monitor' && pendingReplies > 0 && (
              <span className="pulse-ring absolute right-1 top-2 h-2.5 w-2.5 rounded-full bg-amber-400 md:static" aria-label="有待处理事项" />
            )}
          </NavLink>
        ))}
      </nav>

      <div className="space-y-3 border-t border-white/10 px-2 py-4 md:px-4">
        {pendingReplies > 0 && (
          <p className="hidden text-center text-[11px] leading-5 text-amber-300 md:block">有 {pendingReplies} 条 HR 消息等你处理</p>
        )}
        <button
          type="button"
          data-testid="sidebar-exit-app"
          onClick={() => setShowExitDialog(true)}
          title="结束所有任务和后台程序并退出"
          className="flex w-full items-center justify-center gap-3 rounded-xl px-1 py-3 text-[10px] text-white/60 transition-all duration-200 hover:-translate-y-px hover:bg-white/10 hover:text-white md:justify-start md:px-3 md:text-sm"
        >
          <LogOut className="h-4 w-4" />
          <span className="hidden md:inline">退出程序</span>
        </button>
      </div>
      {showExitDialog && (
        <ExitAppDialog onStay={() => setShowExitDialog(false)} />
      )}
      {pendingTo !== null && (
        <UnsavedChangesDialog
          saving={savingBeforeLeave}
          error={saveError}
          onSaveAndLeave={() => void handleSaveAndLeave()}
          onDiscardAndLeave={handleDiscardAndLeave}
          onStay={handleStay}
        />
      )}
    </aside>
  )
}
