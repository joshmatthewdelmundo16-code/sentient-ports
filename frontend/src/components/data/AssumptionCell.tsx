import type { ReactNode } from 'react'

/** One assumption, workbook-cell style (reference `.xl-cell`): a name with its source dataset
 *  underneath, and the value on the right. `cellRef` shows a worksheet reference (e.g. "B4")
 *  when one exists. */
export function AssumptionCell({ name, sub, value, detail, cellRef }: {
  name: ReactNode
  sub?: ReactNode
  value: ReactNode
  detail?: ReactNode
  cellRef?: string
}) {
  return (
    <div className="xl-cell">
      {cellRef ? <span className="xc-ref">{cellRef}</span> : null}
      <div className="xc-main">
        <div className="xc-name">{name}</div>
        {sub ? <div className="xc-sub">{sub}</div> : null}
      </div>
      <div className="xc-val">
        <b>{value}</b>
        {detail}
      </div>
    </div>
  )
}
