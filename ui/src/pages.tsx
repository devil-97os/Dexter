import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { Activity, ArrowRight, AudioLines, BrainCircuit, Check, CheckCircle2, ChevronRight, CircleHelp, Clipboard, Clock3, Code2, Cpu, Database, FileCode2, FileSearch, FolderOpen, Globe2, LoaderCircle, LockKeyhole, Mic, Plus, Radio, RefreshCw, Search, Send, Settings2, ShieldAlert, ShieldCheck, Sparkles, Square, Trash2, Volume2, Wifi, X } from 'lucide-react'
import { api, type Message } from './api'
import DexterFace, { type FaceState } from './DexterFace'
import { useApp } from './App'

export function PageHeader({ eyebrow, title, description, action }: { eyebrow: string; title: string; description: string; action?: React.ReactNode }) {
  return <div className="page-heading"><div><div className="eyebrow">{eyebrow}</div><h1>{title}</h1><p>{description}</p></div>{action && <div className="page-heading-action">{action}</div>}</div>
}

function NumberCard({ label, value, detail, icon: Icon, tone = 'cyan' }: { label: string; value: React.ReactNode; detail: string; icon: any; tone?: string }) {
  return <div className="number-card"><div className={`number-icon ${tone}`}><Icon size={18} /></div><div className="number-label">{label}</div><div className="number-value">{value}</div><div className="number-detail">{detail}</div></div>
}

function StatusPill({ children, state = 'neutral' }: { children: React.ReactNode; state?: string }) {
  return <span className={`status-pill ${state}`}><i />{children}</span>
}

function Empty({ icon: Icon = CircleHelp, title, text }: { icon?: any; title: string; text: string }) {
  return <div className="empty-state"><div className="empty-icon"><Icon size={19} /></div><strong>{title}</strong><p>{text}</p></div>
}

