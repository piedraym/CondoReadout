// Calls to the infoRealtor API (backend/api.py)

export async function searchCondos(query, signal) {
  const response = await fetch(`/search?q=${encodeURIComponent(query)}`, { signal })
  if (!response.ok) throw new Error(`search failed: ${response.status}`)
  return response.json()
}

export const reportUrl = (folio) => `/report/${folio}`
export const pdfUrl = (folio) => `/report/${folio}/pdf`
