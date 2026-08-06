import { useCallback, useEffect, useState } from 'react'
import { authHeaders, useAuth } from '../auth.jsx'
import RedactedPreview from '../components/RedactedPreview.jsx'

const label = (value) => (value || '').replace(/_/g, ' ')

export default function MatchesPage() {
  const { token } = useAuth()
  const [mine, setMine] = useState([])
  const [incoming, setIncoming] = useState([])
  const [notifications, setNotifications] = useState([])
  const [error, setError] = useState('')
  const [claimIdNumber, setClaimIdNumber] = useState({})

  const load = useCallback(() => {
    if (!token) return
    const headers = authHeaders(token)
    Promise.all([
      fetch('/api/matches/mine', { headers }).then((r) => (r.ok ? r.json() : [])),
      fetch('/api/matches/incoming', { headers }).then((r) => (r.ok ? r.json() : [])),
      fetch('/api/notifications', { headers }).then((r) => (r.ok ? r.json() : [])),
    ]).then(([m, i, n]) => {
      setMine(m)
      setIncoming(i)
      setNotifications(n)
    })
  }, [token])

  useEffect(load, [load])

  const act = async (url, body) => {
    setError('')
    try {
      const response = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders(token) },
        body: JSON.stringify(body ?? {}),
      })
      if (!response.ok) {
        const data = await response.json().catch(() => ({}))
        throw new Error(data.detail || 'Action failed')
      }
      load()
    } catch (err) {
      setError(err.message)
    }
  }

  const markRead = async (id) => {
    await fetch(`/api/notifications/${id}/read`, {
      method: 'POST',
      headers: authHeaders(token),
    })
    load()
  }

  return (
    <div className="page">
      <h1>Matches &amp; claims</h1>
      {error && <div className="error">{error}</div>}

      <section>
        <h2>Documents you are claiming</h2>
        {mine.length === 0 ? (
          <p className="muted">No matches yet.</p>
        ) : (
          mine.map(({ match, preview }) => (
            <RedactedPreview key={match.id} preview={preview}>
              <div className="claim-section">
                <div className="match-meta">
                  <span className={`status status-${match.status}`}>
                    {label(match.status)}
                  </span>
                  <span className="muted">
                    matched via {label(match.source)} on {label(match.matched_on)}
                  </span>
                </div>

                {match.status === 'matched' && (
                  <div className="claim-form">
                    <label>
                      Enter the full ID number to prove it is yours
                      <input
                        value={claimIdNumber[match.id] || ''}
                        onChange={(e) =>
                          setClaimIdNumber({
                            ...claimIdNumber,
                            [match.id]: e.target.value,
                          })
                        }
                      />
                    </label>
                    <button
                      type="button"
                      onClick={() =>
                        act(`/api/matches/${match.id}/claim`, {
                          id_number: claimIdNumber[match.id] || '',
                        })
                      }
                    >
                      Submit claim
                    </button>
                  </div>
                )}

                {match.status === 'claim_requested' && (
                  <p className="muted">
                    Waiting for the finder to review your claim.
                  </p>
                )}

                {match.status === 'claimed' && (
                  <div>
                    <p className="notice ok">
                      Claim approved. You can now see the full record and arrange
                      handover.
                    </p>
                    <button
                      type="button"
                      onClick={() => act(`/api/matches/${match.id}/complete`)}
                    >
                      Mark as handed over
                    </button>
                  </div>
                )}

                {match.status === 'completed' && (
                  <p className="notice ok">Returned. Case closed.</p>
                )}

                {match.status === 'rejected' && (
                  <p className="notice warn">
                    The finder declined this claim.
                  </p>
                )}
              </div>
            </RedactedPreview>
          ))
        )}
      </section>

      <section>
        <h2>Claims awaiting your decision</h2>
        {incoming.length === 0 ? (
          <p className="muted">No pending claims on documents you posted.</p>
        ) : (
          incoming.map(({ match, preview }) => (
            <div className="card" key={match.id}>
              <p>
                Someone verified the full ID number on the{' '}
                <strong>{label(preview.document_type)}</strong> you posted
                {preview.found_location ? ` (found at ${preview.found_location})` : ''}.
              </p>
              <p className="muted">
                They reproduced the complete number, which only the owner should
                know.
              </p>
              <div className="button-row">
                <button
                  type="button"
                  onClick={() =>
                    act(`/api/matches/${match.id}/decision`, { approve: true })
                  }
                >
                  Approve claim
                </button>
                <button
                  type="button"
                  className="secondary"
                  onClick={() =>
                    act(`/api/matches/${match.id}/decision`, { approve: false })
                  }
                >
                  Decline
                </button>
              </div>
            </div>
          ))
        )}
      </section>

      <section>
        <h2>Notifications</h2>
        {notifications.length === 0 ? (
          <p className="muted">Nothing yet.</p>
        ) : (
          <ul className="notification-list">
            {notifications.map((n) => (
              <li key={n.id} className={n.read_at ? 'read' : ''}>
                <strong>{n.title}</strong>
                <p>{n.body}</p>
                <time>{new Date(n.created_at).toLocaleString()}</time>
                {!n.read_at && (
                  <button
                    type="button"
                    className="link-button"
                    onClick={() => markRead(n.id)}
                  >
                    Mark as read
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}
