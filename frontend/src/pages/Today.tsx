import { useAuth } from '../auth'
import { Card } from '../components/ui'

export default function Today() {
  const { branch } = useAuth()
  return (
    <Card title={`Today${branch ? ` at ${branch.name}` : ''}`}>
      <p>
        The day board, new booking and walk-in screens are next on the roadmap. Use the Setup pages on the left to
        add your rooms, beds, services, therapists and shifts first, so the board has something to show.
      </p>
    </Card>
  )
}
