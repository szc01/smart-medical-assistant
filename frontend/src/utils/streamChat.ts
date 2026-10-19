export type StreamHandlers = {
  onMeta?: (agent: string, label: string) => void
  onToken: (text: string) => void
  onError: (detail: string) => void
}

export async function streamChat(opts: {
  url: string
  token: string
  body: Record<string, unknown>
  handlers: StreamHandlers
  signal?: AbortSignal
}): Promise<void> {
  const resp = await fetch(opts.url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${opts.token}`,
    },
    body: JSON.stringify(opts.body),
    signal: opts.signal,
  })
  if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`)

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    // stream: true 必须加，否则一个汉字被切在两个 chunk 之间会变乱码
    buffer += decoder.decode(value, { stream: true })
    const parts = buffer.split('\n\n')
    buffer = parts.pop() ?? ''          // 最后一段可能是半个事件，留到下一轮
    for (const part of parts) {
      const line = part.trim()
      if (!line.startsWith('data:')) continue
      let evt: any
      try {
        evt = JSON.parse(line.slice(5).trim())
      } catch {
        continue                        // 解析失败的行直接跳过，不要整条流崩掉
      }
      if (evt.type === 'meta') opts.handlers.onMeta?.(evt.agent, evt.label)
      else if (evt.type === 'token') opts.handlers.onToken(evt.text)
      else if (evt.type === 'error') opts.handlers.onError(evt.detail)
    }
  }
}
