import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom'
import { Sidebar } from './components/layout/Sidebar'
import { Header } from './components/layout/Header'
import { ErrorBoundary } from './components/ErrorBoundary'
import { UpdateProvider } from './hooks/useUpdate'
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
  return (
    <UpdateProvider>
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
