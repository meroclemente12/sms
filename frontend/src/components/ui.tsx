import { cloneElement, isValidElement, useEffect, useId } from 'react'
import type { ReactElement, ReactNode } from 'react'
import { STATUS_LABEL } from '../api'
import type { BookingStatus } from '../api'

export function ErrorNote({ message }: { message: string | null }) {
  if (!message) return null
  return (
    <p className="error" role="alert">
      {message}
    </p>
  )
}

export function Card({ title, actions, children }: { title: string; actions?: ReactNode; children: ReactNode }) {
  return (
    <section className="card">
      <header className="card-head">
        <h2>{title}</h2>
        {actions}
      </header>
      {children}
    </section>
  )
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  // Point the label at the control by id: wrapping a <select> in its <label> would make
  // screen readers read every option as part of the field's name.
  const id = useId()
  const control = isValidElement(children)
    ? cloneElement(children as ReactElement<{ id?: string }>, { id })
    : children
  return (
    <div className="field">
      <label className="field-label" htmlFor={id}>
        {label}
      </label>
      {control}
      {hint && <span className="field-hint">{hint}</span>}
    </div>
  )
}

export function StatusPill({ active }: { active: boolean }) {
  return <span className={active ? 'pill pill-on' : 'pill pill-off'}>{active ? 'Active' : 'Inactive'}</span>
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>
}

export function ReadOnlyNote({ show }: { show: boolean }) {
  if (!show) return null
  return <p className="note">You can view this list. A manager or admin can change it.</p>
}

/** A panel that slides in from the right. Escape or the × closes it. */
export function Drawer({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <div className="drawer-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <aside className="drawer" role="dialog" aria-modal="true" aria-label={title}>
        <header className="drawer-head">
          <h2>{title}</h2>
          <button className="ghost close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </header>
        <div className="drawer-body">{children}</div>
      </aside>
    </div>
  )
}

export function StatusBadge({ status }: { status: BookingStatus }) {
  return <span className={`badge st-${status}`}>{STATUS_LABEL[status]}</span>
}