export function HomePage() {
  const { appState, faceState } = useApp()
  return <>
    <PageHeader eyebrow="PERSONAL AI · LOCAL INSTANCE" title="Good to see you, Devil." description="Dexter is ready when you are. Your local tools and connected providers are summarized below." action={<Link className="button primary" to="/chat"><Sparkles size={16} /> Open Dexter Chat <ArrowRight size={15} /></Link>} />
    <section className="home-hero panel">
      <div className="home-hero-copy"><div className="hero-label"><span className="pulse-mark" /> DEXTER · PERSONAL ASSISTANT</div><h2>A clear mind for<br /><span>complicated work.</span></h2><p>Technical exploration, useful conversation, and careful local actions. Ask naturally; confirm every consequential file change.</p><div className="hero-actions"><Link to="/chat" className="button primary"><Sparkles size={16} /> Start a conversation</Link><Link to="/files" className="button secondary"><FileSearch size={16} /> Find something</Link></div><div className="hero-caption"><i className="status-dot idle" /> {faceState === 'idle' ? 'Standing by · push-to-talk only' : `Currently ${faceState}`}</div></div>
      <div className="home-face-wrap"><DexterFace state={faceState} /><div className="face-caption"><span>DX-01</span><i /> FACE CAP SCAN · GRAPHITE ANDROID · 52 MORPHS</div></div>
    </section>
    <div className="section-title"><div><span className="eyebrow">SYSTEM SNAPSHOT</span><h2>Live at a glance</h2></div><span className="muted small">Status read from the local backend</span></div>
    <div className="metrics-grid four">
      <NumberCard label="Providers configured" value={`${appState ? Object.values(appState.providers).filter(p => p.configured).length : '—'} / 3`} detail="Reachability has not been checked" icon={Radio} />
      <NumberCard label="Filename index" value={appState?.indexes.filename_files?.toLocaleString() ?? '—'} detail={appState?.indexes.filename_ready ? 'Indexed files on configured drives' : 'Run a name scan to begin'} icon={FolderOpen} tone="violet" />
      <NumberCard label="Content index" value={appState?.indexes.content_files?.toLocaleString() ?? '—'} detail="Searchable text files" icon={Database} tone="blue" />
      <NumberCard label="Training" value="Idle" detail="No model trainer is installed" icon={Activity} tone="amber" />
    </div>
    <div className="home-lower-grid">
      <section className="panel activity-panel"><div className="panel-heading"><div><span className="eyebrow">RECENT ACTIVITY</span><h2>Local event stream</h2></div><Link className="subtle-link" to="/training">View event log <ChevronRight size={15} /></Link></div>
        {appState?.recent_events?.length ? <div className="event-list">{appState.recent_events.slice(0, 5).map((e, i) => <div className="event-row" key={`${e.created}-${i}`}><div className={`event-mark ${e.status}`}><Activity size={14} /></div><div><strong>{e.detail}</strong><span>{e.type.replaceAll('_', ' ')}{e.provider ? ` · ${e.provider}` : ''}</span></div><time>{new Date(e.created).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</time></div>)}</div> : <Empty icon={Activity} title="No activity recorded yet" text="Provider requests, file-index jobs, and explicit memory or training actions will appear here." />}
      </section>
      <section className="panel quick-panel"><span className="eyebrow">QUICK LAUNCH</span><h2>Where to next?</h2><div className="quick-links"><Link to="/chat"><div className="quick-icon cyan"><Sparkles size={18} /></div><span><strong>Ask Dexter</strong><small>Continue across Groq and Gemini</small></span><ArrowRight size={16} /></Link><Link to="/files"><div className="quick-icon blue"><Search size={18} /></div><span><strong>Search local files</strong><small>Use the existing SQLite indexes</small></span><ArrowRight size={16} /></Link><Link to="/memory"><div className="quick-icon violet"><BrainCircuitIcon /></div><span><strong>Review owner memory</strong><small>{appState?.memory_count ?? 0} saved facts · owner controlled</small></span><ArrowRight size={16} /></Link></div></section>
    </div>
  </>
}

function BrainCircuitIcon() { return <Sparkles size={18} /> }

type ChatMessage = Message & { pairedUserId?: number; pending?: boolean }

export function ChatPage() {
  const { appState, setFaceState, refresh } = useApp()
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [text, setText] = useState('')
  const [provider, setProvider] = useState(localStorage.getItem('devil-provider') || 'auto')
  const [conversations, setConversations] = useState<{ id: string; title: string }[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [outcome, setOutcome] = useState<FaceState>('idle')
  const [recording, setRecording] = useState(false)
  const [transcribing, setTranscribing] = useState(false)
  const [speakingId, setSpeakingId] = useState<number | null>(null)
  const [memoryDraft, setMemoryDraft] = useState<ChatMessage | null>(null)
  const [candidateBusy, setCandidateBusy] = useState<number | null>(null)
  const bottom = useRef<HTMLDivElement>(null)
  const recorder = useRef<MediaRecorder | null>(null)
  const audioChunks = useRef<Blob[]>([])
  const stream = useRef<MediaStream | null>(null)
  const configured = appState?.providers
  const busyFace: FaceState = recording ? 'listening' : busy || transcribing ? 'thinking' : speakingId ? 'speaking' : outcome

  useEffect(() => { setFaceState(busyFace) }, [busyFace, setFaceState])
  useEffect(() => {
    let cancelled = false
    Promise.all([api.history(), api.conversations()]).then(([result, threads]) => {
      if (!cancelled) {
        let pairedUserId: number | undefined
        setMessages(result.messages.map(message => {
          if (message.role === 'user') pairedUserId = message.id
          return { ...message, pairedUserId }
        }))
        setConversations(threads.conversations)
      }
    }).catch(e => setError(e.message))
    return () => { cancelled = true }
  }, [appState?.conversation_id])
  useEffect(() => { bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }) }, [messages, busy])
  useEffect(() => () => { stream.current?.getTracks().forEach(t => t.stop()) }, [])

  const startNew = async () => {
    setError('')
    try { await api.newConversation(); setMessages([]); await refresh() } catch (e) { setError((e as Error).message) }
  }

  const send = async (event?: FormEvent) => {
    event?.preventDefault()
    const content = text.trim()
    if (!content || busy) return
    setText(''); setError(''); setBusy(true)
    const temporary = Date.now()
    setMessages(current => [...current, { id: -temporary, role: 'user', content, created: new Date().toISOString(), pending: true }])
    try {
      if (provider === 'compare') {
        const result = await api.compare(content)
        setMessages(current => [...current.filter(m => m.id !== -temporary),
          { id: -temporary, role: 'user', content, created: new Date().toISOString() },
          ...result.responses.map((r: any, i: number) => ({ id: r.message_id || -(temporary + i + 1), role: 'assistant' as const,
            content: r.content, provider: r.provider, created: new Date().toISOString(), pairedUserId: result.user_message_id,
            pending: !r.available }))])
      } else {
        const result = await api.send(content, provider)
        setMessages(current => [...current.filter(m => m.id !== -temporary),
          { id: result.user_message_id, role: 'user', content, created: new Date().toISOString() },
          { ...result.message, pairedUserId: result.user_message_id }])
      }
      setOutcome('success')
      window.setTimeout(() => setOutcome('idle'), 1300)
      await refresh()
    } catch (e) {
      setMessages(current => current.map(m => m.id === -temporary ? { ...m, pending: false } : m))
      setError((e as Error).message)
      setOutcome('error')
      window.setTimeout(() => setOutcome('idle'), 1600)
    } finally { setBusy(false) }
  }

  const toggleRecording = async () => {
    if (recording && recorder.current) { recorder.current.stop(); return }
    setError('')
    try {
      const media = await navigator.mediaDevices.getUserMedia({ audio: true })
      stream.current = media
      const type = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : ''
      const instance = new MediaRecorder(media, type ? { mimeType: type } : undefined)
      recorder.current = instance
      audioChunks.current = []
      instance.ondataavailable = e => { if (e.data.size) audioChunks.current.push(e.data) }
      instance.onstop = async () => {
        media.getTracks().forEach(t => t.stop()); stream.current = null
        setRecording(false); setTranscribing(true)
        try {
          const blob = new Blob(audioChunks.current, { type: instance.mimeType || 'audio/webm' })
          const bytes = new Uint8Array(await blob.arrayBuffer())
          let binary = ''
          bytes.forEach(byte => { binary += String.fromCharCode(byte) })
          const result = await api.transcribe(btoa(binary), blob.type || 'audio/webm')
          setText(result.transcript)
        } catch (e) { setError((e as Error).message); setOutcome('error'); window.setTimeout(() => setOutcome('idle'), 1600) }
        finally { setTranscribing(false) }
      }
      instance.start()
      setRecording(true)
      setFaceState('listening')
    } catch (e) {
      setError(`Microphone unavailable: ${(e as Error).message}`)
      setOutcome('error')
      window.setTimeout(() => setOutcome('idle'), 1600)
    }
  }

  const speak = async (message: ChatMessage) => {
    setSpeakingId(message.id); setError(''); setFaceState('speaking')
    try { await api.speak(message.content, message.provider || ''); }
    catch (e) { setError((e as Error).message); setOutcome('error'); window.setTimeout(() => setOutcome('idle'), 1600) }
    finally { setSpeakingId(null) }
  }

  const proposeCandidate = async (message: ChatMessage) => {
    const userId = message.pairedUserId
    if (!userId || message.id < 0) { setError('This comparison response has not yet been stored. Reload the conversation and try again.'); return }
    setCandidateBusy(message.id); setError('')
    try {
      await api.submitCandidate(message.id, 'conversation')
      setMessages(current => current.map(item => item.id === message.id || item.id === userId
        ? { ...item, excluded_training: 0 } : item))
      setError('Training candidate submitted for review. No model training has started.')
      await refresh()
    } catch (e) { setError((e as Error).message) }
    finally { setCandidateBusy(null) }
  }

  const excludeCandidate = async (message: ChatMessage) => {
    setCandidateBusy(message.id); setError('')
    try {
      await api.exclusion(message.id, true)
      setMessages(current => current.map(item => item.id === message.id ? { ...item, excluded_training: 1 } : item))
      setError('This response and its selected training example were removed from the training queue.')
      await refresh()
    } catch (e) { setError((e as Error).message) }
    finally { setCandidateBusy(null) }
  }

  const providerOptions = [
    { id: 'auto', label: 'Automatic · recommended', available: true },
    { id: 'groq', label: 'Groq · technical + local tools', available: configured?.groq?.configured ?? false },
    { id: 'gemini', label: 'Gemini · general conversation', available: configured?.gemini?.configured ?? false },
    { id: 'nemotron', label: 'Nemotron · web research', available: configured?.nemotron?.configured ?? false },
    { id: 'compare', label: 'Compare configured providers', available: true },
  ]

  return <div className="chat-page">
    <PageHeader eyebrow="DEXTER · CONVERSATION WORKSPACE" title="Your space to think out loud." description="Groq handles technical work, Gemini handles general conversation, and Nemotron is available for web research." action={<button className="button secondary" onClick={() => void startNew()}><Plus size={16} /> New conversation</button>} />
    <div className="chat-layout panel">
      <div className="chat-main">
        <div className="chat-toolbar"><div className="chat-peer"><div className="chat-avatar"><Sparkles size={17} /></div><div><strong>Dexter</strong><small>PERSONAL ASSISTANT · DEVIL LOCAL</small></div></div><label className="thread-select-label"><span>THREAD</span><select aria-label="Conversation history" value={appState?.conversation_id || ''} onChange={async e => { await api.selectConversation(e.target.value); await refresh() }}>{conversations.map(thread => <option key={thread.id} value={thread.id}>{thread.title || 'New conversation'}</option>)}</select></label>
          <label className="provider-select-label">PROVIDER<select value={provider} onChange={e => { setProvider(e.target.value); localStorage.setItem('devil-provider', e.target.value) }}>{providerOptions.map(o => <option key={o.id} value={o.id}>{o.label}{!o.available && o.id !== 'auto' && o.id !== 'compare' ? ' · not configured' : ''}</option>)}</select></label>
        </div>
        <div className="messages-scroll">
          {!messages.length && <div className="chat-welcome"><div className="welcome-mark"><Sparkles size={24} /></div><div className="eyebrow">A NEW THREAD</div><h2>Where should we begin?</h2><p>Ask about something you’re building, have Dexter search your indexed files, or use the mic to dictate a message.</p><div className="starter-prompts"><button onClick={() => setText('Help me find the right project for the feature I want to build.')}>Find a project <ArrowRight size={14} /></button><button onClick={() => setText('What information would help you work better with me?')}>Set a useful context <ArrowRight size={14} /></button></div></div>}
          {messages.map(message => <article className={`message-row ${message.role}`} key={message.id}>
            <div className={`message-avatar ${message.role}`}>{message.role === 'assistant' ? <Sparkles size={15} /> : 'D'}</div>
            <div className="message-body"><div className="message-meta"><strong>{message.role === 'assistant' ? 'Dexter' : 'You'}</strong>{message.provider && <span className="provider-badge">{message.provider}</span>}<time>{new Date(message.created).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</time>{message.pending && <LoaderCircle className="spin" size={14} />}</div>
              <div className="message-content">{message.content}</div>
              {message.role === 'assistant' && message.id > 0 && <div className="message-actions"><button title="Copy response" onClick={() => void navigator.clipboard.writeText(message.content)}><Clipboard size={14} /> Copy</button>{appState?.voice.text_to_speech && appState.preferences?.tts_enabled !== false && <button title="Speak this response" onClick={() => void speak(message)}>{speakingId === message.id ? <AudioLines size={14} /> : <Volume2 size={14} />}{speakingId === message.id ? 'Speaking' : 'Speak'}</button>}{message.excluded_training === 0 ? <button title="Remove this response from the training set" disabled={candidateBusy === message.id} onClick={() => void excludeCandidate(message)}><LockKeyhole size={14} /> Exclude from training</button> : <button title="Select this response as a training candidate" disabled={candidateBusy === message.id} onClick={() => void proposeCandidate(message)}><CheckCircle2 size={14} /> Review for training</button>}<span className="privacy-note"><LockKeyhole size={12} /> {message.excluded_training === 0 ? 'selected by owner' : 'excluded by default'}</span></div>}
              {message.role === 'user' && message.id > 0 && <div className="message-actions"><button title="Review and save this as owner memory" onClick={() => setMemoryDraft(message)}><BrainCircuit size={14} /> Save as memory</button><span className="privacy-note"><LockKeyhole size={12} /> separate from training</span></div>}
            </div>
          </article>)}
          {busy && <div className="assistant-thinking"><div className="message-avatar assistant"><Sparkles size={15} /></div><div><div className="thinking-dots"><i /><i /><i /></div><span>Dexter is working with the selected provider…</span></div></div>}
          <div ref={bottom} />
        </div>
        {error && <div className="inline-error"><ShieldAlert size={15} />{error}<button onClick={() => setError('')} aria-label="Dismiss"><X size={14} /></button></div>}
        <form className="composer" onSubmit={send}>
          <button type="button" className={`mic-button ${recording ? 'recording' : ''}`} title={recording ? 'Stop recording' : 'Record a voice message'} disabled={transcribing || !appState?.voice.speech_to_text} onClick={() => void toggleRecording()}>{recording ? <Square size={17} /> : transcribing ? <LoaderCircle className="spin" size={18} /> : <Mic size={18} />}</button>
          <div className="composer-input-wrap"><textarea rows={1} placeholder={recording ? 'Listening… select the stop button when finished' : transcribing ? 'Transcribing with Groq Whisper…' : 'Message Dexter…'} value={text} onChange={e => setText(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void send() } }} disabled={recording || transcribing} /><div className="composer-hint">ENTER TO SEND <span>·</span> SHIFT + ENTER FOR NEW LINE</div></div>
          <button className="send-button" type="submit" disabled={!text.trim() || busy || recording || transcribing} title="Send message"><Send size={17} /></button>
        </form>
        <div className="chat-disclaimer"><ShieldCheck size={13} /> File changes require your approval. Messages are stored in the local DEVIL database.</div>
      </div>
      <aside className="chat-rail"><div className="rail-face"><DexterFace state={busyFace} compact /><div className="rail-status"><i className={`status-dot ${busyFace}`} />{busyFace === 'idle' ? 'Ready to help' : busyFace === 'listening' ? 'Listening to you' : busyFace === 'thinking' ? 'Working on it' : 'Speaking aloud'}</div></div>
        <div className="rail-divider" /><div className="eyebrow">PROVIDER ROUTING</div><div className="route-list">{[
          ['Groq', 'Technical tasks & local tools', configured?.groq?.configured], ['Gemini', 'General conversation', configured?.gemini?.configured], ['Nemotron', 'OpenRouter web research', configured?.nemotron?.configured],
        ].map(([name, description, up]) => <div className="route-row" key={name as string}><i className={`status-dot ${up ? 'configured' : 'disabled'}`} /><span><strong>{name}</strong><small>{description}</small></span></div>)}</div>
        <div className="rail-callout"><div className="callout-icon"><LockKeyhole size={15} /></div><strong>Your data stays local.</strong><p>Keys stay on the Python backend. Chat messages are excluded from training unless you review and submit a specific example.</p><Link to="/settings">Privacy & controls <ArrowRight size={13} /></Link></div>
      </aside>
    </div>
    {memoryDraft && <MemoryDraftDialog message={memoryDraft} onClose={() => setMemoryDraft(null)} onSaved={() => { setMemoryDraft(null); void refresh() }} />}
  </div>
}

