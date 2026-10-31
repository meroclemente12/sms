import './App.css'

const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

function App() {
  return (
    <main className="shell">
      <h1>Banahaw Front Desk</h1>
      <p>
        The day board, booking and walk-in screens are tracked as Phase 1 issues. This app
        talks to the API at <code>{API_URL}</code>.
      </p>
    </main>
  )
}

export default App
