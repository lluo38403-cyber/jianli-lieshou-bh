import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom'
import { Sidebar } from './components/layout/Sidebar'
import { Header } from './components/layout/Header'
import { ErrorBoundary } from './components/ErrorBoundary'
import { UpdateProvider } from './hooks/useUpdate'
import { UnsavedChangesProvider } from './hooks/useUnsavedChangesGuard'
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
  // v34：点 --app 窗口右上角 X 不再弹任何确认框——前端不注册
  // beforeunload，X 直接关窗；关闭清理全部走后端关窗监视线程
  // （watch_app_window_close → _perform_exit_cleanup：先停进行中
  // 任务，再停 runtime / 我们拉的 Chrome，最后退出进程，与
  // “退出程序”点“是”同一套）。代码关窗（更新换版、“退出程序”
  // 点“是”后的 window.close()）也不需要再绕过什么，直接关就行。
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