function MemoryDraftDialog({ message, onClose, onSaved }: { message: ChatMessage; onClose: () => void; onSaved: () => void }) {
  const [content, setContent] = useState(message.content.slice(0, 500))
  const [category, setCategory] = useState('communication')
  const [source, setSource] = useState('confirmed')
  const [error, setError] = useState('')
  const save = async () => {
    try { await api.saveMemory({ content, category, source_type: source, confidence: source === 'confirmed' ? 1 : 0.5 }); onSaved() }
    catch (e) { setError((e as Error).message) }
  }
  return <div className="modal-scrim"><section className="confirm-modal memory-modal"><button className="modal-close" onClick={onClose}><X size={17} /></button><div className="eyebrow">OWNER-CONTROLLED MEMORY</div><h2>Save a useful fact?</h2><p>Review the wording. This will be saved as owner memory only, not training data.</p><textarea className="field-textarea" value={content} onChange={e => setContent(e.target.value)} maxLength={2000} /><div className="form-two"><label>Category<input value={category} onChange={e => setCategory(e.target.value)} /></label><label>Source<select value={source} onChange={e => setSource(e.target.value)}><option value="confirmed">Confirmed by Devil</option><option value="inference">Inference · uncertain</option></select></label></div>{error && <div className="inline-error">{error}</div>}<div className="modal-actions"><button className="button secondary" onClick={onClose}>Cancel</button><button className="button primary" onClick={() => void save()}><Check size={15} /> Save memory</button></div></section></div>
}

