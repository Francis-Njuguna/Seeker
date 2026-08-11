/**
 * Icon set.
 *
 * Hand-drawn on a 24x24 grid with a 1.7 stroke, round caps and joins. They are
 * inline SVG rather than a font or an icon package because there are only
 * fifteen of them and shipping a dependency for that is not worth it — and
 * inline means each one inherits `currentColor` from whatever it sits in.
 *
 * Every icon takes the same props, so they are interchangeable at call sites:
 *   <Icon.Search />               default 1em, inherits colour
 *   <Icon.Search size={18} />
 */

const base = {
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.7,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
  'aria-hidden': 'true',
  focusable: 'false',
}

function Svg({ size, children, ...rest }) {
  return (
    <svg {...base} width={size || '1em'} height={size || '1em'} {...rest}>
      {children}
    </svg>
  )
}

/* A found ID card: a card outline with a portrait block and two text lines. */
const IdCard = (p) => (
  <Svg {...p}>
    <rect x="2.5" y="5" width="19" height="14" rx="2.5" />
    <circle cx="8.5" cy="11" r="2" />
    <path d="M5.4 16.2c.5-1.4 1.7-2.2 3.1-2.2s2.6.8 3.1 2.2" />
    <path d="M15 10.5h4M15 13.8h2.5" />
  </Svg>
)

const Search = (p) => (
  <Svg {...p}>
    <circle cx="10.5" cy="10.5" r="6.5" />
    <path d="m15.4 15.4 4.1 4.1" />
  </Svg>
)

const Bell = (p) => (
  <Svg {...p}>
    <path d="M18 8.8a6 6 0 1 0-12 0c0 4.2-1.5 5.5-1.5 5.5h15S18 13 18 8.8Z" />
    <path d="M13.7 18a2 2 0 0 1-3.4 0" />
  </Svg>
)

const Handshake = (p) => (
  <Svg {...p}>
    <path d="M11 6.5 8.6 8.9a1.8 1.8 0 0 0 2.5 2.5l1.4-1.4 3.4 3.4" />
    <path d="m13 6.5 5 5M6 11l-3.5 3.5M21.5 14.5 18 11" />
    <path d="M11 6.5 8 5 2.5 9.5M13 6.5 16 5l5.5 4.5" />
    <path d="m12.5 10 3.9 3.9a1.6 1.6 0 0 1-2.3 2.3l-.6-.6" />
  </Svg>
)

/* Shield with a check — used for anything about verification. */
const ShieldCheck = (p) => (
  <Svg {...p}>
    <path d="M12 2.8 4.5 5.8v5.5c0 4.4 3.1 8.4 7.5 9.9 4.4-1.5 7.5-5.5 7.5-9.9V5.8Z" />
    <path d="m9 11.8 2.2 2.2L15.2 10" />
  </Svg>
)

const Lock = (p) => (
  <Svg {...p}>
    <rect x="4.5" y="10" width="15" height="10.5" rx="2.2" />
    <path d="M8 10V7.2a4 4 0 0 1 8 0V10" />
  </Svg>
)

const Eye = (p) => (
  <Svg {...p}>
    <path d="M2.2 12S5.6 5.8 12 5.8 21.8 12 21.8 12 18.4 18.2 12 18.2 2.2 12 2.2 12Z" />
    <circle cx="12" cy="12" r="2.8" />
  </Svg>
)

const Camera = (p) => (
  <Svg {...p}>
    <path d="M3.5 8.5h3l1.5-2.2h8L17.5 8.5h3a1.5 1.5 0 0 1 1.5 1.5v8a1.5 1.5 0 0 1-1.5 1.5h-17A1.5 1.5 0 0 1 2 18v-8a1.5 1.5 0 0 1 1.5-1.5Z" />
    <circle cx="12" cy="13.5" r="3.4" />
  </Svg>
)

const Upload = (p) => (
  <Svg {...p}>
    <path d="M12 15.5V3.8M8.2 7.6 12 3.8l3.8 3.8" />
    <path d="M3.8 15v3.6a2 2 0 0 0 2 2h12.4a2 2 0 0 0 2-2V15" />
  </Svg>
)

const Check = (p) => (
  <Svg {...p}>
    <path d="m4.5 12.5 5 5 10-11" />
  </Svg>
)

/* Circled check, for the terminal "returned" state. */
const CheckCircle = (p) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="9.2" />
    <path d="m8 12.2 2.8 2.8L16.2 9.5" />
  </Svg>
)

const AlertTriangle = (p) => (
  <Svg {...p}>
    <path d="M12 3.8 2.6 19.5h18.8Z" />
    <path d="M12 9.5v4.2M12 17h.01" />
  </Svg>
)

const Info = (p) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="9.2" />
    <path d="M12 11.2v5M12 7.8h.01" />
  </Svg>
)

const ArrowRight = (p) => (
  <Svg {...p}>
    <path d="M4.2 12h15.4M13.8 6.2 19.6 12l-5.8 5.8" />
  </Svg>
)

const X = (p) => (
  <Svg {...p}>
    <path d="M6 6l12 12M18 6 6 18" />
  </Svg>
)

const Inbox = (p) => (
  <Svg {...p}>
    <path d="M3 13.5h4.5l1.5 2.6h6l1.5-2.6H21" />
    <path d="M5.4 4.5h13.2l2.4 9v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-5Z" />
  </Svg>
)

const MapPin = (p) => (
  <Svg {...p}>
    <path d="M12 21.2s7-5.6 7-11a7 7 0 1 0-14 0c0 5.4 7 11 7 11Z" />
    <circle cx="12" cy="10" r="2.6" />
  </Svg>
)

const Icon = {
  IdCard,
  Search,
  Bell,
  Handshake,
  ShieldCheck,
  Lock,
  Eye,
  Camera,
  Upload,
  Check,
  CheckCircle,
  AlertTriangle,
  Info,
  ArrowRight,
  X,
  Inbox,
  MapPin,
}

export default Icon
