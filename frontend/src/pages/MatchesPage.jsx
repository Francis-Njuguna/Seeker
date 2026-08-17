import { useCallback, useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { authHeaders, useAuth } from '../auth.jsx'
import RedactedPreview from '../components/RedactedPreview.jsx'
import Icon from '../components/Icon.jsx'

const label = (value = '') => value.replace(/_/g, ' ')
const money = (minor = 0) => `KES ${(minor / 100).toLocaleString('en-KE', { minimumFractionDigits: 2 })}`

const documentCheckText = (match) => {
  if (match.verification_status === 'inconclusive') {
    return 'No usable name or document number was read from the photo. No attempt was used.'
  }
  return null
}

async function api(token, url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: options.body instanceof FormData
      ? { ...authHeaders(token), ...options.headers }
      : { 'Content-Type': 'application/json', ...authHeaders(token), ...options.headers },
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.detail || 'Action failed')
  return data
}

function Status({ value }) {
  return <span className={`status status-${value}`}>{label(value)}</span>
}

function Conversation({ token, userId, matchId }) {
  const [open, setOpen] = useState(false)
  const [messages, setMessages] = useState([])
  const [body, setBody] = useState('')
  const [error, setError] = useState('')

  const load = async () => {
    try { setMessages(await api(token, `/api/messages/${matchId}`)) } catch (err) { setError(err.message) }
  }

  useEffect(() => { if (open) load() }, [open, matchId])

  const send = async (event) => {
    event.preventDefault()
    if (!body.trim()) return
    try {
      await api(token, `/api/messages/${matchId}`, { method: 'POST', body: JSON.stringify({ body }) })
      setBody('')
      await load()
    } catch (err) { setError(err.message) }
  }

  return <div className="conversation">
    <button type="button" className="secondary" onClick={() => setOpen(!open)}><Icon.Inbox />{open ? 'Close messages' : 'Open messages'}</button>
    {open && <div className="message-panel">
      {error && <p className="error">{error}</p>}
      <div className="message-list">{messages.length === 0 ? <p className="muted">No messages yet.</p> : messages.map((message) => <div key={message.id} className={`message ${message.sender_user_id === userId ? 'mine' : ''}`}><p>{message.body}</p><time>{new Date(message.created_at).toLocaleString()}</time></div>)}</div>
      <form className="message-form" onSubmit={send}><input value={body} onChange={(e) => setBody(e.target.value)} placeholder="Write a private message" maxLength={2000} /><button aria-label="Send message"><Icon.ArrowRight /></button></form>
    </div>}
  </div>
}

