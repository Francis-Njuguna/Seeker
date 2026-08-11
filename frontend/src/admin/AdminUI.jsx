import Icon from '../components/Icon.jsx'
import { label } from './api.js'

/** Page header shared by every admin view. */
export function AdminHead({ title, subtitle, count, onReload }) {
  return (
    <header className="page-head admin-head">
      <div>
        <h1>
          {title}
          {count != null && <span className="count">{count}</span>}
        </h1>
        {subtitle && <p className="subtitle">{subtitle}</p>}
      </div>
      {onReload && (
        <button type="button" className="secondary" onClick={onReload}>
          Refresh
        </button>
      )}
    </header>
  )
}

/**
 * The three states every admin table shares.
 *
 * Returns null when there is data to draw, so a caller renders the table only
 * once there is something real in it — an empty table with headers reads as
 * "broken", not "nothing yet".
 */
export function TableState({ loading, error, rows, empty, onReload }) {
  if (loading) {
    return (
      <div className="admin-state" role="status">
        <span className="spinner" aria-hidden="true" />
        <p>Loading…</p>
      </div>
    )
  }

  if (error) {
    return (
      <div className="error" role="alert">
        <Icon.AlertTriangle />
        <div>
          <p>{error}</p>
          {onReload && (
            <button type="button" className="link-button" onClick={onReload}>
              Try again
            </button>
          )}
        </div>
      </div>
    )
  }

  if (!rows || rows.length === 0) {
    return (
      <div className="empty">
        <span className="empty-icon">
          <Icon.Inbox />
        </span>
        <strong>{empty?.title || 'Nothing here yet'}</strong>
        {empty?.body && <p>{empty.body}</p>}
      </div>
    )
  }

  return null
}

/** Status pill reusing the app-wide .status-* colours. */
export function Status({ value }) {
  if (!value) return <span className="muted">—</span>
  return <span className={`status status-${value}`}>{label(value)}</span>
}

/**
 * Horizontal scroll container for wide tables.
 *
 * The ledger has ten columns and will not fold onto a phone; letting the table
 * scroll inside its own box keeps the page itself from scrolling sideways.
 */
export function TableScroll({ children }) {
  return <div className="table-scroll">{children}</div>
}
