import { useMemo, useState } from 'react'
import { AdminHead, Status, TableScroll, TableState } from './AdminUI.jsx'
import { formatDateTime, formatMoney, label, useAdminResource } from './api.js'

/**
 * The transaction ledger.
 *
 * GET /api/admin/transactions takes no query parameters — it returns the whole
 * ledger ordered by created_at desc. Filtering is therefore done client-side on
 * what the endpoint already sent; adding ?status= or ?page= would be inventing
 * backend behaviour that does not exist.
 */
export default function AdminTransactions() {
  const { data, error, loading, reload } = useAdminResource('/api/admin/transactions')
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
      return [row.reference, row.payer_email, row.finder_email, row.holder_name]
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
        title: 'No transactions yet',
        body: 'Payments appear here once a seeker starts checkout on an approved claim.',
      }}
    />
  )

  return (
    <div className="page">
      <AdminHead
        title="Transactions"
        subtitle="Every payment, with the platform/finder split as recorded at the time."
        count={loading || error ? null : rows.length}
        onReload={reload}
      />

      {state || (
        <>
          <div className="admin-filters">
            <div className="field">
              <label htmlFor="txn-search">Search</label>
              <input
                id="txn-search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Reference, payer, finder or holder name"
              />
            </div>
            <div className="field">
              <label htmlFor="txn-status">Status</label>
              <select
                id="txn-status"
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
              <strong>No transactions match that filter</strong>
              <p>Clear the search or pick a different status.</p>
            </div>
          ) : (
            <TableScroll>
              <table className="admin-table">
                <thead>
                  <tr>
                    <th>Reference</th>
                    <th>Status</th>
                    <th>Payer</th>
                    <th>Finder</th>
                    <th>Document</th>
                    <th className="numeric">Amount</th>
                    <th className="numeric">Platform</th>
                    <th className="numeric">Finder share</th>
                    <th>Created</th>
                    <th>Paid</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((row) => (
                    <tr key={row.id}>
                      <td>
                        <span className="code-inline">{row.reference || '—'}</span>
                        <span className="muted admin-provider">{row.provider}</span>
                      </td>
                      <td>
                        <Status value={row.status} />
                      </td>
                      <td>{row.payer_email || '—'}</td>
                      <td>{row.finder_email || '—'}</td>
                      <td>
                        {row.document_type ? label(row.document_type) : '—'}
                        {row.holder_name && (
                          <span className="muted">{row.holder_name}</span>
                        )}
                      </td>
                      <td className="numeric">{formatMoney(row.amount, row.currency)}</td>
                      <td className="numeric">
                        {formatMoney(row.platform_share, row.currency)}
                      </td>
                      <td className="numeric">
                        {formatMoney(row.finder_share, row.currency)}
                      </td>
                      <td>{formatDateTime(row.created_at)}</td>
                      <td>{formatDateTime(row.paid_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableScroll>
          )}

          <p className="hint">
            Showing {filtered.length} of {rows.length}. Amounts are as recorded at
            payment time.
          </p>
        </>
      )}
    </div>
  )
}