export function TrainingPage() {
  const { appState } = useApp()
  const [candidates, setCandidates] = useState<any[]>([])
  const [events, setEvents] = useState<any[]>([])
  const [filter, setFilter] = useState({ type: '', provider: '', status: '' })
  const [error, setError] = useState('')
  const refresh = useCallback(async () => {
    try { const [c, e] = await Promise.all([api.candidates(), api.events(filter)]); setCandidates(c.candidates); setEvents(e.events); setError('') }
    catch (e) { setError((e as Error).message) }
  }, [filter])
  useEffect(() => { void refresh() }, [refresh])
  const review = async (id: number, status: string) => { try { await api.reviewCandidate(id, { status }); await refresh() } catch (e) { setError((e as Error).message) } }
  const pending = candidates.filter(c => c.status === 'pending').length
  return <>
    <PageHeader eyebrow="DEXTER · LEARNING LABORATORY" title="A learning pipeline, with receipts." description="Review the examples Devil explicitly selects. Training is not implemented or running; the page reports that honestly." action={<button className="button secondary" onClick={() => void refresh()}><RefreshCw size={15} /> Refresh</button>} />
    {error && <div className="inline-error"><ShieldAlert size={15} />{error}</div>}
    <section className="training-state panel"><div className="training-state-mark"><Activity size={22} /></div><div className="training-state-copy"><div className="eyebrow">PIPELINE STATUS</div><h2>Trainer not installed</h2><p>Teacher APIs can create reviewed examples, but there is no local training process, dataset builder, evaluation runner, or checkpoint system in this project.</p></div><StatusPill state="amber">IDLE · NO TRAINING PROCESS</StatusPill></section>
    <div className="metrics-grid four training-metrics">
      <NumberCard label="Selected examples" value={appState?.training.dataset_candidates ?? 0} detail="Explicitly submitted for review" icon={Database} />
      <NumberCard label="Pending review" value={pending} detail="Not included in a dataset yet" icon={Clock3} tone="amber" />
      <NumberCard label="Accepted" value={candidates.filter(c => c.status === 'accepted').length} detail="Owner-reviewed candidates" icon={CheckCircle2} tone="blue" />
      <NumberCard label="Training metrics" value="Unavailable" detail="No telemetry source exists" icon={Activity} tone="violet" />
    </div>
    <section className="panel section-panel"><div className="panel-heading"><div><div className="eyebrow">DATASET REVIEW</div><h2>Candidate examples</h2></div><span className="muted small">Private chats are excluded unless Devil selects an example.</span></div>
      {candidates.length ? <div className="candidate-list">{candidates.map(c => <article className="candidate-card" key={c.id}><div className="candidate-meta"><StatusPill state={c.status === 'accepted' ? 'green' : c.status === 'rejected' ? 'red' : 'amber'}>{c.status.replace('_', ' ')}</StatusPill><span>{c.teacher}</span><span>{c.category}</span><time>{new Date(c.created).toLocaleString()}</time></div><div className="candidate-prompt"><b>Owner prompt</b><p>{c.prompt}</p></div><div className="candidate-response"><b>Assistant answer</b><p>{c.response}</p></div><div className="candidate-actions"><button className="button secondary small-button" onClick={() => void review(c.id, 'rejected')}><X size={14} /> Reject</button><button className="button primary small-button" onClick={() => void review(c.id, 'accepted')}><Check size={14} /> Accept candidate</button><button className="button secondary small-button" onClick={async () => { if (window.confirm('Permanently delete this training example?')) { await api.deleteCandidate(c.id); await refresh() } }}><Trash2 size={14} /> Delete</button></div></article>)}</div> : <Empty icon={Database} title="No training examples selected" text="Use “Review for training” on a specific chat response. Dexter never adds conversations automatically." />}
    </section>
    <section className="panel section-panel"><div className="panel-heading"><div><div className="eyebrow">AUDIT TRAIL</div><h2>Pipeline events</h2></div><div className="filter-row"><select aria-label="Filter by event type" value={filter.type} onChange={e => setFilter({ ...filter, type: e.target.value })}><option value="">All event types</option><option value="chat">Chat</option><option value="provider_comparison">Provider comparison</option><option value="training_candidate">Training candidates</option><option value="memory">Memory</option><option value="file_approval">File approval</option><option value="voice_playback">Voice</option></select><select aria-label="Filter by provider" value={filter.provider} onChange={e => setFilter({ ...filter, provider: e.target.value })}><option value="">All providers</option><option>Groq</option><option>Gemini</option><option>OpenRouter/Nemotron</option></select><select aria-label="Filter by status" value={filter.status} onChange={e => setFilter({ ...filter, status: e.target.value })}><option value="">All statuses</option><option value="completed">Completed</option><option value="failed">Failed</option><option value="pending_review">Pending review</option><option value="saved">Saved</option></select></div></div>
      {events.length ? <div className="log-table-wrap"><table className="log-table"><thead><tr><th>TIME</th><th>EVENT</th><th>PROVIDER</th><th>STATUS</th><th>DETAIL</th></tr></thead><tbody>{events.map(e => <tr key={e.id}><td>{new Date(e.created).toLocaleString()}</td><td>{e.type.replaceAll('_', ' ')}</td><td>{e.provider || '—'}</td><td><StatusPill state={e.status === 'failed' ? 'red' : e.status === 'completed' || e.status === 'saved' ? 'green' : 'neutral'}>{e.status}</StatusPill></td><td>{e.detail}</td></tr>)}</tbody></table></div> : <Empty icon={Activity} title="No matching events" text="Real backend events will be recorded here. No sample activity is generated." />}
    </section>
  </>
}

