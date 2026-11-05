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

export async function getStatCondos(name, jurisdiction, value) {
  const query = new URLSearchParams({ jurisdiction, value })
  const response = await fetch(`/stats/${name}/condos?${query}`)
  if (!response.ok) throw new Error(`condo list failed: ${response.status}`)
  return response.json()
}

// The admin page's loads; the admin password goes in its own header (none is needed on your Mac)
export async function getLoads(password) {
  const response = await fetch('/admin/loads', { headers: password ? { 'X-Admin-Password': password } : {} })
  if (response.status === 403) throw new Error('password')
  if (!response.ok) throw new Error(`loads failed: ${response.status}`)
  return response.json()
}