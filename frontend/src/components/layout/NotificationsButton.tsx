import { useState } from 'react'
import { Link } from 'react-router'
import { useActivity } from '../../api/queries'
import type { ActivityItem } from '../../api/types'
import { formatDateTime, statusLabel, statusTone, type Tone } from '../../shared/format'
import { Drawer } from '../ui/Drawer'
import { Icon, type IconName } from '../ui/Icon'

const DOT: Record<Tone, { cls: string; icon: IconName }> = {
  success: { cls: 'good', icon: 'check' },
  danger: { cls: 'err', icon: 'x' },
  warning: { cls: 'warn', icon: 'clock' },
  info: { cls: 'accent', icon: 'dot' },
  neutral: { cls: '', icon: 'dot' },
}

function Event({ item }: { item: ActivityItem }) {
  const tone = DOT[statusTone(item.status)]
  const detail = [item.context, statusLabel(item.status)].filter(Boolean).join(' · ')
  return (
    <div className="tl-item">
      <div className={`tl-dot ${tone.cls}`}><Icon name={tone.icon} size={10} /></div>
      <div className="tl-body">
        <div className="tl-top">
          <span className="tl-actor">{item.title}</span>
          <span className="tl-act">{item.subject}</span>
          <span className="tl-time">{formatDateTime(item.occurred_at)}</span>
        </div>
        <div className="tl-detail">{detail}</div>
        {item.error ? <div className="tl-detail" style={{ color: 'var(--error-strong)' }}>{item.error}</div> : null}
      </div>
    </div>
  )
}

/** Bell + drawer of the most recent real activity in this scope. The dot appears only when a
 *  recent event failed; there is no read/unread tracking, so it does not claim "new". */
export function NotificationsButton() {
  const [open, setOpen] = useState(false)
  const activity = useActivity(8)
  const items = activity.data ?? []
  const hasFailure = items.some((i) => statusTone(i.status) === 'danger')
  const close = () => setOpen(false)

  return (
    <>
      <button type="button" className="tb-icon" onClick={() => setOpen(true)} aria-label="Notifications" aria-haspopup="dialog">
        <Icon name="bell" size={17} />
        {hasFailure ? <span className="bell-dot" title="A recent run failed" /> : null}
      </button>
      <Drawer
        open={open}
        onClose={close}
        title="Notifications"
        subtitle={activity.data ? `${items.length} Recent Event${items.length === 1 ? '' : 's'} · Activity In This Scope` : 'Activity In This Scope'}
        footer={<Link className="btn" to="/activity" onClick={close}>View Full Activity <Icon name="arrowr" size={14} /></Link>}
      >
        {activity.isLoading ? (
          <div className="stack-sm" aria-busy="true">
            {[0, 1, 2, 3].map((i) => <div key={i} className="skel" style={{ height: 38 }} />)}
          </div>
        ) : activity.error ? (
          <p className="muted" role="alert">Activity Could Not Be Loaded. {activity.error instanceof Error ? activity.error.message : ''}</p>
        ) : items.length === 0 ? (
          <p className="muted">No Activity Yet. Runs, dataset changes and uploads appear here as they happen.</p>
        ) : (
          <div className="timeline">{items.map((i) => <Event key={i.id} item={i} />)}</div>
        )}
      </Drawer>
    </>
  )
}
