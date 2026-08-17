import { useEffect, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
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

const ownerLinks = [
  { to: '/owner', label: 'Home', icon: Icon.Inbox },
  { to: '/search', label: 'Search', icon: Icon.Search },
  { to: '/alerts', label: 'Alerts', icon: Icon.Bell },
  { to: '/matches?workspace=owner', label: 'Claims', icon: Icon.Handshake },
]

const finderLinks = [
  { to: '/finder', label: 'Home', icon: Icon.Inbox },
  { to: '/post-found', label: 'Post document', icon: Icon.IdCard },
  { to: '/matches?workspace=finder', label: 'Handovers', icon: Icon.Handshake },
]

const FINDER_NOTIFICATION_KINDS = new Set([
  'claim_requested',
  'claim_paid',
  'police_transfer_due',
])

export default function Nav() {
  const { user, token, logout, workspace, setWorkspace } = useAuth()
  const [unread, setUnread] = useState({ owner: 0, finder: 0 })
  const location = useLocation()
  const navigate = useNavigate()

  const links = workspace === 'finder' ? finderLinks : ownerLinks

  // Presentation only. RequireAdmin on the route and require_admin on every
  // /api/admin/* endpoint are what actually enforce this.
  const visibleLinks = isAdmin(user)
    ? [...links, { to: '/admin', label: 'Admin', icon: Icon.ShieldCheck }]
    : links

  useEffect(() => {
    if (!token) return
    fetch('/api/notifications?unread_only=true', { headers: authHeaders(token) })
      .then((r) => (r.ok ? r.json() : []))
      .then((rows) => setUnread(rows.reduce((counts, item) => {
        counts[FINDER_NOTIFICATION_KINDS.has(item.kind) ? 'finder' : 'owner'] += 1
        return counts
      }, { owner: 0, finder: 0 })))
      .catch(() => {})
  }, [token, location.pathname])

  return (
    <nav className="nav">
      <Brand as={Link} to="/" />

      <div className="workspace-switch" role="group" aria-label="Workspace">
        <button type="button" className={workspace === 'owner' ? 'is-on' : ''} onClick={() => { setWorkspace('owner'); navigate('/owner') }}>Owner{unread.owner > 0 && <span className="badge">{unread.owner}</span>}</button>
        <button type="button" className={workspace === 'finder' ? 'is-on' : ''} onClick={() => { setWorkspace('finder'); navigate('/finder') }}>Finder{unread.finder > 0 && <span className="badge">{unread.finder}</span>}</button>
      </div>

      {/* Scrolls horizontally rather than collapsing to a hamburger: five
          items (six for admins) fit on a phone with a nudge, and a menu would
          hide the unread badge, which is the one thing worth surfacing. */}
      <ul>
        {visibleLinks.map(({ to, label, icon: Glyph }) => {
          const active =
            to === '/admin'
              ? location.pathname.startsWith('/admin')
              : location.pathname === to.split('?')[0]
          return (
            <li key={to}>
              <Link
                to={to}
                className={active ? 'active' : ''}
                aria-current={active ? 'page' : undefined}
              >
                <Glyph />
                {label}
                {to.startsWith('/matches') && unread[workspace] > 0 && (
                  <span className="badge" aria-label={`${unread[workspace]} unread`}>
                    {unread[workspace]}
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