function OwnerClaim({ entry, token, user, reload, delivery, setDelivery, payment, setPayment, fail }) {
  const { match, preview, full } = entry
  const [idNumber, setIdNumber] = useState('')
  const [message, setMessage] = useState('')
  const [method, setMethod] = useState('courier')
  const [destination, setDestination] = useState('')
  const [receiptBusy, setReceiptBusy] = useState(false)
  const [busy, setBusy] = useState('')

  const upload = async (kind, file) => {
    if (!file) return
    setBusy(kind)
    const form = new FormData()
    form.append(kind === 'document' ? 'image' : 'selfie', file)
    try {
      await api(token, `/api/matches/${match.id}/${kind === 'document' ? 'verify-identity' : 'verify-face'}`, { method: 'POST', body: form })
      await reload()
    } catch (err) { fail(err) } finally { setBusy('') }
  }

  const submitClaim = async () => {
    setBusy('claim')
    try {
      await api(token, `/api/matches/${match.id}/claim`, { method: 'POST', body: JSON.stringify({ id_number: idNumber || null, message: message || null }) })
      await reload()
    } catch (err) { fail(err) } finally { setBusy('') }
  }

  const quote = async (event) => {
    event.preventDefault()
    setBusy('quote')
    try {
      const data = await api(token, `/api/deliveries/${match.id}/quote`, { method: 'PUT', body: JSON.stringify({ method, destination_address: destination }) })
      setDelivery((current) => ({ ...current, [match.id]: data }))
    } catch (err) { fail(err) } finally { setBusy('') }
  }

  const pay = async () => {
    setBusy('payment')
    try {
      const started = await api(token, `/api/payments/${match.id}/initiate`, { method: 'POST', body: JSON.stringify({ phone: user.phone_number }) })
      setPayment((current) => ({ ...current, [match.id]: started }))
      if (started.reference?.startsWith('MOCK-')) {
        await api(token, `/api/payments/mock/${started.reference}`, { method: 'POST' })
        await reload()
      }
    } catch (err) { fail(err) } finally { setBusy('') }
  }

  const checkPayment = async () => {
    setBusy('payment-check')
    try {
      const status = await api(token, `/api/payments/${match.id}`)
      setPayment((current) => ({ ...current, [match.id]: { ...current[match.id], ...status } }))
      if (status.status === 'paid') await reload()
    } catch (err) { fail(err) } finally { setBusy('') }
  }

  const refreshReceipt = async () => {
    setReceiptBusy(true)
    try {
      const data = await api(token, `/api/deliveries/${match.id}`)
      setDelivery((current) => ({ ...current, [match.id]: data }))
      await reload()
    } catch (err) { fail(err) } finally { setReceiptBusy(false) }
  }

  const proofOpen = ['matched', 'claim_pending'].includes(match.status)
  const quoted = delivery[match.id]
  const startedPayment = payment[match.id]

  return <RedactedPreview preview={preview} full={full}>
    <div className="claim-section workflow">
      <div className="match-meta"><Status value={match.status} /><span className="muted">Matched by {label(match.matched_on)}</span></div>

      {proofOpen && <>
        <p className="muted">Portrait documents use both your document copy and a selfie. If either is unavailable, submit other evidence for finder and admin review.</p>
        <div className="proof-grid">
          <label className="dropzone"><span className="dropzone-icon"><Icon.Upload /></span><span className="dropzone-text"><strong>Your copy of the document</strong><span>{match.verification_status === 'verified' ? 'Verified' : match.verification_status === 'inconclusive' ? 'Try a clearer, tightly framed photo' : 'Read once, compared and discarded'}</span></span><input type="file" accept="image/*" onChange={(e) => upload('document', e.target.files[0])} disabled={busy === 'document' || match.verification_status === 'verified'} /></label>
          {preview.has_reference_face && <label className="dropzone"><span className="dropzone-icon"><Icon.Camera /></span><span className="dropzone-text"><strong>Your selfie</strong><span>{match.face_verification_status === 'verified' ? 'Verified' : 'Compared transiently and discarded'}</span></span><input type="file" accept="image/*" capture="user" onChange={(e) => upload('face', e.target.files[0])} disabled={busy === 'face' || match.face_verification_status === 'verified'} /></label>}
        </div>
        {documentCheckText(match) && <p className="notice warn"><Icon.Inbox />{documentCheckText(match)}</p>}
        <div className="form-grid">
          <div className="field"><label htmlFor={`number-${match.id}`}>Full document number <span className="muted">- fallback evidence</span></label><input id={`number-${match.id}`} className="code" value={idNumber} onChange={(e) => setIdNumber(e.target.value)} /></div>
          <div className="field"><label htmlFor={`note-${match.id}`}>Note to finder <span className="muted">- optional</span></label><input id={`note-${match.id}`} value={message} onChange={(e) => setMessage(e.target.value)} /></div>
        </div>
        <button type="button" onClick={submitClaim} disabled={busy === 'claim' || (!idNumber && match.verification_status !== 'verified' && match.face_verification_status !== 'verified')}><Icon.Handshake />{busy === 'claim' ? 'Submitting...' : 'Submit ownership claim'}</button>
      </>}

      {match.status === 'claim_requested' && <p className="notice warn"><Icon.Inbox />{match.finder_approved_at ? 'The finder approved this claim. It is waiting for admin review.' : 'Your evidence is waiting for the finder to review it.'}</p>}

      {match.status === 'awaiting_payment' && <div className="recovery-setup">
        <h3>Choose recovery</h3>
        <form onSubmit={quote}>
          <div className="segmented"><button type="button" className={method === 'courier' ? 'is-on' : ''} onClick={() => setMethod('courier')}>Courier</button><button type="button" className={method === 'meetup' ? 'is-on' : ''} onClick={() => setMethod('meetup')}>Staffed public meetup</button></div>
          <div className="field"><label htmlFor={`destination-${match.id}`}>{method === 'courier' ? 'Delivery destination' : 'Preferred area or public facility'}</label><textarea id={`destination-${match.id}`} value={destination} onChange={(e) => setDestination(e.target.value)} rows={2} placeholder={method === 'courier' ? 'County, town, street and landmark' : 'Police station, Posta branch or partner shop'} required /></div>
          <button disabled={busy === 'quote'}><Icon.Search />{busy === 'quote' ? 'Calculating...' : 'Get recovery quote'}</button>
        </form>
        {quoted && <div className="quote-summary"><div><strong>{quoted.method === 'courier' ? quoted.provider : 'Public meetup'}</strong><p className="muted">{quoted.suggested_public_location || quoted.destination_address}</p></div><strong>{money(quoted.fee)}</strong></div>}
        {quoted && <div className="payment-summary"><p>Recovery fee <strong>{money(20000)}</strong></p><p>{quoted.method === 'courier' ? 'Courier' : 'Meetup'} <strong>{money(quoted.fee)}</strong></p><p className="total">Total <strong>{money(20000 + quoted.fee)}</strong></p><button type="button" onClick={pay} disabled={busy === 'payment'}><Icon.ArrowRight />{busy === 'payment' ? 'Sending STK push...' : `Pay ${money(20000 + quoted.fee)}`}</button></div>}
        {startedPayment && !startedPayment.reference?.startsWith('MOCK-') && <div className="notice ok"><Icon.Check /><div><p>STK push sent to {user.phone_number}. Approve it on your phone.</p><button type="button" className="secondary" onClick={checkPayment} disabled={busy === 'payment-check'}>{busy === 'payment-check' ? 'Checking...' : 'Check payment status'}</button></div></div>}
      </div>}

      {match.status === 'claimed' && <div className="receipt-panel">
        <p className="notice ok"><Icon.Check />Payment is secured. Do not share the receipt code until the document is physically in your hands.</p>
        {quoted?.handover_code ? <div className="receipt-code"><span>Receipt code</span><strong>{quoted.handover_code}</strong></div> : <button type="button" className="secondary" onClick={refreshReceipt} disabled={receiptBusy}>{receiptBusy ? 'Checking...' : 'Show receipt code'}</button>}
        {quoted?.tracking_reference && <p className="muted">Tracking reference: <span className="code-inline">{quoted.tracking_reference}</span></p>}
      </div>}

      {match.status === 'completed' && <p className="notice ok"><Icon.Check />Receipt confirmed. This recovery is complete.</p>}
      {['claim_requested', 'awaiting_payment', 'claimed'].includes(match.status) && <Conversation token={token} userId={user.id} matchId={match.id} />}
    </div>
  </RedactedPreview>
}

