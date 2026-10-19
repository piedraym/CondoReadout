import { useEffect, useState } from 'react'
import './App.css'
import ReportView from './ReportView.jsx'
import SearchBox from './SearchBox.jsx'

// The chosen condo lives in the address (?folio=...), so a report can be shared or reloaded
const folioInUrl = () => new URLSearchParams(window.location.search).get('folio')

export default function App() {
  const [folio, setFolio] = useState(folioInUrl)

  useEffect(() => {
    const onBackButton = () => setFolio(folioInUrl())
    window.addEventListener('popstate', onBackButton)
    return () => window.removeEventListener('popstate', onBackButton)
  }, [])

  function show(newFolio) {
    window.history.pushState(null, '', newFolio ? `?folio=${newFolio}` : '/')
    setFolio(newFolio)
  }

  return (
    <main>
      <header>
        <h1>CondoReadout</h1>
        <p>Recertification and risk report for Miami-Dade condos.</p>
      </header>
      {folio ? (
        <ReportView folio={folio} onBack={() => show(null)} />
      ) : (
        <SearchBox onPick={(row) => show(row.folio)} />
      )}
    </main>
  )
}