export function MemoryPage() {
  const { appState, refresh } = useApp()
  const [items, setItems] = useState<any[]>([])
  const [query, setQuery] = useState('')
  const [form, setForm] = useState(false)
  const [editing, setEditing] = useState<number | null>(null)
  const [content, setContent] = useState('')
  const [category, setCategory] = useState('general')
  const [source, setSource] = useState('confirmed')
  const [error, setError] = useState('')
  const load = useCallback(async () => { try { setItems((await api.memories(query)).memories); setError('') } catch (e) { setError((e as Error).message) } }, [query])
  useEffect(() => { void load() }, [load])
  const beginEdit = (item: any) => { setEditing(item.id); setContent(item.content); setCategory(item.category); setSource(item.source_type); setForm(true) }
  const reset = () => { setEditing(null); setContent(''); setCategory('general'); setSource('confirmed'); setForm(false) }
  const save = async (event: FormEvent) => {
    event.preventDefault(); setError('')
    try {
      const body = { content, category, source_type: source, confidence: source === 'confirmed' ? 1 : 0.5 }
      if (editing) await api.editMemory(editing, body); else await api.saveMemory(body)
      reset(); await load(); await refresh()
    } catch (e) { setError((e as Error).message) }
  }
  const remove = async (id: number) => { if (!window.confirm('Delete this saved memory?')) return; try { await api.deleteMemory(id); await load(); await refresh() } catch (e) { setError((e as Error).message) } }
  return <>
    <PageHeader eyebrow="DEXTER · OWNER CONTEXT" title="Memory, with your permission." description="These are useful owner facts, separate from training examples. Only facts you explicitly save appear here." action={<button className="button primary" onClick={() => { reset(); setForm(true) }}><Plus size={16} /> Save a fact</button>} />
    <div className="memory-intro panel"><div className="memory-illustration"><BrainCircuit size={27} /></div><div><strong>{appState?.memory_count ?? 0} saved memories</strong><p>Confirmed facts and uncertain inferences are labeled separately. You can correct or delete any item; this page starts empty until you save something.</p></div><StatusPill state="green">LOCAL SQLITE</StatusPill></div>
    <div className="memory-toolbar"><label className="search-field"><Search size={17} /><input placeholder="Search saved memory…" value={query} onChange={e => setQuery(e.target.value)} /><kbd>FILTER</kbd></label><span className="muted small">{items.length} result{items.length === 1 ? '' : 's'}</span></div>
    {error && <div className="inline-error"><ShieldAlert size={15} />{error}</div>}
    {items.length ? <div className="memory-grid">{items.map(item => <article className="memory-card panel" key={item.id}><div className="memory-card-top"><span className={`source-tag ${item.source_type}`}>{item.source_type === 'confirmed' ? <CheckCircle2 size={13} /> : <CircleHelp size={13} />}{item.source_type === 'confirmed' ? 'CONFIRMED' : 'INFERENCE'}</span><span className="memory-category">{item.category}</span></div><p>{item.content}</p><div className="memory-card-footer"><span>Created {new Date(item.created).toLocaleDateString()} · updated {new Date(item.updated).toLocaleDateString()}</span><div><button title="Edit memory" onClick={() => beginEdit(item)}><Settings2 size={14} /></button><button title="Delete memory" onClick={() => void remove(item.id)}><Trash2 size={14} /></button></div></div></article>)}</div> : <Empty icon={BrainCircuit} title={query ? 'No memories match that search' : 'Your memory is yours to build'} text={query ? 'Try another keyword, or clear the filter.' : 'When a personal preference or project fact would be useful later, choose “Save a fact” or save one from a chat. Nothing is inferred or saved automatically.'} />}
    {form && <div className="modal-scrim"><form className="confirm-modal memory-modal" onSubmit={save}><button type="button" className="modal-close" onClick={reset}><X size={17} /></button><div className="eyebrow">{editing ? 'EDIT SAVED MEMORY' : 'OWNER-APPROVED MEMORY'}</div><h2>{editing ? 'Correct this fact.' : 'What should Dexter remember?'}</h2><p>Memory is stored locally. This does not create a training example.</p><label className="field-label">Memory<textarea className="field-textarea" required maxLength={2000} value={content} onChange={e => setContent(e.target.value)} placeholder="A specific fact that would help with future assistance…" /></label><div className="form-two"><label>Category<input value={category} onChange={e => setCategory(e.target.value)} maxLength={60} /></label><label>Evidence<select value={source} onChange={e => setSource(e.target.value)}><option value="confirmed">Confirmed by Devil</option><option value="inference">Inference · uncertain</option></select></label></div>{error && <div className="inline-error">{error}</div>}<div className="modal-actions"><button type="button" className="button secondary" onClick={reset}>Cancel</button><button className="button primary" type="submit"><Check size={15} /> Save memory</button></div></form></div>}
  </>
}

