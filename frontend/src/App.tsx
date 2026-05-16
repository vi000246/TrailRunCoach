import { BrowserRouter, Routes, Route, Navigate } from 'react-router'
import { AppShell } from './layouts/AppShell'
import { SeasonPage } from './pages/SeasonPage'
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
          <Route index element={<Navigate to="/season" replace />} />
          <Route path="season" element={<SeasonPage />} />
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
