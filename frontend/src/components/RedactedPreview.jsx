import { authHeaders, useAuth } from '../auth.jsx'
import { useEffect, useState } from 'react'
import Icon from './Icon.jsx'

const label = (value) => (value || '').replace(/_/g, ' ')

/**
 * A found document, redacted by default.
 *
 * When `full` is supplied the server has decided this viewer is entitled to the
 * whole record (the finder, or a seeker whose claim was approved), so the
 * unblurred photo and real field values are shown instead. The component never
 * unredacts anything itself: without `full` it has no access to the raw values.
 */
export default function RedactedPreview({ preview, full, children }) {
  const { token } = useAuth()
  const [imageSrc, setImageSrc] = useState(null)

  const imageUrl = full?.image_url || preview.blurred_image_url
  const unblurred = Boolean(full?.image_url)

  // Both image endpoints require auth, so fetch as a blob rather than putting
  // the URL straight in src.
  useEffect(() => {
    if (!imageUrl || !token) {
      setImageSrc(null)
      return
    }
    let revoke
    let cancelled = false
    fetch(imageUrl, { headers: authHeaders(token) })
      .then((r) => (r.ok ? r.blob() : Promise.reject()))
      .then((blob) => {
        if (cancelled) return
        revoke = URL.createObjectURL(blob)
        setImageSrc(revoke)
      })
      .catch(() => {})
    return () => {
      cancelled = true
      if (revoke) URL.revokeObjectURL(revoke)
    }
  }, [imageUrl, token])

  return (
    <div className="card preview-card">
      <div className="preview-header">
        <span className="doc-type">
          <Icon.IdCard />
          {label(full?.document_type || preview.document_type)}
        </span>
        <span className={`status status-${preview.status}`}>
          {label(preview.status)}
        </span>
      </div>

      <div className="preview-body">
        {imageSrc ? (
          <div className={`thumb-frame ${unblurred ? 'is-unlocked' : 'is-locked'}`}>
            <img
              src={imageSrc}
              alt={unblurred ? 'Document photo' : 'Blurred document preview'}
              className={unblurred ? 'full-thumb' : 'blurred-thumb'}
            />
            <span className="thumb-badge">
              {unblurred ? <Icon.Eye /> : <Icon.Lock />}
              {unblurred ? 'Full photo' : 'Blurred'}
            </span>
          </div>
        ) : (
          <div className="thumb-frame is-empty">
            <Icon.Camera />
            No photo
          </div>
        )}

        {full ? (
          <dl className="redacted-fields">
            <dt>Name</dt>
            <dd>{full.holder_name}</dd>
            <dt>ID number</dt>
            <dd>{full.id_number}</dd>
            {full.found_location && (
              <>
                <dt>Found at</dt>
                <dd>{full.found_location}</dd>
              </>
            )}
            {full.found_note && (
              <>
                <dt>Note</dt>
                <dd>{full.found_note}</dd>
              </>
            )}
            <dt>Posted</dt>
            <dd className="tabular">
              {new Date(full.created_at).toLocaleDateString()}
            </dd>
          </dl>
        ) : (
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
            <dd className="tabular">
              {new Date(preview.created_at).toLocaleDateString()}
            </dd>
          </dl>
        )}
      </div>

      {!full && (
        <p className="redaction-note">
          <Icon.ShieldCheck />
          <span>
            Details are hidden to protect the owner. Only the last two characters
            of the ID number are shown.
          </span>
        </p>
      )}

      {children}
    </div>
  )
}