export function FilesPage() {
  const { appState, refresh } = useApp()
  const [query, setQuery] = useState('')
  const [mode, setMode] = useState('name')
  const [results, setResults] = useState<any[]>([])
  const [selected, setSelected] = useState<any>(null)
  const [preview, setPreview] = useState('')
  const [searching, setSearching] = useState(false)
  const [error, setError] = useState('')
  const [starting, setStarting] = useState(false)
  const runSearch = async (event?: FormEvent) => {
    event?.preventDefault(); if (!query.trim()) return
    setSearching(true); setError(''); setSelected(null); setPreview('')
    try { const result = await api.searchFiles(query, mode); setResults(result.results || []); if (result.error) setError(result.error) }
    catch (e) { setError((e as Error).message) }
    finally { setSearching(false) }
  }
  const read = async (file: any) => {
    if (!file.path) return
    setSelected(file); setPreview('Loading current file…')
    try { const result = await api.readFile(file.path); setPreview(result.content || result.error || '(empty file)') }
    catch (e) { setPreview((e as Error).message) }
  }
  const index = async (kind: string) => {
    setStarting(true); setError('')
    try { const result = await api.startIndex(kind); if (result.error) setError(result.error); await refresh() }
    catch (e) { setError((e as Error).message) }
    finally { setStarting(false) }
  }
  const job = appState?.index_job
  return <>
    <PageHeader eyebrow="DEVIL · LOCAL FILE DISCOVERY" title="Find the needle, not every haystack." description="Search indexed filenames across allowed roots or indexed text inside projects. Matches are checked against the current filesystem." action={<button className="button secondary" onClick={() => void index('names')} disabled={starting}><RefreshCw size={15} /> Refresh name index</button>} />
    <section className="file-search-panel panel"><div className="file-search-icon"><FileSearch size={22} /></div><div className="eyebrow">LOCAL SEARCH · INDEXED METADATA</div><form className="file-search-form" onSubmit={runSearch}><div className="search-field large"><Search size={18} /><input autoFocus placeholder={mode === 'name' ? 'Try “OCR project” or a filename…' : 'Search indexed code and text…'} value={query} onChange={e => setQuery(e.target.value)} /><kbd>ENTER</kbd></div><button type="submit" className="button primary" disabled={searching || !query.trim()}>{searching ? <LoaderCircle className="spin" size={15} /> : <Search size={15} />} Search</button></form><div className="search-options"><div className="segmented-control"><button className={mode === 'name' ? 'selected' : ''} onClick={() => setMode('name')}><FolderOpen size={14} /> File and folder names</button><button className={mode === 'content' ? 'selected' : ''} onClick={() => setMode('content')}><Code2 size={14} /> Indexed contents</button></div><div className="index-status"><i className={`status-dot ${appState?.indexes.filename_ready ? 'configured' : 'disabled'}`} />{appState?.indexes.filename_files?.toLocaleString() ?? 0} files indexed · {appState?.indexes.content_files?.toLocaleString() ?? 0} with searchable content</div></div></section>
    {error && <div className="inline-error"><ShieldAlert size={15} />{error}</div>}
    {job && <section className="job-banner panel"><div className="job-spinner"><LoaderCircle className="spin" size={19} /></div><div><strong>{job.label} · {job.status}</strong><small>{job.progress?.scanned ?? job.progress?.files ?? 0} files checked {job.progress?.project ? `· ${job.progress.project}` : ''}</small></div><button className="button secondary small-button" onClick={() => void api.cancelIndex()}><Square size={13} /> Cancel job</button></section>}
    <div className="files-results-layout"><section className="panel results-panel"><div className="panel-heading"><div><div className="eyebrow">{mode === 'name' ? 'FILENAME INDEX' : 'CONTENT INDEX'}</div><h2>{results.length ? `${results.length} matching results` : 'Search results'}</h2></div><span className="muted small">{query ? `for “${query}”` : 'Waiting for a query'}</span></div>
      {results.length ? <div className="file-result-list">{results.map((r, i) => <button className={`file-result ${selected?.path === r.path ? 'selected' : ''}`} onClick={() => void read(r)} key={`${r.path}-${i}`}><div className="file-icon">{r.kind === 'project' || r.kind === 'dir' ? <FolderOpen size={17} /> : <FileCode2 size={17} />}</div><div className="file-result-main"><strong>{r.name || r.path.split(/[\\/]/).at(-1)}</strong><span>{r.path}</span>{r.excerpt && <small>{r.excerpt}</small>}</div><div className="file-result-meta"><span>{r.kind || r.extension || 'file'}</span><time>{r.modified || ''}</time></div><ChevronRight size={15} /></button>)}</div> : <Empty icon={FileSearch} title={query ? 'No indexed matches' : 'Your indexes are ready when you are'} text={query ? 'Try fewer keywords, switch between name and content search, or index the project first.' : 'Search filenames across the existing index, or query text from files already content-indexed.'} />}
    </section>
    <aside className="panel file-preview-panel"><div className="panel-heading"><div><div className="eyebrow">CURRENT-DISK PREVIEW</div><h2>{selected ? 'Selected file' : 'No file selected'}</h2></div><FileCode2 size={17} className="muted" /></div>{selected ? <><div className="preview-path">{selected.path}</div><pre className="file-preview">{preview || 'Select a file to read its current contents.'}</pre><Link to="/chat" className="subtle-link">Ask Dexter about this file <ArrowRight size={14} /></Link></> : <Empty icon={FileCode2} title="Choose a result" text="DEVIL will read the file from disk when you select it; content-index snippets are only discovery aids." />}</aside></div>
    <section className="index-cta panel"><div><div className="eyebrow">BACKGROUND INDEXING</div><strong>Expand searchable project contents</strong><p>Use the existing filename index to select recognized projects. Jobs continue while the interface stays responsive.</p></div><div className="index-cta-actions"><button className="button secondary" disabled={starting || !!job} onClick={() => void index('all')}><Database size={15} /> Index projects</button><button className="button secondary" disabled={starting || !!job} onClick={() => void index('names')}><RefreshCw size={15} /> Scan names</button></div></section>
  </>
}

