import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { authHeaders, useAuth } from '../auth.jsx'
import Icon from '../components/Icon.jsx'

const ACTIONS = [
  {
    to: '/post-found',
    icon: Icon.IdCard,
    image: '/img/card-found.webp',
    alt: 'Someone taking a card out of a wallet',
    title: 'Post a found ID',
    body: "Found someone's document? Post it and we will look for the owner.",
  },
  {
    to: '/search',
    icon: Icon.Search,
    image: '/img/card-search.webp',
    alt: 'Hands holding an open, empty wallet',
    title: 'Search for yours',
    body: 'Lost something? Search what has already been handed in by ID number or name.',
  },
  {
    to: '/alerts',
    icon: Icon.Bell,
    image: '/img/card-alert.webp',
    alt: 'A hand lifting keys out of a wallet',
    title: 'Register an alert',
    body: 'Nothing yet? Leave the details and we will tell you the moment it turns up.',
  },
]

function greeting(hour) {
  if (hour < 12) return 'Good morning'
  if (hour < 17) return 'Good afternoon'
  return 'Good evening'
}

export default function DashboardPage() {
  const { token, user, setWorkspace } = useAuth()
  const [notifications, setNotifications] = useState([])
  const [stats, setStats] = useState({ found: 0, alerts: 0, matches: 0 })

  useEffect(() => {
    setWorkspace('owner')
    if (!token) return
    const headers = authHeaders(token)
    Promise.all([
      fetch('/api/notifications?unread_only=true', { headers }).then((r) =>
        r.ok ? r.json() : [],
      ),
      fetch('/api/found-ids/mine', { headers }).then((r) => (r.ok ? r.json() : [])),
      fetch('/api/lost-alerts', { headers }).then((r) => (r.ok ? r.json() : [])),
      fetch('/api/matches/mine', { headers }).then((r) => (r.ok ? r.json() : [])),
    ]).then(([notif, found, alerts, matches]) => {
      setNotifications(notif.slice(0, 5))
      setStats({ found: found.length, alerts: alerts.length, matches: matches.length })
    })
  }, [token])

  // Only the given name — "Good morning, Amina Otieno" reads like a form letter.
  const firstName = (user?.display_name || '').trim().split(/\s+/)[0]
  const tiles = [
    { n: stats.found, label: 'IDs you posted' },
    { n: stats.alerts, label: 'Active alerts' },
    { n: stats.matches, label: 'Matches' },
  ]

  return (
    <div className="page">
      <header className="page-head">
        <p className="eyebrow">{greeting(new Date().getHours())}</p>
        <h1>{firstName ? `${firstName}.` : 'Welcome.'}</h1>
        <p className="subtitle">
          Post something you found, look for something you lost, or leave an alert
          and let us do the watching.
        </p>
      </header>

      <div className="action-grid">
        {ACTIONS.map(({ to, icon: Glyph, image, alt, title, body }) => (
          <Link to={to} className="action-card" key={to}>
            <div className="action-card-photo">
              <img src={image} alt={alt} loading="lazy" />
              <span className="action-card-chip" aria-hidden="true">
                <Glyph />
              </span>
            </div>
            <div className="action-card-body">
              <h3>
                {title}
                <Icon.ArrowRight />
              </h3>
              <p>{body}</p>
            </div>
          </Link>
        ))}
      </div>

      <div className="stats-grid">
        {tiles.map(({ n, label }) => (
          <div className={`stat-card${n === 0 ? ' is-zero' : ''}`} key={label}>
            <div className="stat-number">{n}</div>
            <div className="stat-label">{label}</div>
          </div>
        ))}
      </div>

      <section>
        <div className="section-head">
          <h2>Unread</h2>
          {notifications.length > 0 && (
            <span className="count">{notifications.length}</span>
          )}
        </div>

        {notifications.length === 0 ? (
          <div className="empty">
            <span className="empty-icon">
              <Icon.Inbox />
            </span>
            <strong>Nothing needs you right now</strong>
            <p>
              When a document matches one of your alerts, or someone claims
              something you posted, it shows up here.
            </p>
          </div>
        ) : (
          <>
            <ul className="notification-list">
              {notifications.map((n) => (
                <li key={n.id} className={n.read_at ? 'read' : ''}>
                  <span className="notification-dot" aria-hidden="true">
                    <Icon.Bell />
                  </span>
                  <strong>{n.title}</strong>
                  <p>{n.body}</p>
                  <time dateTime={n.created_at}>
                    {new Date(n.created_at).toLocaleString()}
                  </time>
                  <Link
                    to={['claim_requested', 'claim_paid', 'police_transfer_due'].includes(n.kind) ? '/matches?workspace=finder' : '/matches?workspace=owner'}
                    className="view-all"
                  >
                    Open <Icon.ArrowRight />
                  </Link>
                </li>
              ))}
            </ul>
            <Link to="/matches?workspace=owner" className="view-all">
              View all notifications
              <Icon.ArrowRight />
            </Link>
          </>
        )}
      </section>
    </div>
  )
}
