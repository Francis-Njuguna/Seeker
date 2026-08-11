import { useEffect, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { authHeaders, useAuth } from '../auth.jsx'
import { isAdmin } from '../admin/api.js'
import Brand from './Brand.jsx'
import Icon from './Icon.jsx'

/** First letters of the first two words, e.g. "Amina Otieno" -> "AO". */
function initials(name) {
  return (name || '?')
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0])
    .join('')
    .toUpperCase()
}

const links = [
  { to: '/', label: 'Home', icon: Icon.Inbox },
  { to: '/post-found', label: 'Post a found ID', icon: Icon.IdCard },
  { to: '/search', label: 'Search', icon: Icon.Search },
  { to: '/alerts', label: 'My alerts', icon: Icon.Bell },
  { to: '/matches', label: 'Matches', icon: Icon.Handshake },
]

export default function Nav() {
  const { user, token, logout } = useAuth()
  const [unread, setUnread] = useState(0)
  const location = useLocation()

  // Presentation only. RequireAdmin on the route and require_admin on every
  // /api/admin/* endpoint are what actually enforce this.
  const visibleLinks = isAdmin(user)
    ? [...links, { to: '/admin', label: 'Admin', icon: Icon.ShieldCheck }]
    : links

  useEffect(() => {
    if (!token) return
    fetch('/api/notifications/unread-count', { headers: authHeaders(token) })
      .then((r) => (r.ok ? r.json() : { unread: 0 }))
      .then((d) => setUnread(d.unread))
      .catch(() => {})
  }, [token, location.pathname])

  return (
    <nav className="nav">
      <Brand as={Link} to="/" />

      {/* Scrolls horizontally rather than collapsing to a hamburger: five
          items (six for admins) fit on a phone with a nudge, and a menu would
          hide the unread badge, which is the one thing worth surfacing. */}
      <ul>
        {visibleLinks.map(({ to, label, icon: Glyph }) => {
          const active =
            to === '/admin'
              ? location.pathname.startsWith('/admin')
              : location.pathname === to
          return (
            <li key={to}>
              <Link
                to={to}
                className={active ? 'active' : ''}
                aria-current={active ? 'page' : undefined}
              >
                <Glyph />
                {label}
                {to === '/matches' && unread > 0 && (
                  <span className="badge" aria-label={`${unread} unread`}>
                    {unread}
                  </span>
                )}
              </Link>
            </li>
          )
        })}
      </ul>

      <div className="nav-user">
        <span className="avatar" title={user?.display_name}>
          {initials(user?.display_name)}
        </span>
        <span className="nav-user-name">{user?.display_name}</span>
        <button type="button" onClick={logout} className="link-button">
          Sign out
        </button>
      </div>
    </nav>
  )
}
