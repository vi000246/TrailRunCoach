import { BrowserRouter, Routes, Route, Navigate } from 'react-router'
import { AppShell } from './layouts/AppShell'
import { OverviewPage } from './pages/OverviewPage'
import { RunningPage } from './pages/RunningPage'
import { TrailPage } from './pages/TrailPage'
import { AchievementsPage } from './pages/AchievementsPage'
import { ActivityListPage } from './pages/ActivityListPage'
import { ActivityDetailPage } from './pages/ActivityDetailPage'
import { ConfigPage } from './pages/ConfigPage'
import { AiPage } from './pages/AiPage'
import { SyncPage } from './pages/SyncPage'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<Navigate to="/overview" replace />} />
          <Route path="overview" element={<OverviewPage />} />
          <Route path="running" element={<RunningPage />} />
          <Route path="trail" element={<TrailPage />} />
          <Route path="achievements" element={<AchievementsPage />} />
          {/* legacy redirect */}
          <Route path="season" element={<Navigate to="/overview" replace />} />
          <Route path="activities" element={<ActivityListPage />} />
          <Route path="activities/:id" element={<ActivityDetailPage />} />
          <Route path="config" element={<ConfigPage />} />
          <Route path="ai" element={<AiPage />} />
          <Route path="sync" element={<SyncPage />} />
          {/* legacy redirect */}
          <Route path="coros" element={<Navigate to="/sync" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