export function ToolsPage() {
  const { appState } = useApp()
  const groups = [
    { title: 'DISCOVERY & INSPECTION', items: [
      ['find_files', 'Search indexed filenames and project roots.', 'Local filename index'],
      ['search_local_files', 'Search indexed text and source snippets.', 'Persistent content index'],
      ['read_indexed_file', 'Read a bounded current-disk line range.', 'Allowed roots only'],
      ['outline / grep', 'Map symbols and inspect exact code references.', 'Targeted evidence'],
      ['index_project', 'Index useful project text in the background.', 'Single active job'],
    ] },
    { title: 'APPROVED FILE OPERATIONS', items: [
      ['write_file / edit_file', 'Create or edit text after an explicit approval.', 'UI confirmation gate'],
      ['delete_file / undo_last', 'Quarantine one file and restore recorded changes.', 'Recoverable where journaled'],
      ['zip_folder / unzip_files', 'Create archives or extract validated archives.', 'Approval required'],
    ] },
    { title: 'PROVIDERS & VOICE', items: [
      ['Groq', 'Technical chat, tool calls, local tools, and Whisper transcription.', appState?.providers.groq.configured ? 'Configured' : 'Missing credential'],
      ['Gemini', 'General conversation and final-answer synthesis.', appState?.providers.gemini.configured ? 'Configured' : 'Missing credential'],
      ['OpenRouter / Nemotron', 'Web research using the configured search tool.', appState?.providers.nemotron.configured ? 'Configured · web search' : 'Missing credential'],
      ['Push-to-talk & SAPI', 'User-activated browser recording, Whisper, Windows speech output.', `${appState?.voice.speech_to_text ? 'Whisper configured' : 'Whisper unavailable'} · ${appState?.voice.text_to_speech ? 'SAPI available' : 'SAPI unavailable'}`],
    ] },
  ]
  return <>
    <PageHeader eyebrow="DEVIL · CAPABILITY REGISTRY" title="Tools Dexter can actually use." description="A transparent view of backend capabilities and their boundaries. Model output alone never executes arbitrary commands." />
    <section className="tool-boundary panel"><div className="tool-boundary-icon"><ShieldCheck size={20} /></div><div><strong>Constrained by design</strong><p>There is no shell or unrestricted code-execution tool. Local paths are checked against the configured roots; each destructive or consequential file operation asks you first.</p></div></section>
    {groups.map(group => <section className="tool-group panel" key={group.title}><div className="eyebrow">{group.title}</div><div className="tool-grid">{group.items.map(([name, detail, availability]) => <article className="tool-card" key={name}><div className="tool-card-top"><div className="tool-symbol"><Code2 size={16} /></div><StatusPill state={String(availability).includes('Missing') || String(availability).includes('unavailable') ? 'amber' : 'green'}>{availability}</StatusPill></div><strong>{name}</strong><p>{detail}</p></article>)}</div></section>)}
    <div className="tool-note"><CircleHelp size={15} /><span>Capabilities are derived from the inspected Python tools and runtime. Nemotron is represented as web research, not general chat.</span></div>
  </>
}

