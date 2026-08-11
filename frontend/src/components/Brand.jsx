import Icon from './Icon.jsx'

/**
 * The Seeker wordmark.
 *
 * The mark is a document card with a magnifier over it — a found ID being
 * looked at, which is the whole product in one glyph. Drawn here rather than
 * pulled from Icon.jsx because it is a logo, not an icon: it has its own
 * two-weight construction and should not drift when the icon set changes.
 */
export default function Brand({ as: Tag = 'span', ...rest }) {
  return (
    <Tag className="brand" {...rest}>
      <span className="brand-mark" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor">
          <rect
            x="2.6"
            y="4.4"
            width="14.4"
            height="11"
            rx="2"
            strokeWidth="1.8"
            opacity="0.55"
          />
          <path
            d="M6.2 8.2h7M6.2 11.4h3.4"
            strokeWidth="1.8"
            strokeLinecap="round"
            opacity="0.55"
          />
          <circle cx="15" cy="14.4" r="5" strokeWidth="2" fill="none" />
          <path d="m18.7 18.1 2.9 2.9" strokeWidth="2.2" strokeLinecap="round" />
        </svg>
      </span>
      Seeker
    </Tag>
  )
}

/**
 * The dark, photographic half of the sign-in and sign-up screens.
 *
 * Below 900px the photo collapses from a column to a banner (handled in CSS)
 * so the form stays above the fold on a phone.
 */
export function AuthLayout({ image, alt, quote, attribution, children }) {
  return (
    <div className="auth-shell">
      <aside className="auth-visual">
        <img src={image} alt={alt} />
        <Brand />

        <div className="auth-visual-copy">
          <p className="auth-quote">{quote}</p>
          <p className="auth-attribution">{attribution}</p>

          <dl className="auth-facts">
            <div className="auth-fact">
              <dt>Redacted</dt>
              <dd>Details stay hidden until a claim is approved</dd>
            </div>
            <div className="auth-fact">
              <dt>Verified</dt>
              <dd>Claimants prove the document is theirs</dd>
            </div>
          </dl>
        </div>
      </aside>

      <main className="auth-pane">
        <div className="auth-card">
          {children}
          <p className="auth-legal">
            <Icon.Lock />
            Uploaded documents are stored privately and shown blurred and redacted
            to everyone but a verified owner.
          </p>
        </div>
      </main>
    </div>
  )
}
