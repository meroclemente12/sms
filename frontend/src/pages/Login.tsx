import { useState } from 'react'
import type { FormEvent } from 'react'
import { useAuth } from '../auth'
import { ErrorNote, Field } from '../components/ui'
import { useAction } from '../components/hooks'

export default function Login() {
  const { login } = useAuth()
  const [spa, setSpa] = useState(() => {
    try {
      return localStorage.getItem('banahaw.spa') ?? ''
    } catch {
      return ''
    }
  })
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const { busy, error, run } = useAction()

  async function submit(e: FormEvent) {
    e.preventDefault()
    const ok = await run(() => login(spa.trim(), email.trim(), password))
    if (ok) {
      try {
        localStorage.setItem('banahaw.spa', spa.trim())
      } catch {
        // ignore
      }
    }
  }

  return (
    <main className="login">
      <form className="card login-card" onSubmit={submit}>
        <h1>Front desk sign in</h1>
        <Field label="Spa code" hint="Ask your manager if you don't know it">
          <input value={spa} onChange={(e) => setSpa(e.target.value)} autoComplete="organization" required />
        </Field>
        <Field label="Email">
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" required />
        </Field>
        <Field label="Password">
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
          />
        </Field>
        <ErrorNote message={error} />
        <button className="primary" disabled={busy}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </main>
  )
}
