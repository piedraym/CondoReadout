// Calls to the CondoReadout API (backend/api.py)

export async function searchCondos(query, signal) {
  const response = await fetch(`/search?q=${encodeURIComponent(query)}`, { signal })
  if (!response.ok) throw new Error(`search failed: ${response.status}`)
  return response.json()
}

export const reportUrl = (folio) => `/report/${folio}`
export const pdfUrl = (folio) => `/report/${folio}/pdf`

export async function getStats() {
  const response = await fetch('/stats')
  if (!response.ok) throw new Error(`stats failed: ${response.status}`)
  return response.json()
}

export async function getStatCondos(name, jurisdiction, light) {
  const query = new URLSearchParams({ jurisdiction, light })
  const response = await fetch(`/stats/${name}/condos?${query}`)
  if (!response.ok) throw new Error(`condo list failed: ${response.status}`)
  return response.json()
}
