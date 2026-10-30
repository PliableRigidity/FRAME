import { FormEvent, useEffect, useState } from 'react'
type Asset = { path: string; kind: string; extension: string; size: number }
type Photo = { id: number; name: string; width?: number; height?: number; camera?: string; status: string; error?: string; has_thumbnail: boolean; assets: Asset[] }
type Job = { state: string; source?: string; completed: number; total: number; current_file?: string; error?: string }

export function App() {
  const [path, setPath] = useState('')
  const [job, setJob] = useState<Job>({ state: 'idle', completed: 0, total: 0 })
  const [photos, setPhotos] = useState<Photo[]>([])
  const [error, setError] = useState('')
  async function loadPhotos() { const response = await fetch('/api/photos?limit=500'); if (response.ok) setPhotos((await response.json()).items) }
  useEffect(() => { void loadPhotos() }, [])
  useEffect(() => {
    if (job.state !== 'running') return
    const timer = window.setInterval(async () => { const next = await (await fetch('/api/scans/current')).json(); setJob(next); if (next.state !== 'running') void loadPhotos() }, 500)
    return () => window.clearInterval(timer)
  }, [job.state])
  async function start(event: FormEvent) {
    event.preventDefault(); setError('')
    const response = await fetch('/api/scans', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path }) })
    const body = await response.json()
    if (!response.ok) setError(body.detail || 'Unable to start scan'); else setJob(body)
  }
  async function cancel() { setJob(await (await fetch('/api/scans/current/cancel', { method: 'POST' })).json()) }
  const progress = job.total ? Math.round(job.completed / job.total * 100) : 0
  return <main>
    <header><div><span className="eyebrow">LOCAL PHOTO CULLING</span><h1>FRAME</h1></div><p>Your photographs stay on this computer.</p></header>
    <section className="import-panel">
      <div><h2>Import a collection</h2><p>Enter a local, mounted drive, or network directory. Subdirectories are included.</p></div>
      <form onSubmit={start}><label htmlFor="path">Photo directory</label><div className="input-row"><input id="path" value={path} onChange={e => setPath(e.target.value)} placeholder="C:\Users\you\Pictures" required/><button disabled={job.state === 'running'}>Analyse</button></div></form>
      {error && <p className="error" role="alert">{error}</p>}
      {job.state !== 'idle' && <div className="progress"><div><strong>{job.state === 'running' ? `Scanning ${job.current_file || '…'}` : `Scan ${job.state}`}</strong><span>{job.completed} / {job.total}</span></div><progress max="100" value={progress}/>{job.state === 'running' && <button className="quiet" onClick={cancel}>Cancel safely</button>}{job.error && <p className="error">{job.error}</p>}</div>}
    </section>
    <section className="gallery-heading"><div><span className="eyebrow">LIBRARY</span><h2>{photos.length} logical photographs</h2></div><p>RAW + rendered pairs appear once.</p></section>
    {photos.length === 0 ? <div className="empty"><h3>Your gallery is ready for a first import.</h3><p>FRAME only reads source files. Cached previews are stored separately.</p></div> : <div className="grid">{photos.map(photo => <article key={photo.id} className="card">
      <div className="thumb">{photo.has_thumbnail ? <img src={`/api/photos/${photo.id}/thumbnail`} alt="" loading="lazy"/> : <span>RAW</span>}<span className="badge">{photo.status}</span></div>
      <div className="details"><h3>{photo.name}</h3><p>{photo.width ? `${photo.width} × ${photo.height}` : 'Preview unavailable'}{photo.camera ? ` · ${photo.camera}` : ''}</p><p>{photo.assets.map(a => a.extension.slice(1).toUpperCase()).join(' + ')}</p>{photo.error && <p className="warning">{photo.error}</p>}</div>
    </article>)}</div>}
  </main>
}
