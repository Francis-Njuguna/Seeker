import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { authHeaders, useAuth } from '../auth.jsx'
import RedactedPreview from '../components/RedactedPreview.jsx'
import Icon from '../components/Icon.jsx'

export default function SearchPage() {
  const { token } = useAuth()
  const navigate = useNavigate()
  const [mode, setMode] = useState('id_number')
  const [query, setQuery] = useState('')
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  // Claim state, keyed by match id.
  const [claiming, setClaiming] = useState(null)
  const [claimIdNumber, setClaimIdNumber] = useState('')
  const [claimMessage, setClaimMessage] = useState('')
  const [claimError, setClaimError] = useState('')
  const [claimed, setClaimed] = useState(false)

  const byId = mode === 'id_number'

  const search = async (e) => {
    e.preventDefault()
    setError('')
    setResult(null)
    setClaimed(false)
    setBusy(true)
    try {
      const response = await fetch('/api/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders(token) },
        body: JSON.stringify({ [mode]: query }),
      })
      const data = await response.json()
      if (!response.ok) throw new Error(data.detail || 'Search failed')
      setResult(data)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const submitClaim = async (matchId) => {
    setClaimError('')
    try {
      const response = await fetch(`/api/matches/${matchId}/claim`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders(token) },
        body: JSON.stringify({ id_number: claimIdNumber, message: claimMessage }),
      })
      const data = await response.json()
      if (!response.ok) throw new Error(data.detail || 'Claim failed')
      setClaimed(true)
      setClaiming(null)
    } catch (err) {
      setClaimError(err.message)
    }
  }

  return (
    <div className="page">
      <header className="page-head">
        <h1>Search for your document</h1>
        <p className="subtitle">
          Matching is exact. Type the ID number or full name exactly as it appears
          on the document.
        </p>
      </header>

      <form className="card" onSubmit={search}>
        {/* Segmented control rather than radios: it is two mutually exclusive
            modes for one field, not two separate questions. */}
        <div className="segmented" role="group" aria-label="Search by">
          <button
            type="button"
            className={byId ? 'is-on' : ''}
            aria-pressed={byId}
            onClick={() => setMode('id_number')}
          >
            By ID number
          </button>
          <button
            type="button"
            className={!byId ? 'is-on' : ''}
            aria-pressed={!byId}
            onClick={() => setMode('name')}
          >
            By full name
          </button>
        </div>

        <div className="field">
          <label htmlFor="query">{byId ? 'ID number' : 'Full name'}</label>
          <input
            id="query"
            className={byId ? 'code' : undefined}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={byId ? 'AB123456' : 'Jane Alice Doe'}
            required
          />
          <span className="hint">
            {byId
              ? 'Including any letters, exactly as printed.'
              : 'All the names on the document, in the same order.'}
          </span>
        </div>

        <button type="submit" disabled={busy}>
          <Icon.Search />
          {busy ? 'Searching…' : 'Search'}
        </button>
      </form>

      {error && (
        <div className="error" role="alert">
          <Icon.AlertTriangle />
          <span>{error}</span>
        </div>
      )}

      {claimed && (
        <div className="notice ok">
          <Icon.Check />
          <span>
            Claim submitted. The finder has been notified and will review it. You
            will get a notification once they respond.
          </span>
        </div>
      )}

      {result && result.found && (
        <section>
          <div className="section-head">
            <h2>
              {result.results.length === 1 ? 'Possible match' : 'Possible matches'}
            </h2>
            <span className="count">{result.results.length}</span>
          </div>
          <p className="muted">{result.suggestion}</p>

          <div className="stack" style={{ marginTop: '1rem' }}>
            {result.results.map((preview, index) => {
              const matchId = result.match_ids[index]
              return (
                <RedactedPreview key={preview.id} preview={preview}>
                  {matchId && !claimed && (
                    <div className="claim-section">
                      {claiming === matchId ? (
                        <div className="claim-form">
                          <div className="field">
                            <label htmlFor={`claim-id-${matchId}`}>
                              Enter the full ID number to prove it is yours
                            </label>
                            <input
                              id={`claim-id-${matchId}`}
                              className="code"
                              value={claimIdNumber}
                              onChange={(e) => setClaimIdNumber(e.target.value)}
                              placeholder="Full ID number"
                            />
                          </div>
                          <div className="field">
                            <label htmlFor={`claim-msg-${matchId}`}>
                              Message to the finder{' '}
                              <span className="muted">(optional)</span>
                            </label>
                            <textarea
                              id={`claim-msg-${matchId}`}
                              rows={2}
                              value={claimMessage}
                              onChange={(e) => setClaimMessage(e.target.value)}
                              placeholder="Where and when you lost it, or how to reach you."
                            />
                          </div>
                          {claimError && (
                            <div className="error" role="alert">
                              <Icon.AlertTriangle />
                              <span>{claimError}</span>
                            </div>
                          )}
                          <div className="button-row">
                            <button type="button" onClick={() => submitClaim(matchId)}>
                              Submit claim
                            </button>
                            <button
                              type="button"
                              className="secondary"
                              onClick={() => setClaiming(null)}
                            >
                              Cancel
                            </button>
                          </div>
                        </div>
                      ) : (
                        <button type="button" onClick={() => setClaiming(matchId)}>
                          <Icon.Handshake />
                          This is mine — start a claim
                        </button>
                      )}
                    </div>
                  )}
                </RedactedPreview>
              )
            })}
          </div>
        </section>
      )}

      {result && !result.found && (
        <div className="card no-match-card">
          <span className="empty-icon">
            <Icon.Search />
          </span>
          <h2>No match yet</h2>
          <p>{result.suggestion}</p>
          <button
            type="button"
            onClick={() =>
              navigate('/alerts', {
                state: byId ? { id_number: query } : { name: query },
              })
            }
          >
            <Icon.Bell />
            Register a lost-ID alert
          </button>
        </div>
      )}
    </div>
  )
}
