import { useCallback, useEffect, useState } from 'react'
import { authHeaders, useAuth } from '../auth.jsx'
import RedactedPreview from '../components/RedactedPreview.jsx'
import Icon from '../components/Icon.jsx'

const label = (value) => (value || '').replace(/_/g, ' ')

export default function MatchesPage() {
  const { token } = useAuth()
  const [mine, setMine] = useState([])
  const [incoming, setIncoming] = useState([])
  const [notifications, setNotifications] = useState([])
  const [error, setError] = useState('')
  const [claimIdNumber, setClaimIdNumber] = useState({})
  const [verifying, setVerifying] = useState({})
  const [verifyResult, setVerifyResult] = useState({})
  const [faceVerifying, setFaceVerifying] = useState({})
  const [faceResult, setFaceResult] = useState({})

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

  // Upload the seeker's own photo of the document. The server OCRs it, compares
  // it to the finder's record, and discards the image.
  const verifyIdentity = async (matchId, file) => {
    if (!file) return
    setError('')
    setVerifying({ ...verifying, [matchId]: true })
    try {
      const body = new FormData()
      body.append('image', file)
      const response = await fetch(`/api/matches/${matchId}/verify-identity`, {
        method: 'POST',
        headers: authHeaders(token),
        body,
      })
      const data = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(data.detail || 'Verification failed')
      setVerifyResult({ ...verifyResult, [matchId]: data })
      load()
    } catch (err) {
      setError(err.message)
    } finally {
      setVerifying({ ...verifying, [matchId]: false })
    }
  }

  // Upload a selfie. The server compares it to the face on the posted document,
  // then discards it — neither the photo nor its embedding is stored.
  //
  // The failure codes mean genuinely different things and are worth keeping
  // apart: 422 is "this document has no usable face on it" or "there are two
  // people in your selfie" — neither is the claimant's fault and neither costs
  // an attempt, so the message has to send them somewhere useful rather than
  // reading as a rejection. 503 is a deployment without the engine. 429 is out
  // of attempts. Only a 200 with verified:false is an actual mismatch.
  const verifyFace = async (matchId, file) => {
    if (!file) return
    setError('')
    setFaceVerifying({ ...faceVerifying, [matchId]: true })
    try {
      const body = new FormData()
      body.append('selfie', file)
      const response = await fetch(`/api/matches/${matchId}/verify-face`, {
        method: 'POST',
        headers: authHeaders(token),
        body,
      })
      const data = await response.json().catch(() => ({}))
      if (!response.ok) {
        setFaceResult({
          ...faceResult,
          [matchId]: { blocked: true, detail: data.detail || 'Selfie check failed' },
        })
        return
      }
      setFaceResult({ ...faceResult, [matchId]: data })
      load()
    } catch (err) {
      setError(err.message)
    } finally {
      setFaceVerifying({ ...faceVerifying, [matchId]: false })
    }
  }

  const markRead = async (id) => {
    await fetch(`/api/notifications/${id}/read`, {
      method: 'POST',
      headers: authHeaders(token),
    })
    load()
  }

  const unreadCount = notifications.filter((n) => !n.read_at).length

  return (
    <div className="page">
      <header className="page-head">
        <h1>Matches &amp; claims</h1>
        <p className="subtitle">
          Everything waiting on you, in one place — documents you are claiming,
          claims other people have made on what you posted, and the trail of
          notifications behind both.
        </p>
      </header>

      {error && (
        <div className="error" role="alert">
          <Icon.AlertTriangle />
          <span>{error}</span>
        </div>
      )}

      <section>
        <div className="section-head">
          <h2>Documents you are claiming</h2>
          {mine.length > 0 && <span className="count">{mine.length}</span>}
        </div>

        {mine.length === 0 ? (
          <div className="empty">
            <span className="empty-icon">
              <Icon.Search />
            </span>
            <strong>No matches yet</strong>
            <p>
              Search for your document, or register an alert and we will match it
              for you as soon as someone posts it.
            </p>
          </div>
        ) : (
          <div className="stack">
            {mine.map(({ match, preview, full }) => {
              // Mirrors the three proofs the claim endpoint accepts: a typed ID
              // number, a verified document photo, or a passing selfie. Keeping
              // this in one place stops the button and the label drifting apart.
              const proven =
                match.verification_status === 'verified' ||
                match.face_verification_status === 'verified'

              return (
              <RedactedPreview key={match.id} preview={preview} full={full}>
                <div className="claim-section">
                  <div className="match-meta">
                    <span className={`status status-${match.status}`}>
                      {label(match.status)}
                    </span>
                    <span className="muted">
                      matched via {label(match.source)} on {label(match.matched_on)}
                    </span>
                    {match.verification_status !== 'unverified' && (
                      <span className={`status status-${match.verification_status}`}>
                        verification: {label(match.verification_status)}
                      </span>
                    )}
                  </div>

                  {match.status === 'matched' && (
                    <div className="claim-form">
                      <p className="muted">
                        Prove it's yours in whichever way is easiest: type the full
                        ID number, upload a photo of your own copy of the document,
                        or take a selfie to match against the photo on it.
                      </p>

                      {match.verification_status === 'unverified' && (
                        <div className="verify-upload">
                          <label className="dropzone">
                            <span className="dropzone-icon">
                              <Icon.Upload />
                            </span>
                            <span className="dropzone-text">
                              <strong>Upload your own photo of this document</strong>
                              <span>
                                We read it, compare it, and delete it straight
                                after
                              </span>
                            </span>
                            <input
                              type="file"
                              accept="image/*"
                              onChange={(e) =>
                                verifyIdentity(match.id, e.target.files[0])
                              }
                              disabled={verifying[match.id]}
                            />
                          </label>

                          {verifying[match.id] && (
                            <p className="muted" style={{ marginTop: '0.6rem' }}>
                              Verifying…
                            </p>
                          )}

                          {verifyResult[match.id] && (
                            <div
                              className={
                                verifyResult[match.id].verified
                                  ? 'notice ok'
                                  : 'notice warn'
                              }
                              style={{ marginTop: '0.85rem' }}
                            >
                              {verifyResult[match.id].verified ? (
                                <Icon.Check />
                              ) : (
                                <Icon.AlertTriangle />
                              )}
                              <div>
                                <p>{verifyResult[match.id].detail}</p>
                                {verifyResult[match.id].verified ? (
                                  <p>
                                    You can now submit the claim below without
                                    typing the ID number.
                                  </p>
                                ) : (
                                  <p>
                                    {verifyResult[match.id].attempts_remaining > 0
                                      ? `${verifyResult[match.id].attempts_remaining} attempts remaining.`
                                      : 'No attempts remaining. Type the full ID number instead.'}
                                  </p>
                                )}
                              </div>
                            </div>
                          )}
                        </div>
                      )}

                      {match.face_verification_status !== 'verified' &&
                        match.face_verification_status !== 'no_face_on_record' &&
                        match.verification_status !== 'verified' && (
                          <div className="verify-upload">
                            <label className="dropzone">
                              <span className="dropzone-icon">
                                <Icon.Camera />
                              </span>
                              <span className="dropzone-text">
                                <strong>Take or upload a selfie</strong>
                                <span>
                                  Matched against the photo on the document, then
                                  deleted — the image and the face data are never
                                  stored
                                </span>
                              </span>
                              <input
                                type="file"
                                accept="image/*"
                                capture="user"
                                onChange={(e) => verifyFace(match.id, e.target.files[0])}
                                disabled={faceVerifying[match.id]}
                              />
                            </label>

                            {faceVerifying[match.id] && (
                              <p className="muted" style={{ marginTop: '0.6rem' }}>
                                Checking the photo…
                              </p>
                            )}

                            {faceResult[match.id] && (
                              <div
                                className={
                                  faceResult[match.id].verified
                                    ? 'notice ok'
                                    : 'notice warn'
                                }
                                style={{ marginTop: '0.85rem' }}
                              >
                                {faceResult[match.id].verified ? (
                                  <Icon.Check />
                                ) : (
                                  <Icon.AlertTriangle />
                                )}
                                <div>
                                  {/* `detail` carries the raw euclidean distance
                                      and the threshold. That is useful for a
                                      blocked attempt, where it explains what to
                                      fix, but showing it on a pass or a
                                      mismatch hands the claimant a score to
                                      optimise against across their five
                                      attempts — so those cases get the verdict
                                      in words instead. */}
                                  {faceResult[match.id].blocked ? (
                                    <p>{faceResult[match.id].detail}</p>
                                  ) : faceResult[match.id].verified ? (
                                    <>
                                      <p>
                                        Your selfie matched the photo on the
                                        document.
                                      </p>
                                      <p>
                                        You can now submit the claim below without
                                        typing the ID number.
                                      </p>
                                    </>
                                  ) : (
                                    <>
                                      <p>
                                        {faceResult[match.id].face_found_in_selfie
                                          ? 'That selfie did not match the photo on the document.'
                                          : 'We could not find a face in that photo. Try again in better light, facing the camera.'}
                                      </p>
                                      <p>
                                        {faceResult[match.id].attempts_remaining > 0
                                          ? `${faceResult[match.id].attempts_remaining} attempts remaining.`
                                          : 'No attempts remaining. Type the full ID number instead.'}
                                      </p>
                                    </>
                                  )}
                                </div>
                              </div>
                            )}
                          </div>
                        )}

                      {match.face_verification_status === 'verified' && (
                        <p className="notice ok">
                          <Icon.ShieldCheck />
                          <span>
                            Selfie matched the photo on the document. You can
                            submit the claim without typing the number.
                          </span>
                        </p>
                      )}

                      {match.verification_status === 'verified' && (
                        <p className="notice ok">
                          <Icon.ShieldCheck />
                          <span>
                            Document verified. You can submit the claim without
                            typing the number.
                          </span>
                        </p>
                      )}

                      {match.verification_status === 'failed' && (
                        <p className="notice warn">
                          <Icon.AlertTriangle />
                          <span>
                            Verification failed. Type the full ID number to claim.
                          </span>
                        </p>
                      )}

                      {match.face_verification_status === 'no_face_on_record' && (
                        <p className="notice warn">
                          <Icon.AlertTriangle />
                          <span>
                            The posted photo has no face clear enough to check a
                            selfie against. Use the ID number or upload a photo of
                            the document instead.
                          </span>
                        </p>
                      )}

                      <div className="field">
                        <label htmlFor={`claim-${match.id}`}>
                          {proven
                            ? 'Full ID number'
                            : 'Enter the full ID number to prove it is yours'}
                          {proven && (
                            <span className="muted"> — optional, already verified</span>
                          )}
                        </label>
                        <input
                          id={`claim-${match.id}`}
                          className="code"
                          placeholder="AB123456"
                          value={claimIdNumber[match.id] || ''}
                          onChange={(e) =>
                            setClaimIdNumber({
                              ...claimIdNumber,
                              [match.id]: e.target.value,
                            })
                          }
                          disabled={verifying[match.id]}
                        />
                      </div>

                      <button
                        type="button"
                        disabled={
                          verifying[match.id] ||
                          faceVerifying[match.id] ||
                          (!proven && !claimIdNumber[match.id])
                        }
                        onClick={() =>
                          act(`/api/matches/${match.id}/claim`, {
                            id_number: claimIdNumber[match.id] || null,
                          })
                        }
                      >
                        <Icon.Handshake />
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
                        <Icon.Check />
                        <span>
                          Claim approved. You can now see the full record and
                          arrange handover.
                        </span>
                      </p>
                      <button
                        type="button"
                        onClick={() => act(`/api/matches/${match.id}/complete`)}
                      >
                        <Icon.CheckCircle />
                        Mark as handed over
                      </button>
                    </div>
                  )}

                  {match.status === 'completed' && (
                    <p className="notice ok">
                      <Icon.CheckCircle />
                      <span>Returned. Case closed.</span>
                    </p>
                  )}

                  {match.status === 'rejected' && (
                    <p className="notice warn">
                      <Icon.X />
                      <span>The finder declined this claim.</span>
                    </p>
                  )}
                </div>
              </RedactedPreview>
              )
            })}
          </div>
        )}
      </section>

      <section>
        <div className="section-head">
          <h2>Claims awaiting your decision</h2>
          {incoming.length > 0 && <span className="count">{incoming.length}</span>}
        </div>

        {incoming.length === 0 ? (
          <div className="empty">
            <span className="empty-icon">
              <Icon.Inbox />
            </span>
            <strong>Nothing to decide</strong>
            <p>
              When someone claims a document you posted, their claim lands here
              for you to approve or decline.
            </p>
          </div>
        ) : (
          <div className="stack">
            {incoming.map(({ match, preview, full }) => (
              <div className="card" key={match.id}>
                <p>
                  Someone is claiming the{' '}
                  <strong>
                    {label(full?.document_type || preview.document_type)}
                  </strong>{' '}
                  you posted
                  {preview.found_location
                    ? ` (found at ${preview.found_location})`
                    : ''}
                  .
                </p>

                {match.verification_method === 'face_selfie' ? (
                  <p className="notice ok" style={{ marginTop: '0.85rem' }}>
                    <Icon.Camera />
                    <span>
                      {/* Deliberately no percentage. face_confidence is a
                          margin to the match threshold, not a probability — a
                          genuine match reads as 42%, which a finder would
                          reasonably misread as "probably not them". */}
                      Their selfie matched the photo on the document. A face
                      check is strong evidence but not proof of ownership, so
                      confirm anything else you can before approving.
                    </span>
                  </p>
                ) : match.verification_method === 'document_image' ? (
                  <p className="notice warn" style={{ marginTop: '0.85rem' }}>
                    <Icon.AlertTriangle />
                    <span>
                      They uploaded their own photo of the document and it matched
                      this record automatically
                      {match.verification_score != null
                        ? ` (score ${match.verification_score}).`
                        : '.'}{' '}
                      Automated checks can be fooled, so confirm anything else you
                      can before approving.
                    </span>
                  </p>
                ) : (
                  <p className="notice ok" style={{ marginTop: '0.85rem' }}>
                    <Icon.ShieldCheck />
                    <span>
                      They reproduced the complete ID number, which only the owner
                      should know.
                    </span>
                  </p>
                )}

                <div className="button-row">
                  <button
                    type="button"
                    onClick={() =>
                      act(`/api/matches/${match.id}/decision`, { approve: true })
                    }
                  >
                    <Icon.Check />
                    Approve claim
                  </button>
                  <button
                    type="button"
                    className="danger"
                    onClick={() =>
                      act(`/api/matches/${match.id}/decision`, { approve: false })
                    }
                  >
                    <Icon.X />
                    Decline
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      <section>
        <div className="section-head">
          <h2>Notifications</h2>
          {unreadCount > 0 && <span className="count">{unreadCount} unread</span>}
        </div>

        {notifications.length === 0 ? (
          <div className="empty">
            <span className="empty-icon">
              <Icon.Bell />
            </span>
            <strong>Nothing yet</strong>
            <p>Matches, claims and approvals all leave a note here.</p>
          </div>
        ) : (
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
