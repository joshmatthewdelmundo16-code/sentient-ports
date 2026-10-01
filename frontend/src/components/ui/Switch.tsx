/** Toggle switch (reference `.switch`). Always give it a visible `label` or an `ariaLabel`. */
export function Switch({ checked, onChange, label, ariaLabel, disabled }: {
  checked: boolean
  onChange: (next: boolean) => void
  label?: string
  ariaLabel?: string
  disabled?: boolean
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label ? undefined : ariaLabel}
      disabled={disabled}
      className={`switch ${checked ? 'on' : ''}`}
      onClick={() => onChange(!checked)}
    >
      <span className="track" />
      {label ? <span className="txt">{label}</span> : null}
    </button>
  )
}
