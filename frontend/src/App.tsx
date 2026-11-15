import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import './App.css'
import { AuthProvider, useAuth } from './auth'
import Layout from './Layout'
import Login from './pages/Login'
import Board from './pages/Board'
import Clients from './pages/Clients'
import BranchesPage from './pages/setup/BranchesPage'
import ChannelsPage from './pages/setup/ChannelsPage'
import DiscountCodesPage from './pages/setup/DiscountCodesPage'
import AccountsPage from './pages/setup/AccountsPage'
import Accounting from './pages/Accounting'
import PettyCash from './pages/PettyCash'
import Recurring from './pages/Recurring'
import Allocations from './pages/Allocations'
import AccountingDashboard from './pages/AccountingDashboard'
import Payments from './pages/Payments'
import SchedulePage from './pages/setup/SchedulePage'
import ServicesPage from './pages/setup/ServicesPage'
import StaffPage from './pages/setup/StaffPage'
import TherapistsPage from './pages/setup/TherapistsPage'
import PaySettingsPage from './pages/setup/PaySettingsPage'
import TherapistPay from './pages/TherapistPay'

function Routed() {
  const { me, loading, canManage, isAdmin, isAccountant, canCheckPayments, canDoAccounts } = useAuth()
  if (loading) return <p className="loading">Loading…</p>
  if (!me) return <Login />
  return (
    <Routes>
      <Route element={<Layout />}>
        {isAccountant ? (
          <Route index element={<Navigate to="/payments" replace />} />
        ) : (
          <>
            <Route index element={<Board />} />
            <Route path="clients" element={<Clients />} />
            <Route path="clients/:clientId" element={<Clients />} />
            <Route path="petty-cash" element={<PettyCash />} />
          </>
        )}
        {canCheckPayments && <Route path="payments" element={<Payments />} />}
        {canCheckPayments && <Route path="pay" element={<TherapistPay />} />}
        {canDoAccounts && <Route path="accounting" element={<Accounting />} />}
        {canDoAccounts && <Route path="accounting/recurring" element={<Recurring />} />}
        {canDoAccounts && <Route path="accounting/allocations" element={<Allocations />} />}
        {canDoAccounts && <Route path="accounting/dashboard" element={<AccountingDashboard />} />}
        {canManage && (
          <>
            <Route path="setup/branches" element={<BranchesPage />} />
            <Route path="setup/services" element={<ServicesPage />} />
            <Route path="setup/therapists" element={<TherapistsPage />} />
            <Route path="setup/schedule" element={<SchedulePage />} />
            <Route path="setup/channels" element={<ChannelsPage />} />
            <Route path="setup/discount-codes" element={<DiscountCodesPage />} />
            <Route path="setup/pay" element={<PaySettingsPage />} />
          </>
        )}
        {isAdmin && <Route path="setup/staff" element={<StaffPage />} />}
        {isAdmin && <Route path="setup/accounts" element={<AccountsPage />} />}
        {isAdmin && <Route path="setup/payment-methods" element={<Navigate to="/setup/accounts" replace />} />}
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
