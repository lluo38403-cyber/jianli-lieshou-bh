import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom'
import { useEffect } from 'react'
import { Sidebar } from './components/layout/Sidebar'
import { Header } from './components/layout/Header'
import { ErrorBoundary } from './components/ErrorBoundary'
import { UpdateProvider } from './hooks/useUpdate'
import { UnsavedChangesProvider } from './hooks/useUnsavedChangesGuard'
import { installCloseConfirm } from './lib/closeConfirm'
import DashboardPage from './pages/DashboardPage'
import ConfigPage from './pages/ConfigPage'
import AdminPage from './pages/AdminPage'
import { OnboardingTour } from './components/onboarding/OnboardingTour'

function JobsPage() {
  return <DashboardPage view="jobs" />
}

function MonitorPage() {
  return <DashboardPage view="monitor" />
}

function MainLayout() {
  const location = useLocation()
  // v25：点 --app 窗口右上角 X 时先经 beforeunload 确认。现代 Chrome
  // 的 beforeunload 只能弹浏览器自带的通用离开确认框（放不进自定义
  // 文案，见 lib/closeConfirm）；点“离开”后后端关窗监视线程走统一
  // 清理再退出进程，点“留下”则什么都不做。代码关窗（更新换版、
  // “退出程序”点“是”）先置位跳过确认，见 skipCloseConfirm。
  useEffect(() => installCloseConfirm(), [])
  return (
    <UpdateProvider>
    <UnsavedChangesProvider>
    <div className="flex h-screen overflow-hidden bg-background text-foreground">
      <Sidebar />
      <div className="min-w-0 flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="min-w-0 flex-1 overflow-y-auto p-3 md:p-6">
          <div key={location.pathname} className="animate-page-in">
            <ErrorBoundary>
              <Routes>
                <Route path="/" element={<DashboardPage />} />
                <Route path="/jobs" element={<JobsPage />} />
                <Route path="/monitor" element={<MonitorPage />} />
                <Route path="/config" element={<ConfigPage />} />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
            </ErrorBoundary>
          </div>
        </main>
      </div>
      <OnboardingTour />
    </div>
    </UnsavedChangesProvider>
    </UpdateProvider>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        {/* 管理员面板：独立入口，不在用户界面里放链接，直接开地址进。 */}
        <Route path="/admin" element={<AdminPage />} />
        <Route path="*" element={<MainLayout />} />
      </Routes>
    </BrowserRouter>
  )
}
