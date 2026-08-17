import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { authHeaders, useAuth } from '../auth.jsx'
import Icon from '../components/Icon.jsx'

const money = (minor = 0) => `KES ${(minor / 100).toLocaleString('en-KE', { minimumFractionDigits: 2 })}`
const label = (value = '') => value.replace(/_/g, ' ')

export default function FinderDashboardPage() {
  const { token, user, setWorkspace } = useAuth()
  const [documents, setDocuments] = useState([])
  const [matches, setMatches] = useState([])
  const [wallet, setWallet] = useState({ available: 0, pending: 0, withdrawn: 0, minimum_withdrawal: 10000 })
  const [withdrawals, setWithdrawals] = useState([])
  const [amount, setAmount] = useState('100')
  const [phone, setPhone] = useState(user?.phone_number || '')
  const [challenge, setChallenge] = useState(null)
  const [code, setCode] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [policeReferences, setPoliceReferences] = useState({})

  const load = useCallback(async () => {
    if (!token) return
    const headers = authHeaders(token)
    const [found, finderMatches, summary, history] = await Promise.all([
      fetch('/api/found-ids/mine', { headers }).then((r) => r.ok ? r.json() : []),
      fetch('/api/matches/finder', { headers }).then((r) => r.ok ? r.json() : []),
      fetch('/api/wallet', { headers }).then((r) => r.ok ? r.json() : wallet),
      fetch('/api/wallet/withdrawals', { headers }).then((r) => r.ok ? r.json() : []),
    ])
    setDocuments(found)
    setMatches(finderMatches)
    setWallet(summary)
    setWithdrawals(history)
  }, [token])

  useEffect(() => { setWorkspace('finder'); load() }, [load])

  const startWithdrawal = async (event) => {
    event.preventDefault()
    setError('')
    setBusy(true)
    try {
      const response = await fetch('/api/wallet/withdrawals', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders(token) },
        body: JSON.stringify({ amount: Math.round(Number(amount) * 100), phone_number: phone }),
      })
      const data = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(data.detail || 'Could not start withdrawal')
      setChallenge(data)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const confirmWithdrawal = async (event) => {
    event.preventDefault()
    setError('')
    setBusy(true)
    try {
      const response = await fetch(`/api/wallet/withdrawals/${challenge.withdrawal.id}/confirm`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders(token) },
        body: JSON.stringify({ challenge_id: challenge.otp.challenge_id, code }),
      })
      const data = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(data.detail || 'Could not confirm withdrawal')
      setChallenge(null)
      setCode('')
      await load()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const recordPoliceTransfer = async (documentId) => {
    setError('')
    try {
      const response = await fetch(`/api/found-ids/${documentId}/police-transfer`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders(token) },
        body: JSON.stringify({ reference: policeReferences[documentId] || '' }),
      })
      const data = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(data.detail || 'Could not record police transfer')
      await load()
    } catch (err) { setError(err.message) }
  }

  const activeHandovers = matches.filter(({ match }) => ['claim_requested', 'awaiting_payment', 'claimed'].includes(match.status))

  return (
    <div className="page">
      <header className="page-head">
        <p className="eyebrow">Finder workspace</p>
        <h1>Documents in your care</h1>
        <p className="subtitle">Manage claims, handovers, escrow and withdrawals from one place.</p>
      </header>

      {error && <div className="error" role="alert"><Icon.AlertTriangle /><span>{error}</span></div>}

      <div className="stats-grid wallet-stats">
        <div className="stat-card"><div className="stat-number money-number">{money(wallet.pending)}</div><div className="stat-label">Pending handover</div></div>
        <div className="stat-card"><div className="stat-number money-number">{money(wallet.available)}</div><div className="stat-label">Available</div></div>
        <div className="stat-card"><div className="stat-number money-number">{money(wallet.withdrawn)}</div><div className="stat-label">Withdrawn</div></div>
      </div>

      <div className="button-row finder-actions">
        <Link className="btn" to="/post-found"><Icon.IdCard />Post a found document</Link>
        <Link className="btn secondary" to="/matches?workspace=finder"><Icon.Handshake />Open claims and handovers</Link>
      </div>

      <section>
        <div className="section-head"><h2>Withdraw earnings</h2></div>
        <form className="card compact-form" onSubmit={challenge ? confirmWithdrawal : startWithdrawal}>
          {!challenge ? <>
            <div className="field"><label htmlFor="withdraw-amount">Amount (KES)</label><input id="withdraw-amount" type="number" min={wallet.minimum_withdrawal / 100} max={wallet.available / 100} step="1" value={amount} onChange={(e) => setAmount(e.target.value)} required /></div>
            <div className="field"><label htmlFor="withdraw-phone">M-Pesa number</label><input id="withdraw-phone" inputMode="tel" value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="07XXXXXXXX" required /></div>
            <button disabled={busy || wallet.available < wallet.minimum_withdrawal}><Icon.ArrowRight />{busy ? 'Requesting code...' : 'Verify payout number'}</button>
            <p className="hint">Minimum withdrawal is {money(wallet.minimum_withdrawal)}. Every payout number is verified by OTP.</p>
          </> : <>
            <div className="field"><label htmlFor="withdraw-code">One-time code sent to {challenge.withdrawal.phone_number}</label><input id="withdraw-code" className="code" inputMode="numeric" value={code} onChange={(e) => setCode(e.target.value)} required /></div>
            {challenge.otp.sandbox_code && <p className="notice warn">Sandbox code: {challenge.otp.sandbox_code}</p>}
            <div className="button-row"><button disabled={busy}><Icon.Check />{busy ? 'Confirming...' : 'Confirm withdrawal'}</button><button type="button" className="secondary" onClick={() => setChallenge(null)}>Cancel</button></div>
          </>}
        </form>
      </section>

      <section>
        <div className="section-head"><h2>Active handovers</h2>{activeHandovers.length > 0 && <span className="count">{activeHandovers.length}</span>}</div>
        {activeHandovers.length === 0 ? <div className="empty"><span className="empty-icon"><Icon.Handshake /></span><strong>No active handovers</strong><p>Approved claims and paid deliveries will appear here.</p></div> :
          <div className="stack">{activeHandovers.map(({ match, full, preview }) => <div className="card" key={match.id}><div className="section-head"><h3>{label((full || preview).document_type)}</h3><span className={`status status-${match.status}`}>{label(match.status)}</span></div><p>{full?.holder_name || preview.holder_name_redacted}</p><p className="muted">{match.status === 'claim_requested' ? 'Review the ownership evidence.' : match.status === 'awaiting_payment' ? 'Waiting for the owner to choose delivery and pay.' : 'Payment is secured. Complete the handover using the receipt code.'}</p><Link to="/matches?workspace=finder" className="view-all">Manage handover <Icon.ArrowRight /></Link></div>)}</div>}
      </section>

      <section>
        <div className="section-head"><h2>Your posted documents</h2>{documents.length > 0 && <span className="count">{documents.length}</span>}</div>
        {documents.length === 0 ? <div className="empty"><strong>No documents posted</strong><p>Post a found document to begin looking for its owner.</p></div> :
          <div className="stack">{documents.map((document) => <div className="card" key={document.id}><div className="section-head"><h3>{label(document.document_type)}</h3><span className={`status status-${document.status}`}>{label(document.status)}</span></div><strong>{document.holder_name}</strong><div className="code-inline">{document.id_number}</div><p className="muted">Public pickup area: {document.found_location || 'Not specified'}</p>{document.police_transfer_due_at && !document.police_transferred_at && <div className="notice warn"><Icon.AlertTriangle /><div><p>This document passed the 90-day unclaimed limit. Transfer it to a police station.</p><div className="confirm-code"><div className="field"><label htmlFor={`police-${document.id}`}>Police occurrence or receipt reference</label><input id={`police-${document.id}`} value={policeReferences[document.id] || ''} onChange={(e) => setPoliceReferences((current) => ({ ...current, [document.id]: e.target.value }))} /></div><button type="button" onClick={() => recordPoliceTransfer(document.id)} disabled={!policeReferences[document.id]}>Record police transfer</button></div></div></div>}{document.police_transferred_at && <p className="notice ok"><Icon.Check />Transferred to police. Reference: {document.police_reference}</p>}</div>)}</div>}
      </section>

      {withdrawals.length > 0 && <section><div className="section-head"><h2>Withdrawal history</h2></div><div className="stack">{withdrawals.map((item) => <div className="card row-card" key={item.id}><div><strong>{money(item.amount)}</strong><p className="muted">{item.phone_number}</p></div><span className={`status status-${item.status}`}>{label(item.status)}</span></div>)}</div></section>}
    </div>
  )
}