function FinderClaim({ entry, token, user, reload, delivery, setDelivery, fail }) {
  const { match, full, preview } = entry
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState('')
  const document = full || preview

  const act = async (path, body) => {
    setBusy(path)
    try {
      await api(token, path, { method: 'POST', body: JSON.stringify(body || {}) })
      await reload()
      if (['dispatch', 'confirm'].some((part) => path.endsWith(part))) {
        const data = await api(token, `/api/deliveries/${match.id}`)
        setDelivery((current) => ({ ...current, [match.id]: data }))
      }
    } catch (err) { fail(err) } finally { setBusy('') }
  }

  const loadDelivery = async () => {
    try { const data = await api(token, `/api/deliveries/${match.id}`); setDelivery((current) => ({ ...current, [match.id]: data })) } catch (err) { fail(err) }
  }

  const order = delivery[match.id]
  return <div className="card workflow-card">
    <div className="section-head"><div><h3>{label(document.document_type)}</h3><p>{document.holder_name}</p></div><Status value={match.status} /></div>
    <div className="evidence-summary"><p>Document check: <Status value={match.verification_status} /></p><p>Selfie check: <Status value={match.face_verification_status} /></p>{documentCheckText(match) && <p className="muted">{documentCheckText(match)}</p>}{match.flag_reason && <p className="muted">Review note: {match.flag_reason}</p>}</div>
    {match.status === 'claim_requested' && !match.finder_approved_at && <div className="button-row"><button type="button" onClick={() => act(`/api/matches/${match.id}/decision`, { approve: true })} disabled={busy}><Icon.Check />Approve owner</button><button type="button" className="danger" onClick={() => act(`/api/matches/${match.id}/decision`, { approve: false, note: 'Evidence did not establish ownership.' })} disabled={busy}>Decline</button></div>}
    {match.status === 'claim_requested' && match.finder_approved_at && <p className="notice warn"><Icon.Inbox />Finder review complete. Seeker admin review is still required.</p>}
    {match.status === 'awaiting_payment' && <p className="muted">Waiting for the owner to choose a recovery method and complete payment.</p>}
    {match.status === 'claimed' && <div className="handover-actions">
      {!order && <button type="button" className="secondary" onClick={loadDelivery}>Load handover details</button>}
      {order && <><div className="quote-summary"><div><strong>{order.provider || 'Staffed public meetup'}</strong><p className="muted">{order.destination_address}</p><p className="muted">Pickup: {order.pickup_address}</p></div><Status value={order.status} /></div>
        {order.status === 'ready_for_handover' && <button type="button" onClick={() => act(`/api/deliveries/${match.id}/dispatch`)} disabled={busy}><Icon.ArrowRight />Mark document dispatched or handed to staff</button>}
        {['ready_for_handover', 'in_transit'].includes(order.status) && <div className="confirm-code"><div className="field"><label htmlFor={`code-${match.id}`}>Receipt code from owner</label><input id={`code-${match.id}`} className="code" inputMode="numeric" value={code} onChange={(e) => setCode(e.target.value)} placeholder="6 digits" /></div><button type="button" onClick={() => act(`/api/deliveries/${match.id}/confirm`, { code })} disabled={!code || busy}><Icon.Check />Confirm receipt and release KES 100</button></div>}
      </>}
    </div>}
    {match.status === 'completed' && <p className="notice ok"><Icon.Check />Handover confirmed. KES 100 is available in your finder wallet.</p>}
    {['claim_requested', 'awaiting_payment', 'claimed'].includes(match.status) && <Conversation token={token} userId={user.id} matchId={match.id} />}
  </div>
}

