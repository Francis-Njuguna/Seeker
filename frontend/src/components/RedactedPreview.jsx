import { authHeaders, useAuth } from '../auth.jsx'
import { useEffect, useState } from 'react'

const label = (value) => (value || '').replace(/_/g, ' ')

/**
 * The only view of a found document a seeker sees before their claim is
 * approved. Fields arrive already redacted from the server; this component
 * never has the raw values.
 */
export default function RedactedPreview({ preview, children }) {
  const { token } = useAuth()
  const [imageSrc, setImageSrc] = useState(null)

  // The blurred image endpoint requires auth, so fetch it as a blob rather
  // than putting the URL straight in src.
  useEffect(() => {
    if (!preview.blurred_image_url || !token) return
    let revoke
    fetch(preview.blurred_image_url, { headers: authHeaders(token) })
      .then((r) => (r.ok ? r.blob() : Promise.reject()))
      .then((blob) => {
        revoke = URL.createObjectURL(blob)
        setImageSrc(revoke)
      })
      .catch(() => {})
    return () => revoke && URL.revokeObjectURL(revoke)
  }, [preview.blurred_image_url, token])

  return (
    <div className="card preview-card">
      <div className="preview-header">
        <span className="doc-type">{label(preview.document_type)}</span>
        <span className={`status status-${preview.status}`}>
          {label(preview.status)}
        </span>
      </div>

      <div className="preview-body">
        {imageSrc ? (
          <img src={imageSrc} alt="Blurred document preview" className="blurred-thumb" />
        ) : (
          <div className="blurred-thumb placeholder">No photo</div>
        )}

        <dl className="redacted-fields">
          <dt>Name</dt>
          <dd className="redacted">{preview.holder_name_redacted}</dd>
          <dt>ID number</dt>
          <dd className="redacted">{preview.id_number_redacted}</dd>
          {preview.found_location && (
            <>
              <dt>Found at</dt>
              <dd>{preview.found_location}</dd>
            </>
          )}
          <dt>Posted</dt>
          <dd>{new Date(preview.created_at).toLocaleDateString()}</dd>
        </dl>
      </div>

      <p className="redaction-note">
        Details are hidden to protect the owner. Only the last two characters of
        the ID number are shown.
      </p>

      {children}
    </div>
  )
}
