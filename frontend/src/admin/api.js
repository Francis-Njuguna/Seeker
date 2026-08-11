import { useCallback, useEffect, useState } from 'react'
import { authHeaders, useAuth } from '../auth.jsx'

/** The role the backend gates /api/admin/* on (UserRole.ADMIN in models.py). */
export const ADMIN_ROLE = 'admin'

export function isAdmin(user) {
  return user?.role === ADMIN_ROLE
}

/**
 * GET an admin endpoint, tracking the three states a table needs to render
 * honestly: still loading, failed, or loaded-and-possibly-empty.
 *
 * Deliberately thin — it wraps the same `fetch` + `authHeaders` every other page
 * uses rather than introducing a second API client.
 */
export function useAdminResource(path) {
  const { token } = useAuth()
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    if (!token) return
    setLoading(true)
    setError('')
    try {
      const response = await fetch(path, { headers: authHeaders(token) })
      if (!response.ok) {
        // 403 here means the token is valid but the role is not admin — worth
        // saying plainly, since it is the one error a correct fix differs for.
        const body = await response.json().catch(() => ({}))
        throw new Error(
          response.status === 403
            ? 'Your account does not have admin access.'
            : body.detail || `Request failed (${response.status})`,
        )
      }
      setData(await response.json())
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [path, token])

  useEffect(() => {
    load()
  }, [load])

  return { data, error, loading, reload: load }
}

/** Minor units to a display string: 20000 KES -> "KES 200.00". */
export function formatMoney(minor, currency = 'KES') {
  if (minor == null) return '—'
  return `${currency} ${(minor / 100).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`
}

export function formatDateTime(value) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString()
}

/** Backend enums are snake_case; the UI shows them spaced. */
export function label(value) {
  return (value || '').replace(/_/g, ' ')
}