export default function MatchesPage() {
  const { token, user, workspace, setWorkspace } = useAuth()
  const [searchParams] = useSearchParams()
  const [mine, setMine] = useState([])
  const [finder, setFinder] = useState([])
  const [delivery, setDelivery] = useState({})
  const [payment, setPayment] = useState({})
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    if (!token) return
    const [ownerRows, finderRows] = await Promise.all([
      api(token, '/api/matches/mine').catch(() => []),
      api(token, '/api/matches/finder').catch(() => []),
    ])
    setMine(ownerRows)
    setFinder(finderRows)
  }, [token])

  useEffect(() => { load() }, [load])
  const requestedWorkspace = searchParams.get('workspace')
  const activeWorkspace = ['owner', 'finder'].includes(requestedWorkspace)
    ? requestedWorkspace
    : workspace
  useEffect(() => {
    if (requestedWorkspace === 'owner' || requestedWorkspace === 'finder') {
      setWorkspace(requestedWorkspace)
    }
  }, [requestedWorkspace])
  const fail = (err) => setError(err.message || String(err))
  const rows = activeWorkspace === 'finder' ? finder : mine
  const otherRows = activeWorkspace === 'finder' ? mine : finder
  const otherWorkspace = activeWorkspace === 'finder' ? 'owner' : 'finder'

  return <div className="page">
    <header className="page-head"><p className="eyebrow">{activeWorkspace === 'finder' ? 'Finder workspace' : 'Owner workspace'}</p><h1>{activeWorkspace === 'finder' ? 'Claims and handovers' : 'Your recovery claims'}</h1><p className="subtitle">{activeWorkspace === 'finder' ? 'Review ownership evidence and release documents only through the receipt-code handover.' : 'Follow each claim from private verification through receipt.'}</p></header>
    {error && <div className="error" role="alert"><Icon.AlertTriangle /><span>{error}</span><button type="button" className="link-button" onClick={() => setError('')}>Dismiss</button></div>}
    {rows.length === 0 ? <div className="empty"><span className="empty-icon"><Icon.Handshake /></span><strong>No claims in the {activeWorkspace} workspace</strong><p>{otherRows.length > 0 ? `You have ${otherRows.length} active ${otherWorkspace} ${otherRows.length === 1 ? 'claim' : 'claims'}.` : activeWorkspace === 'finder' ? 'Claims on documents you post will appear here.' : 'Search for your document to begin a recovery claim.'}</p>{otherRows.length > 0 && <Link className="btn" to={`/matches?workspace=${otherWorkspace}`}>Open {otherWorkspace} {otherRows.length === 1 ? 'claim' : 'claims'} <Icon.ArrowRight /></Link>}</div> : <div className="stack">{rows.map((entry) => activeWorkspace === 'finder' ? <FinderClaim key={entry.match.id} entry={entry} token={token} user={user} reload={load} delivery={delivery} setDelivery={setDelivery} fail={fail} /> : <OwnerClaim key={entry.match.id} entry={entry} token={token} user={user} reload={load} delivery={delivery} setDelivery={setDelivery} payment={payment} setPayment={setPayment} fail={fail} />)}</div>}
  </div>
}
