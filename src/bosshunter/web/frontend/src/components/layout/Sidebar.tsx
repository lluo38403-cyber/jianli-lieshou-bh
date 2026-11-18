import { NavLink } from 'react-router-dom'
import { Briefcase, Home, Radar, Settings } from 'lucide-react'
import { useEffect, useState } from 'react'

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
          <div className="sidebar-logo flex h-10 w-10 items-center justify-center rounded-2xl text-white">
            <Briefcase className="h-5 w-5" />
          </div>
          <div className="hidden md:block">
            <div className="text-[15px] font-black tracking-tight text-white">简历猎手</div>
            <div className="text-[11px] text-white/60">自动找工作 · 本地运行</div>
          </div>
        </div>
      </div>

      <nav className="flex-1 space-y-1 px-1.5 py-4 md:px-3">
        {navItems.map(item => (
          <NavLink
            key={item.to}
            to={item.to}
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
        <div className="hidden rounded-2xl bg-white/10 px-3 py-3 text-[11px] leading-5 text-white/85 md:block">
          <span className="font-black text-white">每天最多自动投 120 个</span>
          <br />够分的岗位才会投，投满了明天继续。
        </div>
        {pendingReplies > 0 && (
          <p className="hidden text-center text-[11px] leading-5 text-amber-300 md:block">有 {pendingReplies} 条 HR 消息等你处理</p>
        )}
      </div>
    </aside>
  )
}
