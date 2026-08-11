import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth.jsx'
import { AuthLayout } from '../components/Brand.jsx'
import Icon from '../components/Icon.jsx'

export default function RegisterPage() {
  const [form, setForm] = useState({ email: '', password: '', display_name: '' })
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const { login } = useAuth()
  const navigate = useNavigate()

  const update = (key) => (e) => setForm({ ...form, [key]: e.target.value })

  const submit = async (e) => {
    e.preventDefault()
    setError('')
    setBusy(true)
    try {
      const response = await fetch('/api/auth/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(form),
      })
      const data = await response.json()
      if (!response.ok) {
        throw new Error(
          typeof data.detail === 'string'
            ? data.detail
            : 'Check your details and try again',
        )
      }
      login(data.access_token, data.user)
      navigate('/')
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <AuthLayout
      image="/img/auth-documents.webp"
      alt="A passport and boarding pass on a dark surface"
      quote={
        <>
          The person who found it <em>is looking for you.</em>
        </>
      }
      attribution="Post what you found, search for what you lost, or leave an alert and we will tell you the moment it turns up."
    >
      <form onSubmit={submit}>
        <h1>Create an account</h1>
        <p className="subtitle">It takes about thirty seconds.</p>

        {error && (
          <div className="error" role="alert">
            <Icon.AlertTriangle />
            <span>{error}</span>
          </div>
        )}

        <div className="field">
          <label htmlFor="display_name">Name</label>
          <input
            id="display_name"
            autoComplete="name"
            placeholder="Your name"
            value={form.display_name}
            onChange={update('display_name')}
            required
          />
        </div>

        <div className="field">
          <label htmlFor="email">Email</label>
          <input
            id="email"
            type="email"
            autoComplete="email"
            placeholder="you@example.com"
            value={form.email}
            onChange={update('email')}
            required
          />
        </div>

        <div className="field">
          <label htmlFor="password">Password</label>
          <input
            id="password"
            type="password"
            autoComplete="new-password"
            placeholder="••••••••"
            value={form.password}
            onChange={update('password')}
            minLength={8}
            required
          />
          <span className="hint">At least 8 characters.</span>
        </div>

        <button type="submit" className="btn-lg" disabled={busy}>
          {busy ? 'Creating account…' : 'Create account'}
        </button>

        <p className="auth-alt">
          Already registered? <Link to="/login">Sign in</Link>
        </p>
      </form>
    </AuthLayout>
  )
}
