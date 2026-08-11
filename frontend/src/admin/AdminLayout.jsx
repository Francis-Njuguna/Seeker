import { Navigate, NavLink, Outlet } from 'react-router-dom'
import { useAuth } from '../auth.jsx'
import Icon from '../components/Icon.jsx'
import { isAdmin } from './api.js'

const SECTIONS = [
  { to: '/admin', end: true, label: 'Overview', icon: Icon.Inbox },
  { to: '/admin/transactions', label: 'Transactions', icon: Icon.Handshake },
  { to: '/admin/claims', label: 'Claims', icon: Icon.IdCard },
  { to: '/admin/flagged', label: 'Flagged', icon: Icon.AlertTriangle },
]

/**
 * Route-level RBAC.
 *
 * Hiding the nav item is presentation, not access control — this is the check
 * that actually holds when someone types /admin into the address bar. The
 * backend gates every /api/admin/* route on the same role independently, so a
 * forged client can still only reach data the server agrees to hand over; this
 * guard is what keeps a non-admin from seeing a broken shell of a page.
 */
export function RequireAdmin({ children }) {
  const { user, loading } = useAuth()

  if (loading) return <div className="loading">Loading...</div>
  if (!user) return <Navigate to="/login" replace />
  if (!isAdmin(user)) return <Navigate to="/" replace />

  return children
}

export default function AdminLayout() {
  const { user } = useAuth()

  return (
    <div className="admin">
      {/* Sidebar on desktop, a horizontally scrolling strip on phones — the
          same trade-off Nav makes, for the same reason: four items fit, and a
          hamburger would hide the flagged count that matters most here. */}
      <aside className="admin-sidebar">
        <div className="admin-sidebar-head">
          <span className="admin-badge">
            <Icon.ShieldCheck />
          </span>
          <div>
            <strong>Admin console</strong>
            <span className="muted">{user?.display_name}</span>
          </div>
        </div>

        <ul>
          {SECTIONS.map(({ to, end, label: text, icon: Glyph }) => (
            <li key={to}>
              <NavLink
                to={to}
                end={end}
                className={({ isActive }) => (isActive ? 'active' : '')}
              >
                <Glyph />
                {text}
              </NavLink>
            </li>
          ))}
        </ul>
      </aside>

      <div className="admin-main">
        <Outlet />
      </div>
    </div>
  )
}
