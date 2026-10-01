/** Sentient Ports mark (anchor in a rounded square), from the reference. Uses theme tokens, so it follows dark/light. */
export function BrandMark({ size = 26 }: { size?: number }) {
  return (
    <svg className="brand-mark" width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden="true">
      <rect x="1" y="1" width="30" height="30" rx="7" fill="var(--accent-soft)" stroke="var(--accent-line)" />
      <path d="M16 6v15" stroke="var(--accent)" strokeWidth="2" strokeLinecap="round" />
      <circle cx="16" cy="6.5" r="2.2" fill="none" stroke="var(--accent)" strokeWidth="2" />
      <path d="M8 14a8 8 0 0 0 16 0" fill="none" stroke="var(--accent)" strokeWidth="2" strokeLinecap="round" />
      <path d="M6 14h4M22 14h4" stroke="var(--accent)" strokeWidth="2" strokeLinecap="round" />
    </svg>
  )
}
