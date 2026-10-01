import { Icon } from '../ui/Icon'
import { useTheme } from './ThemeProvider'

/** Shows the icon of the theme currently in effect (as the reference does); click switches. */
export function ThemeToggle() {
  const { theme, toggle } = useTheme()
  return (
    <button
      type="button"
      className="tb-icon"
      onClick={toggle}
      aria-label="Toggle Theme"
      title={theme === 'dark' ? 'Switch To Light Theme' : 'Switch To Dark Theme'}
    >
      <Icon name={theme === 'dark' ? 'moon' : 'sun'} size={17} />
    </button>
  )
}
