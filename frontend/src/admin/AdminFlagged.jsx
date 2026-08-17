import { useState } from 'react'
import Icon from '../components/Icon.jsx'
import { authHeaders, useAuth } from '../auth.jsx'
import { ClaimTable } from './AdminClaims.jsx'
import { AdminHead, TableScroll, TableState } from './AdminUI.jsx'
import { useAdminResource } from './api.js'

/**
 * The manual-review queue.
 *
 * GET /api/admin/flagged returns the same AdminClaimRow shape as /claims,
 * filtered to flagged_for_review — matches where a face check ran and failed.
 * admin.py exposes no resolve/dismiss route, so this is a queue an admin reads
 * and acts on elsewhere, not one they can clear from here.
 */
export default function AdminFlagged() {
  const { token } = useAuth()
  const { data, error, loading, reload } = useAdminResource('/api/admin/flagged')
  const [actionError, setActionError] = useState('')
  const [busy, setBusy] = useState(null)
  const rows = data || []

  const decide = async (matchId, approve) => {
    setActionError('')
    setBusy(matchId)
    try {
      const response = await fetch(`/api/admin/claims/${matchId}/decision`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders(token) },
        body: JSON.stringify({ approve, note: approve ? 'Ownership evidence approved after manual review.' : 'Ownership evidence was insufficient after manual review.' }),
      })
      const body = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(body.detail || 'Review failed')
      reload()
    } catch (err) {
      setActionError(err.message)
    } finally {
      setBusy(null)
    }
  }

  const state = (
    <TableState
      loading={loading}
      error={error}
      rows={rows}
      onReload={reload}
      empty={{
        title: 'Nothing flagged',
        body: 'Claims land here when a selfie check fails against the posted photo. An empty queue is the good outcome.',
      }}
    />
  )

  return (
    <div className="page">
      <AdminHead
        title="Flagged for review"
        subtitle="Claims where the face check failed and a human needs to decide."
        count={loading || error ? null : rows.length}
        onReload={reload}
      />

      {state || (
        <>
          <div className="notice warn">
            <Icon.AlertTriangle />
            <span>
              A failed face check is not proof of fraud — a bad photo fails too.
              Confirm identity another way before rejecting a claim.
            </span>
          </div>

          {actionError && <div className="error" role="alert"><Icon.AlertTriangle /><span>{actionError}</span></div>}

          <TableScroll>
            <ClaimTable rows={rows} showFlagReason onDecision={decide} busy={busy} />
          </TableScroll>
        </>
      )}
    </div>
  )
}
