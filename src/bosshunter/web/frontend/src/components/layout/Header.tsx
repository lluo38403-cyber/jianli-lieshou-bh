import { useLocation } from 'react-router-dom'
import { ThemeToggle } from './ThemeToggle'

const pageTitles: Record<string, { title: string; subtitle: string }> = {
  '/': { title: '首页', subtitle: '今天的找工作进度，一眼看清' },
  '/jobs': { title: '找工作', subtitle: '所有找到的岗位都在这儿' },
  '/monitor': { title: '投递跟进', subtitle: 'HR 回了消息，第一时间处理' },
  '/config': { title: '我的设置', subtitle: '想找什么工作，在这儿一次说清' },
}

export function Header() {
  const location = useLocation()
  const page = pageTitles[location.pathname] || { title: '简历猎手', subtitle: '自动找工作' }

  return (
    <header className="flex h-16 items-center justify-between border-b border-card-border bg-surface px-6">
      <div className="animate-fade-in" key={location.pathname}>
        <h1 className="text-lg font-black leading-tight text-foreground">{page.title}</h1>
        <p className="text-[11px] leading-tight text-muted">{page.subtitle}</p>
      </div>
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2 text-xs text-muted">
          <span className="pulse-dot inline-block h-2 w-2 rounded-full bg-success" />
          <span>正在本地运行</span>
        </div>
        <ThemeToggle />
      </div>
    </header>
  )
}
