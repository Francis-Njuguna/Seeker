import { useCallback, useEffect, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { authHeaders, useAuth } from '../auth.jsx'

const label = (value) => (value || '').replace(/_/g, ' ')

export default function AlertsPage() {
  const { token } = useAuth()
  const location = useLocation()
  const prefill = location.state || {}

  const [alerts, setAlerts] = useState([])
  const [form, setForm] = useState({
    holder_name: prefill.name || '',
    id_number: prefill.id_number || '',
    document_type: '',
  })
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    if (!token) return
    fetch('/api/lost-alerts', { headers: authHeaders(token) })
      .then((r) => (r.ok ? r.json() : []))
      .then(setAlerts)
      .catch(() => {})
  }, [token])

  useEffect(load, [load])

  const update = (key) => (e) => setForm({ ...form, [key]: e.target.value })

  const submit = async (e) => {
    e.preventDefault()
    setError('')
    setNotice('')
    setBusy(true)
    try {
      const response = await fetch('/api/lost-alerts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders(token) },
        body: JSON.stringify({
          holder_name: form.holder_name,
          id_number: form.id_number,
          document_type: form.document_type || null,
        }),
      })
      const data = await response.json()
      if (!response.ok) {
        throw new Error(
          typeof data.detail === 'string' ? data.detail : 'Could not save the alert',
        )
      }
      setNotice(
        data.status === 'matched'
          ? 'A matching document is already posted. Check your matches now.'
          : 'Alert registered. We will email you if this document is posted.',
      )
      setForm({ holder_name: '', id_number: '', document_type: '' })
      load()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const cancel = async (id) => {
    await fetch(`/api/lost-alerts/${id}`, {
      method: 'DELETE',
      headers: authHeaders(token),
    })
    load()
  }

  return (
    <div className="page">
      <h1>Lost-ID alerts</h1>
      <p className="subtitle">
        If your document has not been posted yet, register an alert. We check
        every new posting against your alerts and notify you automatically.
      </p>

      {error && <div className="error">{error}</div>}
      {notice && <div className="notice ok">{notice}</div>}

      <form className="card" onSubmit={submit}>
        <h2>Register a new alert</h2>
        <label>
          Name on the document
          <input value={form.holder_name} onChange={update('holder_name')} required />
        </label>
        <label>
          ID number
          <input value={form.id_number} onChange={update('id_number')} required />
          <span className="hint">Matching is exact, ignoring case and dashes.</span>
        </label>
        <label>
          Document type (optional)
          <input
            value={form.document_type}
            onChange={update('document_type')}
            placeholder="e.g. passport"
          />
        </label>
        <button type="submit" disabled={busy}>
          {busy ? 'Saving...' : 'Register alert'}
        </button>
      </form>

      <h2>Your alerts</h2>
      {alerts.length === 0 ? (
        <p className="muted">No alerts yet.</p>
      ) : (
        <ul className="alert-list">
          {alerts.map((a) => (
            <li key={a.id} className="card alert-item">
              <div>
                <strong>{a.holder_name}</strong>
                <div className="muted">
                  {a.id_number}
                  {a.document_type ? ` · ${label(a.document_type)}` : ''}
                </div>
                <div className="muted">
                  Registered {new Date(a.created_at).toLocaleDateString()}
                </div>
              </div>
              <div className="alert-actions">
                <span className={`status status-${a.status}`}>{label(a.status)}</span>
                {a.status === 'active' && (
                  <button
                    type="button"
                    className="secondary"
                    onClick={() => cancel(a.id)}
                  >
                    Cancel
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
