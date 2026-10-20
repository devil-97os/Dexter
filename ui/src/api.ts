export type Message = {
  id: number
  role: 'user' | 'assistant'
  content: string
  provider?: string
  created: string
  excluded_training?: number
}

export type AppState = {
  identity: { application: string; assistant: string; owner: string }
  conversation_id: string
  message_count: number
  providers: Record<string, { configured: boolean; model: string; capability: string }>
  preferences?: { tts_enabled?: boolean; reduced_motion?: boolean }
  indexes: {
    filename_files: number | null
    filename_directories: number | null
    content_files: number | null
    content_db_bytes: number | null
    filename_ready: boolean
  }
  index_job: any
  memory_count: number
  training: { trainer: string; status: string; dataset_candidates: number; pending_review: number; accepted: number; rejected: number; metrics: null }
  voice: { speech_to_text: boolean; text_to_speech: boolean; mode: string }
  recent_events: any[]
  roots?: string[]
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`)
  return payload as T
}

const post = (path: string, body: unknown) => request<any>(path, { method: 'POST', body: JSON.stringify(body) })
const put = (path: string, body: unknown) => request<any>(path, { method: 'PUT', body: JSON.stringify(body) })

export const api = {
  state: () => request<AppState>('/api/state'),
  history: () => request<{ conversation_id: string; messages: Message[] }>('/api/chat/history'),
  conversations: () => request<any>('/api/chat/conversations'),
  send: (message: string, provider: string) => post('/api/chat', { message, provider }),
  compare: (message: string) => post('/api/chat/compare', { message }),
  newConversation: () => post('/api/chat/new', {}),
  selectConversation: (conversation_id: string) => post('/api/chat/select', { conversation_id }),
  searchFiles: (q: string, mode: string) => request<any>(`/api/files/search?q=${encodeURIComponent(q)}&mode=${mode}`),
  readFile: (path: string) => request<any>(`/api/files/read?path=${encodeURIComponent(path)}`),
  startIndex: (kind: string, path?: string) => post('/api/index', { kind, path }),
  cancelIndex: () => post('/api/index/cancel', {}),
  health: () => request<any>('/api/providers/health'),
  confirmations: () => request<{ pending: { id: string; action: string }[] }>('/api/confirmations'),
  answerConfirmation: (id: string, approve: boolean) => post('/api/confirmations/answer', { id, approve }),
  transcribe: (audio_base64: string, mime_type: string) => post('/api/voice/transcribe', { audio_base64, mime_type }),
  speak: (text: string, provider: string) => post('/api/voice/speak', { text, provider }),
  memories: (q = '') => request<any>(`/api/memories?q=${encodeURIComponent(q)}`),
  saveMemory: (body: unknown) => post('/api/memories', body),
  editMemory: (id: number, body: unknown) => put(`/api/memories/${id}`, body),
  deleteMemory: (id: number) => request<any>(`/api/memories/${id}`, { method: 'DELETE' }),
  candidates: () => request<any>('/api/training/candidates'),
  submitCandidate: (message_id: number, category: string) => post('/api/training/candidates', { message_id, category }),
  reviewCandidate: (id: number, body: unknown) => put(`/api/training/candidates/${id}`, body),
  deleteCandidate: (id: number) => request<any>(`/api/training/candidates/${id}`, { method: 'DELETE' }),
  exclusion: (id: number, excluded: boolean) => post(`/api/messages/${id}/exclusion`, { excluded }),
  events: (filters: Record<string, string>) => request<any>(`/api/training/events?${new URLSearchParams(filters)}`),
  settings: () => request<any>('/api/settings'),
  saveSettings: (body: unknown) => post('/api/settings', body),
}
