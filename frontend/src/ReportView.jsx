import { useState } from 'react'
import { pdfUrl, reportUrl } from './api.js'

// The report as the API renders it, with a button for the PDF
export default function ReportView({ folio, onBack }) {
  const [height, setHeight] = useState(1100)

  // the page comes from the same address, so its height can be read to avoid a second scroll bar
  function fitHeight(event) {
    const page = event.target.contentDocument
    if (page) setHeight(page.documentElement.scrollHeight)
  }

  return (
    <section className="report">
      <div className="report-bar">
        <button onClick={onBack}>← New search</button>
        <a className="pdf" href={pdfUrl(folio)} target="_blank" rel="noreferrer">
          Download PDF
        </a>
      </div>
      <iframe
        title={`Report for folio ${folio}`}
        src={reportUrl(folio)}
        style={{ height }}
        onLoad={fitHeight}
      />
    </section>
  )
}