export function SettingsPage() {
  const { appState, refresh } = useApp()
  const [health, setHealth] = useState<any>(null)
  const [checking, setChecking] = useState(false)
  const [tts, setTts] = useState(true)
  const [reducedMotion, setReducedMotion] = useState(window.matchMedia('(prefers-reduced-motion: reduce)').matches)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState('')
  const [settingsLoaded, setSettingsLoaded] = useState(false)
  useEffect(() => { api.settings().then(r => { const s = r.settings || {}; if (typeof s.tts_enabled === 'boolean') setTts(s.tts_enabled); if (typeof s.reduced_motion === 'boolean') setReducedMotion(s.reduced_motion); setSettingsLoaded(true) }).catch(e => { setError(e.message); setSettingsLoaded(true) }) }, [])
  useEffect(() => { document.documentElement.dataset.reducedMotion = String(reducedMotion) }, [reducedMotion])
  const check = async () => { setChecking(true); setError(''); try { setHealth((await api.health()).providers) } catch (e) { setError((e as Error).message) } finally { setChecking(false) } }
  const save = async () => { setError(''); try { await api.saveSettings({ tts_enabled: tts, reduced_motion: reducedMotion }); await refresh(); setSaved(true); window.setTimeout(() => setSaved(false), 1600) } catch (e) { setError((e as Error).message) } }
  return <>
    <PageHeader eyebrow="DEVIL · LOCAL PREFERENCES" title="The controls stay in your hands." description="Provider credentials are read from backend environment variables. This page only reports whether they are configured; key values are never returned." />
    {error && <div className="inline-error"><ShieldAlert size={15} />{error}</div>}
    <section className="panel settings-section"><div className="panel-heading"><div><div className="eyebrow">MODEL CONNECTIONS</div><h2>Provider configuration</h2></div><button className="button secondary" onClick={() => void check()} disabled={checking}>{checking ? <LoaderCircle className="spin" size={15} /> : <Wifi size={15} />} Check live connections</button></div><p className="section-description">Status is configuration-only until you run a connection check. Reachability checks call provider model-list endpoints without sending a chat prompt.</p>
      <div className="provider-settings-list">{Object.entries(appState?.providers || {}).map(([key, value]) => { const h = health?.[key]; return <div className="provider-settings-row" key={key}><div className="provider-mark">{key === 'nemotron' ? <Globe2 size={17} /> : <Cpu size={17} />}</div><div className="provider-settings-name"><strong>{key === 'nemotron' ? 'OpenRouter · Nemotron' : key[0].toUpperCase() + key.slice(1)}</strong><small>{value.model} · {value.capability}</small></div><StatusPill state={h ? h.reachable ? 'green' : 'red' : value.configured ? 'blue' : 'amber'}>{h ? h.message : value.configured ? 'Configured · not checked' : 'Credential missing'}</StatusPill></div> })}</div>
    </section>
    <section className="panel settings-section"><div className="eyebrow">VOICE & ACCESSIBILITY</div><h2>Interaction preferences</h2><div className="settings-toggle-row"><div><strong>Speak assistant responses</strong><small>Use the existing Windows SAPI voice when you choose Speak in chat.</small></div><button className={`toggle ${tts ? 'on' : ''}`} role="switch" aria-checked={tts} onClick={() => setTts(!tts)}><i /></button></div><div className="settings-toggle-row"><div><strong>Reduce motion</strong><small>Respect the system preference and reduce face movement and interface transitions.</small></div><button className={`toggle ${reducedMotion ? 'on' : ''}`} role="switch" aria-checked={reducedMotion} onClick={() => setReducedMotion(!reducedMotion)}><i /></button></div><div className="settings-footer"><span className="muted small">{settingsLoaded ? 'Preferences saved locally in data/dexter.db.' : 'Loading preferences…'}</span><button className="button primary" onClick={() => void save()} disabled={!settingsLoaded}>{saved ? <Check size={15} /> : <CheckCircle2 size={15} />}{saved ? 'Saved' : 'Save preferences'}</button></div></section>
    <section className="panel settings-section"><div className="eyebrow">SYSTEM DIAGNOSTICS</div><h2>Local runtime</h2><div className="diagnostic-grid"><div><span>Assistant identity</span><strong>Dexter · owner Devil</strong></div><div><span>Application</span><strong>DEVIL local instance</strong></div><div><span>Filename index</span><strong>{appState?.indexes.filename_ready ? `${appState.indexes.filename_files?.toLocaleString()} files` : 'Not built'}</strong></div><div><span>Content index</span><strong>{appState?.indexes.content_files?.toLocaleString() ?? 'Unknown'} files · {appState?.indexes.content_db_bytes ? `${(appState.indexes.content_db_bytes / 1048576).toFixed(1)} MB` : 'size unknown'}</strong></div><div><span>Owner memory</span><strong>{appState?.memory_count ?? 0} saved item(s)</strong></div><div><span>Model training</span><strong>Not implemented · no job running</strong></div><div className="diagnostic-wide"><span>Allowed local roots</span><strong>{appState?.roots?.join(' · ') || 'See data/roots.json'}</strong></div></div></section>
    <div className="privacy-banner"><LockKeyhole size={17} /><div><strong>Provider keys never enter the browser.</strong><p>Chat and voice requests go to the Python backend. Owner memories and conversation history stay in the local DEVIL database.</p></div></div>
  </>
}
