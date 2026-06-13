import { BrowserRouter, Routes, Route, Navigate } from 'react-router'
import { AppShell } from './layouts/AppShell'
import { OverviewPage } from './pages/OverviewPage'
import { RunningPage } from './pages/RunningPage'
import { TrailPage } from './pages/TrailPage'
import { ActivityListPage } from './pages/ActivityListPage'
import { ActivityDetailPage } from './pages/ActivityDetailPage'
import { ConfigPage } from './pages/ConfigPage'
import { AiPage } from './pages/AiPage'
import { CorosPage } from './pages/CorosPage'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<Navigate to="/overview" replace />} />
          <Route path="overview" element={<OverviewPage />} />
          <Route path="running" element={<RunningPage />} />
          <Route path="trail" element={<TrailPage />} />
          {/* legacy redirect */}
          <Route path="season" element={<Navigate to="/overview" replace />} />
          <Route path="activities" element={<ActivityListPage />} />
          <Route path="activities/:id" element={<ActivityDetailPage />} />
          <Route path="config" element={<ConfigPage />} />
          <Route path="ai" element={<AiPage />} />
          <Route path="coros" element={<CorosPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
