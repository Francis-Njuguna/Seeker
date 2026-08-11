import Icon from '../components/Icon.jsx'
import { AdminHead, Status, TableState } from './AdminUI.jsx'
import { formatMoney, useAdminResource } from './api.js'

/**
 * Platform overview.
 *
 * Every number comes from GET /api/admin/stats — nothing here is computed
 * client-side or hardcoded, so what an admin reads is what the database holds.
 */
export default function AdminOverview() {
  const { data, error, loading, reload } = useAdminResource('/api/admin/stats')

  const state = <TableState loading={loading} error={error} rows={data ? [data] : []} onReload={reload} />
  if (state) {
    return (
      <div className="page">
        <AdminHead title="Overview" subtitle="Platform activity at a glance." />
        {state}
      </div>
    )
  }

  const currency = data.currency

  // Money and volume first: it is the reason this console exists.
  const money = [
    { n: formatMoney(data.gross_volume, currency), label: 'Gross volume', hint: 'Confirmed payments' },
    { n: formatMoney(data.platform_revenue, currency), label: 'Platform revenue' },
    { n: formatMoney(data.finder_payouts, currency), label: 'Finder payouts' },
  ]

  const counts = [
    { n: data.total_users, label: 'Users' },
    { n: data.total_found_ids, label: 'Documents' },
    { n: data.total_lost_alerts, label: 'Lost alerts' },
    { n: data.total_matches, label: 'Matches' },
    { n: data.total_transactions, label: 'Transactions' },
    { n: data.paid_transactions, label: 'Paid' },
    { n: data.claims_completed, label: 'Completed claims' },
    { n: data.flagged_for_review, label: 'Flagged', alert: data.flagged_for_review > 0 },
  ]

  return (
    <div className="page">
      <AdminHead
        title="Overview"
        subtitle="Platform activity at a glance."
        onReload={reload}
      />

      <div className="admin-money-grid">
        {money.map(({ n, label: text, hint }) => (
          <div className="admin-money-card" key={text}>
            <div className="admin-money-value">{n}</div>
            <div className="stat-label">{text}</div>
            {hint && <div className="muted">{hint}</div>}
          </div>
        ))}
      </div>

      <div className="admin-stat-grid">
        {counts.map(({ n, label: text, alert }) => (
          <div
            className={`stat-card${n === 0 ? ' is-zero' : ''}${alert ? ' is-alert' : ''}`}
            key={text}
          >
            <div className="stat-number">{n}</div>
            <div className="stat-label">{text}</div>
          </div>
        ))}
      </div>

      {data.flagged_for_review > 0 && (
        <div className="notice warn">
          <Icon.AlertTriangle />
          <span>
            {data.flagged_for_review} claim
            {data.flagged_for_review === 1 ? '' : 's'} flagged by a failed face
            check need a human decision.
          </span>
        </div>
      )}

      <div className="admin-breakdowns">
        <Breakdown title="Documents by status" counts={data.found_ids_by_status} />
        <Breakdown title="Matches by status" counts={data.matches_by_status} />
      </div>
    </div>
  )
}

function Breakdown({ title, counts }) {
  const entries = Object.entries(counts || {})

  return (
    <section className="card">
      <div className="section-head">
        <h2>{title}</h2>
      </div>
      {entries.length === 0 ? (
        <p className="muted">Nothing recorded yet.</p>
      ) : (
        <ul className="admin-breakdown-list">
          {entries.map(([status, count]) => (
            <li key={status}>
              <Status value={status} />
              <span className="admin-breakdown-count">{count}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
