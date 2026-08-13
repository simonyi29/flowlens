import { lazy, Suspense, useState } from 'react'
import { HashRouter, Navigate, Route, Routes } from 'react-router-dom'
import { Toaster } from 'sonner'
import { EnvironmentCheck, isEnvChecked } from '@/components/env/EnvironmentCheck'
import { LicenseDisclaimer, isLicenseAccepted } from '@/components/license/LicenseDisclaimer'
import { AdminAppShell, UserAppShell } from '@/components/shell/AppShell'
import { PageLoader } from '@/components/product/Primitives'

const DashboardPage = lazy(() => import('@/pages/DashboardPage'))
const ConnectionPage = lazy(() => import('@/pages/ConnectionPage'))
const NewCrawlPage = lazy(() => import('@/pages/NewCrawlPage'))
const TasksPage = lazy(() => import('@/pages/TasksPage'))
const TaskDetailPage = lazy(() => import('@/pages/TaskDetailPage'))
const LibraryPage = lazy(() => import('@/pages/LibraryPage'))
const ContentDetailPage = lazy(() => import('@/pages/ContentDetailPage'))
const MediaPage = lazy(() => import('@/pages/MediaPage'))
const SchedulesPage = lazy(() => import('@/pages/SchedulesPage'))
const SettingsPage = lazy(() => import('@/pages/SettingsPage'))
const AdminPage = lazy(() => import('@/pages/AdminPage'))

function App() {
  const [licenseAccepted, setLicenseAccepted] = useState(() => isLicenseAccepted())
  const [envChecked, setEnvChecked] = useState(() => isEnvChecked())
  const [showDisclaimer, setShowDisclaimer] = useState(false)

  return (
    <HashRouter>
      {(!licenseAccepted || showDisclaimer) && (
        <LicenseDisclaimer onAccept={() => { setLicenseAccepted(true); setShowDisclaimer(false) }} />
      )}
      {licenseAccepted && !showDisclaimer && !envChecked && (
        <EnvironmentCheck onCheckComplete={() => setEnvChecked(true)} />
      )}
      <Suspense fallback={<PageLoader />}>
        <Routes>
          <Route element={<UserAppShell onShowDisclaimer={() => setShowDisclaimer(true)} />}>
            <Route index element={<DashboardPage />} />
            <Route path="connect" element={<ConnectionPage />} />
            <Route path="crawl/new" element={<NewCrawlPage />} />
            <Route path="tasks" element={<TasksPage />} />
            <Route path="tasks/:runId" element={<TaskDetailPage />} />
            <Route path="library" element={<LibraryPage />} />
            <Route path="library/awemes/:awemeId" element={<ContentDetailPage />} />
            <Route path="media" element={<MediaPage />} />
            <Route path="schedules" element={<SchedulesPage />} />
            <Route path="settings" element={<SettingsPage />} />
          </Route>
          <Route path="admin" element={<AdminAppShell />}>
            <Route index element={<Navigate to="workers" replace />} />
            <Route path="workers" element={<AdminPage section="workers" />} />
            <Route path="verifications" element={<AdminPage section="verifications" />} />
            <Route path="queue" element={<AdminPage section="queue" />} />
            <Route path="health" element={<AdminPage section="health" />} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
      <Toaster position="top-right" richColors closeButton />
    </HashRouter>
  )
}

export default App
