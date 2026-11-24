import { useMemo, useState } from 'react'
import { AdminHead, Status, TableScroll, TableState } from './AdminUI.jsx'
import { formatDateTime, label, useAdminResource } from './api.js'

/**
 * Read-only claim history.
 *
 * GET /api/admin/claims is a plain list — no query parameters, no mutation
 * routes anywhere in admin.py. So this page filters what it already has and
 * offers no approve/reject buttons: there is nothing on the backend to call.
 */
export default function AdminClaims() {
  const { data, error, loading, reload } = useAdminResource('/api/admin/claims')
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState('all')

  const rows = data || []

  const statuses = useMemo(
    () => ['all', ...Array.from(new Set(rows.map((r) => r.status))).sort()],
    [rows],
  )

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return rows.filter((row) => {
      if (status !== 'all' && row.status !== status) return false
      if (!needle) return true
      return [row.seeker_email, row.finder_email, row.holder_name, row.document_type]
        .filter(Boolean)
        .some((field) => field.toLowerCase().includes(needle))
    })
  }, [rows, query, status])

  const state = (
    <TableState
      loading={loading}
      error={error}
      rows={rows}
      onReload={reload}
      empty={{
        title: 'No claims yet',
        body: 'A claim appears once a seeker matches a posted document and asks for it back.',
      }}
    />
  )

  return (
    <div className="page">
      <AdminHead
        title="Claims"
        subtitle="How each claimant proved identity, and how the claim resolved."
        count={loading || error ? null : rows.length}
        onReload={reload}
      />

      {loading || error || rows.length === 0 ? state : (
        <>
          <div className="admin-filters">
            <div className="field">
              <label htmlFor="claim-search">Search</label>
              <input
                id="claim-search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Seeker, finder, holder name or document"
              />
            </div>
            <div className="field">
              <label htmlFor="claim-status">Status</label>
              <select
                id="claim-status"
                value={status}
                onChange={(e) => setStatus(e.target.value)}
              >
                {statuses.map((value) => (
                  <option key={value} value={value}>
                    {value === 'all' ? 'All statuses' : label(value)}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {filtered.length === 0 ? (
            <div className="empty">
              <strong>No claims match that filter</strong>
              <p>Clear the search or pick a different status.</p>
            </div>
          ) : (
            <TableScroll>
              <ClaimTable rows={filtered} />
            </TableScroll>
          )}

          <p className="hint">
            Showing {filtered.length} of {rows.length}. This view is read-only —
            the API exposes no claim mutations.
          </p>
        </>
      )}
    </div>
  )
}

/**
 * Shared by Claims and Flagged: /api/admin/flagged returns the same
 * AdminClaimRow shape, just filtered to flagged_for_review.
 */
export function ClaimTable({ rows, showFlagReason = false, onDecision = null, busy = null }) {
  return (
    <table className="admin-table">
      <thead>
        <tr>
          <th>Document</th>
          <th>Claim status</th>
          <th>Seeker</th>
          <th>Finder</th>
          <th>Verification</th>
          <th>Face check</th>
          <th>Payment</th>
          {showFlagReason && <th>Reason</th>}
          {onDecision && <th>Decision</th>}
          <th>Created</th>
          <th>Claimed</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.match_id} className={row.flagged_for_review ? 'is-flagged' : ''}>
            <td>
              {label(row.document_type)}
              <span className="muted">{row.holder_name}</span>
            </td>
            <td>
              <Status value={row.status} />
            </td>
            <td>{row.seeker_email || '—'}</td>
            <td>{row.finder_email || '—'}</td>
            <td>
              <Status value={row.verification_status} />
              {row.verification_method && (
                <span className="muted">{label(row.verification_method)}</span>
              )}
            </td>
            <td>
              <FaceCell row={row} />
            </td>
            <td>
              {row.payment_status ? <Status value={row.payment_status} /> : '—'}
            </td>
            {showFlagReason && (
              <td className="admin-reason">{row.flag_reason || '—'}</td>
            )}
            {onDecision && (
              <td>
                {row.admin_review_required && row.finder_approved_at && row.status === 'claim_requested' ? (
                  <div className="button-row admin-decisions">
                    <button type="button" onClick={() => onDecision(row.match_id, true)} disabled={busy === row.match_id}>Approve</button>
                    <button type="button" className="danger" onClick={() => onDecision(row.match_id, false)} disabled={busy === row.match_id}>Reject</button>
                  </div>
                ) : <span className="muted">Waiting for finder</span>}
              </td>
            )}
            <td>{formatDateTime(row.created_at)}</td>
            <td>{formatDateTime(row.claimed_at)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/**
 * Face confidence is a 0–1 score the backend already computed. It is shown next
 * to the status — never the embedding itself, which the API does not return and
 * this UI has no business displaying.
 *
 * It is labelled "margin", not "match", because that is what face.py computes:
 * 1 - distance/threshold, i.e. how far inside the match threshold the selfie
 * landed. It is not a probability. A genuine match sits around 42%, so calling
 * it "42% match" invites a reviewer to read a pass as a near-miss. An operator
 * working the flagged queue still needs the number — the ordering is real, and a
 * verdict scraping the threshold deserves more scrutiny than a comfortable one —
 * so unlike the claimant and finder views this one keeps it, correctly named.
 */
function FaceCell({ row }) {
  return (
    <>
      <Status value={row.face_verification_status} />
      {row.face_confidence != null && (
        <span className="muted" title="1 − distance/threshold. Higher is a wider margin inside the match threshold, not a probability.">
          {Math.round(row.face_confidence * 100)}% margin
        </span>
      )}
    </>
  )
}
