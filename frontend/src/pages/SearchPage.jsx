import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { authHeaders, useAuth } from '../auth.jsx'
import RedactedPreview from '../components/RedactedPreview.jsx'

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
      <h1>Search for your document</h1>
      <p className="subtitle">
        Matching is exact. Type the ID number or full name exactly as it appears
        on the document.
      </p>

      <form className="card" onSubmit={search}>
        <div className="mode-toggle">
          <label>
            <input
              type="radio"
              checked={mode === 'id_number'}
              onChange={() => setMode('id_number')}
            />
            By ID number
          </label>
          <label>
            <input
              type="radio"
              checked={mode === 'name'}
              onChange={() => setMode('name')}
            />
            By full name
          </label>
        </div>
        <label>
          {mode === 'id_number' ? 'ID number' : 'Full name'}
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={mode === 'id_number' ? 'AB123456' : 'Jane Alice Doe'}
            required
          />
        </label>
        <button type="submit" disabled={busy}>
          {busy ? 'Searching...' : 'Search'}
        </button>
      </form>

      {error && <div className="error">{error}</div>}

      {claimed && (
        <div className="notice ok">
          Claim submitted. The finder has been notified and will review it. You
          will get a notification once they respond.
        </div>
      )}

      {result && result.found && (
        <>
          <h2>{result.results.length === 1 ? 'Possible match' : 'Possible matches'}</h2>
          <p className="muted">{result.suggestion}</p>
          {result.results.map((preview, index) => {
            const matchId = result.match_ids[index]
            return (
              <RedactedPreview key={preview.id} preview={preview}>
                {matchId && !claimed && (
                  <div className="claim-section">
                    {claiming === matchId ? (
                      <div className="claim-form">
                        <label>
                          Enter the full ID number to prove it is yours
                          <input
                            value={claimIdNumber}
                            onChange={(e) => setClaimIdNumber(e.target.value)}
                            placeholder="Full ID number"
                          />
                        </label>
                        <label>
                          Message to the finder (optional)
                          <textarea
                            rows={2}
                            value={claimMessage}
                            onChange={(e) => setClaimMessage(e.target.value)}
                          />
                        </label>
                        {claimError && <div className="error">{claimError}</div>}
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
                        This is mine — start a claim
                      </button>
                    )}
                  </div>
                )}
              </RedactedPreview>
            )
          })}
        </>
      )}

      {result && !result.found && (
        <div className="card no-match-card">
          <h2>No match yet</h2>
          <p>{result.suggestion}</p>
          <button
            type="button"
            onClick={() =>
              navigate('/alerts', {
                state: mode === 'id_number' ? { id_number: query } : { name: query },
              })
            }
          >
            Register a lost-ID alert
          </button>
        </div>
      )}
    </div>
  )
}
