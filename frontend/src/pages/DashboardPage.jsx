import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { authHeaders, useAuth } from '../auth.jsx'

export default function DashboardPage() {
  const { token } = useAuth()
  const [notifications, setNotifications] = useState([])
  const [stats, setStats] = useState({ found: 0, alerts: 0, matches: 0 })

  useEffect(() => {
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

  return (
    <div className="page">
      <h1>Dashboard</h1>

      <div className="card-grid">
        <Link to="/post-found" className="card action-card">
          <h3>📋 Post a found ID</h3>
          <p>Found someone's document? Post it here.</p>
        </Link>
        <Link to="/search" className="card action-card">
          <h3>🔍 Search for yours</h3>
          <p>Lost something? Search by ID number or name.</p>
        </Link>
        <Link to="/alerts" className="card action-card">
          <h3>🔔 Register an alert</h3>
          <p>Get notified if your document turns up later.</p>
        </Link>
      </div>

      <div className="stats-grid">
        <div className="stat-card">
          <div className="stat-number">{stats.found}</div>
          <div className="stat-label">IDs you posted</div>
        </div>
        <div className="stat-card">
          <div className="stat-number">{stats.alerts}</div>
          <div className="stat-label">Active alerts</div>
        </div>
        <div className="stat-card">
          <div className="stat-number">{stats.matches}</div>
          <div className="stat-label">Matches</div>
        </div>
      </div>

      {notifications.length > 0 && (
        <div className="card">
          <h2>Recent notifications</h2>
          <ul className="notification-list">
            {notifications.map((n) => (
              <li key={n.id} className={n.read_at ? 'read' : ''}>
                <strong>{n.title}</strong>
                <p>{n.body}</p>
                <time>{new Date(n.created_at).toLocaleString()}</time>
              </li>
            ))}
          </ul>
          <Link to="/matches" className="view-all">
            View all →
          </Link>
        </div>
      )}
    </div>
  )
}
