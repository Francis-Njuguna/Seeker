import { useEffect, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { authHeaders, useAuth } from '../auth.jsx'

export default function Nav() {
  const { user, token, logout } = useAuth()
  const [unread, setUnread] = useState(0)
  const location = useLocation()

  useEffect(() => {
    if (!token) return
    fetch('/api/notifications/unread-count', { headers: authHeaders(token) })
      .then((r) => (r.ok ? r.json() : { unread: 0 }))
      .then((d) => setUnread(d.unread))
      .catch(() => {})
  }, [token, location.pathname])

  const links = [
    { to: '/', label: 'Home' },
    { to: '/post-found', label: 'Post a found ID' },
    { to: '/search', label: 'Search' },
    { to: '/alerts', label: 'My alerts' },
    { to: '/matches', label: 'Matches' },
  ]

  return (
    <nav className="nav">
      <span className="brand">Seeker</span>
      <ul>
        {links.map((l) => (
          <li key={l.to}>
            <Link
              to={l.to}
              className={location.pathname === l.to ? 'active' : ''}
            >
              {l.label}
              {l.to === '/matches' && unread > 0 && (
                <span className="badge" aria-label={`${unread} unread`}>
                  {unread}
                </span>
              )}
            </Link>
          </li>
        ))}
      </ul>
      <div className="nav-user">
        <span>{user?.display_name}</span>
        <button type="button" onClick={logout} className="link-button">
          Sign out
        </button>
      </div>
    </nav>
  )
}
