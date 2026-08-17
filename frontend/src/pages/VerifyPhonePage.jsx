import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { authHeaders, useAuth } from '../auth.jsx'
import { AuthLayout } from '../components/Brand.jsx'
import Icon from '../components/Icon.jsx'

export default function VerifyPhonePage() {
  const { token, user, refreshUser } = useAuth()
  const navigate = useNavigate()
  const [challenge, setChallenge] = useState(null)
  const [code, setCode] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [phone, setPhone] = useState(user?.phone_number || '')

  const requestCode = async () => {
    setBusy(true)
    setError('')
    try {
      const response = await fetch('/api/auth/otp/request', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders(token) },
        body: JSON.stringify({ phone_number: phone, purpose: 'phone_verification' }),
      })
      const data = await response.json()
      if (!response.ok) throw new Error(data.detail || 'Could not send code')
      setChallenge(data)
      if (data.sandbox_code) setCode(data.sandbox_code)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const verify = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      const response = await fetch('/api/auth/otp/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders(token) },
        body: JSON.stringify({ challenge_id: challenge.challenge_id, code }),
      })
      const data = await response.json()
      if (!response.ok) throw new Error(data.detail || 'Verification failed')
      await refreshUser()
      navigate('/owner')
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <AuthLayout image="/img/auth-passport.webp" alt="Identity documents" quote="Verify your number before handling documents or money.">
      <form onSubmit={verify}>
        <h1>Verify your phone</h1>
        <p className="subtitle">We will send a one-time code to your Kenyan phone number.</p>
        {error && <div className="error"><Icon.AlertTriangle /><span>{error}</span></div>}
        {!challenge ? (
          <>
            <div className="field">
              <label htmlFor="verification-phone">M-Pesa phone number</label>
              <input id="verification-phone" type="tel" inputMode="tel" value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="0712 345 678" required />
            </div>
            <button type="button" className="btn-lg" onClick={requestCode} disabled={busy}>
            {busy ? 'Sending...' : 'Send verification code'}
            </button>
          </>
        ) : (
          <>
            <div className="field">
              <label htmlFor="otp">Verification code</label>
              <input id="otp" className="code" inputMode="numeric" value={code} onChange={(e) => setCode(e.target.value)} required />
            </div>
            {challenge.sandbox_code && <p className="notice warn">Sandbox code: {challenge.sandbox_code}</p>}
            <button className="btn-lg" disabled={busy}>{busy ? 'Verifying...' : 'Verify phone'}</button>
          </>
        )}
      </form>
    </AuthLayout>
  )
}
