/** Quay-crane line drawing used in empty and error states (from the reference). */
export function Crane({ width = 88, height = 72 }: { width?: number; height?: number }) {
  return (
    <svg className="crane" width={width} height={height} viewBox="0 0 88 72" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M10 64h68" />
      <path d="M20 64V20h8v44" />
      <path d="M24 20 68 10" />
      <path d="M24 24h40" />
      <path d="M60 24v8" />
      <path d="M54 32h12v8H54z" />
      <path d="M28 64v-14h16v14" />
      <circle cx="24" cy="20" r="2.5" />
    </svg>
  )
}
