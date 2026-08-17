import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { authHeaders, useAuth } from '../auth.jsx'
import RedactedPreview from '../components/RedactedPreview.jsx'
import Icon from '../components/Icon.jsx'

const DOCUMENT_TYPES = [
  ['national_id', 'National ID'],
  ['passport', 'Passport'],
  ['driving_licence', 'Driving licence'],
  ['birth_certificate', 'Birth certificate'],
  ['alien_card', 'Alien card'],
  ['refugee_id', 'Refugee ID'],
  ['student_id', 'Student ID'],
  ['staff_id', 'Staff ID'],
  ['nhif_card', 'Health insurance card'],
  ['bank_card', 'Bank card'],
  ['other', 'Other document'],
]

export default function SearchPage() {
  const { token } = useAuth()
  const navigate = useNavigate()
  const [mode, setMode] = useState('number')
  const [documentType, setDocumentType] = useState('national_id')
  const [idNumber, setIdNumber] = useState('')
  const [name, setName] = useState('')
  const [dateOfBirth, setDateOfBirth] = useState('')
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [starting, setStarting] = useState(null)

  const search = async (event) => {
    event.preventDefault()
    setError('')
    setResult(null)
    setBusy(true)
    try {
      const payload = mode === 'number'
        ? { document_type: documentType, id_number: idNumber }
        : { document_type: documentType, name, date_of_birth: dateOfBirth }
      const response = await fetch('/api/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders(token) },
        body: JSON.stringify(payload),
      })
      const data = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(data.detail || 'Search failed')
      setResult(data)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const startClaim = async (matchId) => {
    setError('')
    setStarting(matchId)
    try {
      const response = await fetch(`/api/matches/${matchId}/start-claim`, {
        method: 'POST',
        headers: authHeaders(token),
      })
      const data = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(data.detail || 'Could not reserve this document')
      navigate('/matches')
    } catch (err) {
      setError(err.message)
    } finally {
      setStarting(null)
    }
  }

  return (
    <div className="page">
      <header className="page-head">
        <p className="eyebrow">Owner workspace</p>
        <h1>Find your document</h1>
        <p className="subtitle">Search exact details. Results stay redacted until your claim is verified, approved and paid.</p>
      </header>

      <form className="card" onSubmit={search}>
        <div className="field">
          <label htmlFor="search-document-type">Document type</label>
          <select id="search-document-type" value={documentType} onChange={(e) => setDocumentType(e.target.value)}>
            {DOCUMENT_TYPES.map(([value, text]) => <option key={value} value={value}>{text}</option>)}
          </select>
        </div>

        <div className="segmented" role="group" aria-label="Search method">
          <button type="button" className={mode === 'number' ? 'is-on' : ''} onClick={() => setMode('number')}>Document number</button>
          <button type="button" className={mode === 'identity' ? 'is-on' : ''} onClick={() => setMode('identity')}>Full name and date of birth</button>
        </div>

        {mode === 'number' ? (
          <div className="field">
            <label htmlFor="search-id-number">Exact document number</label>
            <input id="search-id-number" className="code" value={idNumber} onChange={(e) => setIdNumber(e.target.value)} placeholder="Enter the complete number" required />
          </div>
        ) : (
          <div className="form-grid">
            <div className="field"><label htmlFor="search-name">Full name on document</label><input id="search-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="As printed" required /></div>
            <div className="field"><label htmlFor="search-dob">Date of birth</label><input id="search-dob" type="date" value={dateOfBirth} onChange={(e) => setDateOfBirth(e.target.value)} required /></div>
          </div>
        )}

        <button disabled={busy}><Icon.Search />{busy ? 'Searching...' : 'Search securely'}</button>
      </form>

      {error && <div className="error" role="alert"><Icon.AlertTriangle /><span>{error}</span></div>}

      {result?.found && <section>
        <div className="section-head"><h2>Possible matches</h2><span className="count">{result.results.length}</span></div>
        <p className="muted">{result.suggestion}</p>
        <div className="stack search-results">
          {result.results.map((preview, index) => {
            const matchId = result.match_ids[index]
            return <RedactedPreview key={preview.id} preview={preview}>
              <div className="claim-section">
                <p className="muted">Reserve this result before uploading private ownership evidence. The reservation expires if you abandon it.</p>
                <button type="button" onClick={() => startClaim(matchId)} disabled={starting === matchId}><Icon.Handshake />{starting === matchId ? 'Reserving...' : 'Start ownership check'}</button>
              </div>
            </RedactedPreview>
          })}
        </div>
      </section>}

      {result && !result.found && <div className="empty no-match-card"><span className="empty-icon"><Icon.Search /></span><strong>No exact match yet</strong><p>{result.suggestion}</p></div>}
    </div>
  )
}
