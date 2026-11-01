import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import './App.css'
import { AuthProvider, useAuth } from './auth'
import Layout from './Layout'
import Login from './pages/Login'
import Board from './pages/Board'
import Clients from './pages/Clients'
import BranchesPage from './pages/setup/BranchesPage'
import ChannelsPage from './pages/setup/ChannelsPage'
import SchedulePage from './pages/setup/SchedulePage'
import ServicesPage from './pages/setup/ServicesPage'
import StaffPage from './pages/setup/StaffPage'
import TherapistsPage from './pages/setup/TherapistsPage'

function Routed() {
  const { me, loading, canManage, isAdmin } = useAuth()
  if (loading) return <p className="loading">Loading…</p>
  if (!me) return <Login />
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Board />} />
        <Route path="clients" element={<Clients />} />
        <Route path="clients/:clientId" element={<Clients />} />
        {canManage && (
          <>
            <Route path="setup/branches" element={<BranchesPage />} />
            <Route path="setup/services" element={<ServicesPage />} />
            <Route path="setup/therapists" element={<TherapistsPage />} />
            <Route path="setup/schedule" element={<SchedulePage />} />
            <Route path="setup/channels" element={<ChannelsPage />} />
          </>
        )}
        {isAdmin && <Route path="setup/staff" element={<StaffPage />} />}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routed />
      </BrowserRouter>
    </AuthProvider>
  )
}
