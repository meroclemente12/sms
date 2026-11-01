import { useState } from 'react'
import type { FormEvent } from 'react'
import { api } from '../../api'
import type { Channel } from '../../api'
import { useAuth } from '../../auth'
import { Card, ErrorNote, Field, StatusPill } from '../../components/ui'
import { useAction, useLoad } from '../../components/hooks'

const BUILT_IN = new Set(['phone', 'email', 'walk_in'])

function ChannelRow({ ch, canEdit, onDone }: { ch: Channel; canEdit: boolean; onDone: () => void }) {
  const { busy, error, run } = useAction()
  async function toggle(active: boolean) {
    if (await run(() => api(`/channels/${ch.id}`, { method: 'PUT', json: { code: ch.code, name: ch.name, active } })))
      onDone()
  }
  return (
    <tr>
      <td>
        {ch.name}
        {BUILT_IN.has(ch.code) && <span className="muted small"> · built in</span>}
      </td>
      <td>
        {canEdit ? (
          <label className="check">
            <input type="checkbox" checked={ch.active} disabled={busy} onChange={(e) => void toggle(e.target.checked)} /> In use
          </label>
        ) : (
          <StatusPill active={ch.active} />
        )}
        <ErrorNote message={error} />
      </td>
    </tr>
  )
}

export default function ChannelsPage() {
  const { isAdmin } = useAuth()
  const { data, error, reload } = useLoad(() => api<Channel[]>('/channels'), [])
  const [name, setName] = useState('')
  const add = useAction()

  async function submit(e: FormEvent) {
    e.preventDefault()
    const code = name
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '_')
      .replace(/^_|_$/g, '')
    if (await add.run(() => api('/channels', { method: 'POST', json: { code, name: name.trim() } }))) {
      setName('')
      void reload()
    }
  }

  return (
    <div className="stack">
      <h1>Booking channels</h1>
      <Card title="How guests book">
        <p className="muted">
          Every booking records one of these, so you can later see how many guests call, email or walk in.
        </p>
        <ErrorNote message={error} />
        <table className="table">
          <tbody>
            {data?.map((ch) => (
              <ChannelRow key={ch.id} ch={ch} canEdit={isAdmin} onDone={() => void reload()} />
            ))}
          </tbody>
        </table>
        {isAdmin && (
          <form className="inline-form" onSubmit={submit}>
            <Field label="New channel" hint="For example Messenger or Hotel partner">
              <input value={name} onChange={(e) => setName(e.target.value)} required />
            </Field>
            <div className="form-actions">
              <button className="primary" disabled={add.busy}>
                Add
              </button>
            </div>
            <ErrorNote message={add.error} />
          </form>
        )}
      </Card>
    </div>
  )
}
