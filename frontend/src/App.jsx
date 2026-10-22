import { useEffect, useState } from 'react'
import './App.css'
import ReportView from './ReportView.jsx'
import SearchBox from './SearchBox.jsx'
import StatsPage from './StatsPage.jsx'

// What is on screen lives in the address (?folio=... or ?page=stats), so it can be shared or reloaded
const fromUrl = () => {
  const params = new URLSearchParams(window.location.search)
  return { folio: params.get('folio'), page: params.get('page') }
}

export default function App() {
  const [view, setView] = useState(fromUrl)

  useEffect(() => {
    const onBackButton = () => setView(fromUrl())
    window.addEventListener('popstate', onBackButton)
    return () => window.removeEventListener('popstate', onBackButton)
  }, [])

  function go(query) {
    window.history.pushState(null, '', query || '/')
    setView(fromUrl())
  }

  return (
    <main>
      <header>
        <nav>
          <a href="/" onClick={(event) => { event.preventDefault(); go(null) }}>Search</a>
          <a href="?page=stats" onClick={(event) => { event.preventDefault(); go('?page=stats') }}>Dashboard</a>
        </nav>
        <h1>CondoReadout</h1>
        <p>Recertification and risk report for Miami-Dade condos.</p>
      </header>
      {view.page === 'stats' ? (
        <StatsPage />
      ) : view.folio ? (
        <ReportView folio={view.folio} onBack={() => go(null)} />
      ) : (
        <SearchBox onPick={(row) => go(`?folio=${row.folio}`)} />
      )}
    </main>
  )
}
