import { useState } from 'react'
import { authHeaders, useAuth } from '../auth.jsx'

const DOCUMENT_TYPES = [
  'national_id',
  'passport',
  'driver_license',
  'residence_permit',
  'student_id',
  'health_card',
  'social_security',
  'other',
]

const label = (value) => value.replace(/_/g, ' ')

export default function PostFoundPage() {
  const { token } = useAuth()
  const [file, setFile] = useState(null)
  const [preview, setPreview] = useState(null)
  const [ocr, setOcr] = useState(null)
  const [scanning, setScanning] = useState(false)
  const [form, setForm] = useState({
    document_type: 'national_id',
    holder_name: '',
    id_number: '',
    found_location: '',
    found_note: '',
  })
  const [error, setError] = useState('')
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)

  const update = (key) => (e) => setForm({ ...form, [key]: e.target.value })

  const onFileChange = (e) => {
    const selected = e.target.files?.[0] || null
    setFile(selected)
    setOcr(null)
    setError('')
    setPreview(selected ? URL.createObjectURL(selected) : null)
  }

  // Step 1: OCR suggests values. Nothing is stored yet — the finder confirms
  // or corrects them before the record is created.
  const scan = async () => {
    if (!file) return
    setScanning(true)
    setError('')
    try {
      const body = new FormData()
      body.append('image', file)
      const response = await fetch('/api/found-ids/ocr-preview', {
        method: 'POST',
        headers: authHeaders(token),
        body,
      })
      const data = await response.json()
      if (!response.ok) throw new Error(data.detail || 'Scan failed')
      setOcr(data)
      setForm((prev) => ({
        ...prev,
        document_type:
          data.document_type && data.document_type !== 'unknown'
            ? data.document_type
            : prev.document_type,
        holder_name: data.holder_name || prev.holder_name,
        id_number: data.id_number || prev.id_number,
      }))
    } catch (err) {
      setError(err.message)
    } finally {
      setScanning(false)
    }
  }

  // Step 2: submit the confirmed fields.
  const submit = async (e) => {
    e.preventDefault()
    setError('')
    setBusy(true)
    try {
      const body = new FormData()
      Object.entries(form).forEach(([k, v]) => body.append(k, v ?? ''))
      body.append('run_ocr', file ? 'true' : 'false')
      if (file) body.append('image', file)

      const response = await fetch('/api/found-ids', {
        method: 'POST',
        headers: authHeaders(token),
        body,
      })
      const data = await response.json()
      if (!response.ok) {
        throw new Error(
          typeof data.detail === 'string' ? data.detail : 'Could not post this ID',
        )
      }
      setResult(data)
      setForm({
        document_type: 'national_id',
        holder_name: '',
        id_number: '',
        found_location: '',
        found_note: '',
      })
      setFile(null)
      setPreview(null)
      setOcr(null)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  if (result) {
    return (
      <div className="page">
        <div className="card success-card">
          <h1>Posted</h1>
          <p>
            Thanks for posting this {label(result.document_type)}. If anyone has
            registered a lost-ID alert for it, they have been notified already.
          </p>
          <p className="muted">
            The details you entered stay hidden from other users. Seekers only
            see a redacted preview until you approve their claim.
          </p>
          <button type="button" onClick={() => setResult(null)}>
            Post another
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="page">
      <h1>Post a found ID</h1>
      <p className="subtitle">
        Upload a photo to auto-fill the fields, then check them before posting.
      </p>

      {error && <div className="error">{error}</div>}

      <form className="card" onSubmit={submit}>
        <fieldset>
          <legend>1. Photo (optional)</legend>
          <input type="file" accept="image/*" onChange={onFileChange} />
          {preview && (
            <div className="preview-row">
              <img src={preview} alt="Selected document" className="thumb" />
              <button type="button" onClick={scan} disabled={scanning}>
                {scanning ? 'Scanning...' : 'Scan with OCR'}
              </button>
            </div>
          )}
          {ocr && (
            <div className={ocr.needs_review ? 'notice warn' : 'notice ok'}>
              {ocr.needs_review
                ? 'OCR was unsure about some fields. Please check every value below.'
                : 'OCR filled the fields below. Please confirm they are correct.'}
              {ocr.ocr_confidence != null && (
                <span className="muted">
                  {' '}
                  (confidence {Math.round(ocr.ocr_confidence)}%)
                </span>
              )}
            </div>
          )}
        </fieldset>

        <fieldset>
          <legend>2. Confirm the details</legend>
          <label>
            Document type
            <select value={form.document_type} onChange={update('document_type')}>
              {DOCUMENT_TYPES.map((t) => (
                <option key={t} value={t}>
                  {label(t)}
                </option>
              ))}
            </select>
          </label>
          <label>
            Name on the document
            <input value={form.holder_name} onChange={update('holder_name')} required />
          </label>
          <label>
            ID number
            <input value={form.id_number} onChange={update('id_number')} required />
            <span className="hint">
              Spacing, dashes and case do not matter for matching.
            </span>
          </label>
          <label>
            Where you found it
            <input
              value={form.found_location}
              onChange={update('found_location')}
              placeholder="e.g. Central Station, platform 4"
            />
          </label>
          <label>
            Note for the owner (optional)
            <textarea value={form.found_note} onChange={update('found_note')} rows={3} />
          </label>
        </fieldset>

        <button type="submit" disabled={busy}>
          {busy ? 'Posting...' : 'Post found ID'}
        </button>
      </form>
    </div>
  )
}
