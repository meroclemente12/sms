import { cloneElement, isValidElement, useId } from 'react'
import type { ReactElement, ReactNode } from 'react'

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
