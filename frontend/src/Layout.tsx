import { NavLink, Outlet } from 'react-router-dom'
import { useAuth } from './auth'

const SETUP_LINKS = [
  { to: '/setup/branches', label: 'Branches and rooms' },
  { to: '/setup/services', label: 'Services and prices' },
  { to: '/setup/therapists', label: 'Therapists' },
  { to: '/setup/schedule', label: 'Shifts and time off' },
  { to: '/setup/channels', label: 'Booking channels' },
]

export default function Layout() {
  const { me, branch, setBranchId, logout, isAdmin } = useAuth()
  if (!me) return null
  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <strong>{me.tenant_name}</strong>
          <span className="muted">Front desk</span>
        </div>
        {me.branches.length > 0 && (
          <div className="branch-picker">
            <label className="muted" htmlFor="branch-picker">
              Branch
            </label>
            <select id="branch-picker" value={branch?.id ?? ''} onChange={(e) => setBranchId(e.target.value)}>
              {me.branches.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.name}
                </option>
              ))}
            </select>
          </div>
        )}
        <div className="who">
          <span>
            {me.name} <span className="muted">· {me.role.replace('_', ' ')}</span>
          </span>
          <button onClick={logout}>Sign out</button>
        </div>
      </header>
      <div className="body">
        <nav className="sidenav" aria-label="Sections">
          <NavLink to="/" end>
            Today
          </NavLink>
          <p className="nav-group">Setup</p>
          {SETUP_LINKS.map((l) => (
            <NavLink key={l.to} to={l.to}>
              {l.label}
            </NavLink>
          ))}
          {isAdmin && <NavLink to="/setup/staff">Staff accounts</NavLink>}
        </nav>
        <main className="content">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
