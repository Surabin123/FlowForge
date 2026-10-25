import { useCallback, useEffect, useState } from 'react'
import { AlertCircle, ArrowRight, Check, Clock3, FileText, Plus, RefreshCw, Save, ShieldCheck, Sparkles, X } from 'lucide-react'

type Request = <T,>(path: string, init?: RequestInit) => Promise<T>
type Tool = { id: string; name: string; description: string; permission: string; side_effect: string; input_schema: Record<string, unknown> }
type Step = { id: string; name: string; type: 'tool' | 'manual'; config: { tool_id?: string; arguments?: Record<string, unknown>; message?: string } }
type Schedule = { starts_at: string; recurrence: string | null; timezone: string } | null
type Plan = {
  id: string; request_text: string; title: string; summary: string; status: string; questions: string[];
  definition: { nodes: Step[]; edges: { source: string; target: string }[] }; schedule: Schedule;
  proposed_hash: string | null; workflow_id: string | null; provider_model: string | null; prompt_version: string;
}
type EditStep = { id: string; name: string; kind: 'tool' | 'approval'; tool_id: string; argumentsText: string; depends_on: string; approval_message: string }
type RunNode = { node_id: string; node_name: string; status: string; output: string | null; error: string | null; failure_reason: string | null; attempts: number }
type Run = { id: string; status: string; error: string | null; nodes: RunNode[] }
type Artifact = { id: string; title: string; kind: string; created_at: string; source_run_id: string; content?: Record<string, unknown> }

const statusText: Record<string, string> = {
  needs_clarification: 'Needs details', awaiting_approval: 'Ready for review', approved: 'Approved',
  cancelled: 'Cancelled', running: 'Running', completed: 'Succeeded', failed: 'Failed', paused: 'Awaiting approval',
}

const toEdits = (plan: Plan): EditStep[] => plan.definition.nodes.map(node => ({
  id: node.id, name: node.name, kind: node.type === 'manual' ? 'approval' : 'tool',
  tool_id: node.config.tool_id || '', argumentsText: JSON.stringify(node.config.arguments || {}, null, 2),
  depends_on: plan.definition.edges.filter(edge => edge.target === node.id).map(edge => edge.source).join(', '),
  approval_message: node.config.message || '',
}))

function outputFor(node: RunNode): Record<string, unknown> | null {
  if (!node.output) return null
  try { return JSON.parse(node.output) as Record<string, unknown> } catch { return null }
}

export default function AutomationPage({ request, refresh, onHistory }: { request: Request; refresh: () => Promise<void>; onHistory: () => void }) {
  const [intent, setIntent] = useState('')
  const [plans, setPlans] = useState<Plan[]>([])
  const [selected, setSelected] = useState<Plan | null>(null)
  const [tools, setTools] = useState<Tool[]>([])
  const [artifacts, setArtifacts] = useState<Artifact[]>([])
  const [artifact, setArtifact] = useState<Artifact | null>(null)
  const [runId, setRunId] = useState<string | null>(null)
  const [run, setRun] = useState<Run | null>(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [editing, setEditing] = useState(false)
  const [editTitle, setEditTitle] = useState('')
  const [editSummary, setEditSummary] = useState('')
  const [editSteps, setEditSteps] = useState<EditStep[]>([])
  const [scheduleAt, setScheduleAt] = useState('')
  const [recurrence, setRecurrence] = useState('')
  const [timezone, setTimezone] = useState('Asia/Calcutta')

  const load = useCallback(async () => {
    const [saved, catalog, generated] = await Promise.all([
      request<Plan[]>('/api/automation/plans'), request<Tool[]>('/api/automation/tools'),
      request<Artifact[]>('/api/automation/artifacts'),
    ])
    setPlans(saved); setTools(catalog); setArtifacts(generated)
    setSelected(current => current ? saved.find(plan => plan.id === current.id) || current : saved[0] || null)
  }, [request])

  useEffect(() => { void load().catch(err => setError(err instanceof Error ? err.message : 'Could not load automations')) }, [load])

  useEffect(() => {
    if (!runId || (run && ['completed', 'failed', 'cancelled'].includes(run.status))) return
    const poll = async () => {
      try {
        const current = await request<Run>(`/api/runs/${runId}`)
        setRun(current)
        if (['completed', 'failed', 'cancelled'].includes(current.status)) {
          await refresh(); await load()
        }
      } catch (err) { setError(err instanceof Error ? err.message : 'Could not refresh the run') }
    }
    void poll()
    const timer = window.setInterval(() => void poll(), 1500)
    return () => window.clearInterval(timer)
  }, [runId, run?.status, request, refresh, load])

  const beginEdit = () => {
    if (!selected) return
    setEditTitle(selected.title); setEditSummary(selected.summary); setEditSteps(toEdits(selected))
    setScheduleAt(selected.schedule?.starts_at || ''); setRecurrence(selected.schedule?.recurrence || '')
    setTimezone(selected.schedule?.timezone || 'Asia/Calcutta'); setEditing(true); setError('')
  }
  const updateStep = (index: number, field: keyof EditStep, value: string) => setEditSteps(items => items.map((item, position) => position === index ? { ...item, [field]: value } : item))
  const execute = async (name: string, operation: () => Promise<void>) => {
    setBusy(name); setError(''); setMessage('')
    try { await operation() } catch (err) { setError(err instanceof Error ? err.message : 'Automation request failed') }
    finally { setBusy('') }
  }
  const propose = (event: React.FormEvent) => {
    event.preventDefault()
    void execute('propose', async () => {
      const plan = await request<Plan>('/api/automation/plans', { method: 'POST', body: JSON.stringify({ request: intent }) })
      setSelected(plan); setEditing(false); setMessage(plan.questions.length ? 'FlowForge needs a detail before it can propose executable steps.' : 'Proposal ready for review. Nothing has run yet.')
      await load()
    })
  }
  const saveEdit = () => {
    if (!selected) return
    void execute('edit', async () => {
      const steps = editSteps.map(step => ({
        id: step.id.trim(), name: step.name.trim(), kind: step.kind,
        tool_id: step.kind === 'tool' ? step.tool_id : null,
        arguments: step.kind === 'tool' ? JSON.parse(step.argumentsText) : {},
        depends_on: step.depends_on.split(',').map(value => value.trim()).filter(Boolean),
        approval_message: step.kind === 'approval' ? step.approval_message || null : null,
      }))
      const updated = await request<Plan>(`/api/automation/plans/${selected.id}`, {
        method: 'PUT', body: JSON.stringify({ title: editTitle, summary: editSummary, steps,
          schedule: scheduleAt.trim() ? { starts_at: scheduleAt.trim(), recurrence: recurrence || null, timezone: timezone.trim() } : null,
          clarifying_questions: [] }),
      })
      setSelected(updated); setEditing(false); setMessage('Updated plan ready for a fresh review and approval.')
      await load()
    })
  }
  const approve = () => {
    if (!selected?.proposed_hash) return
    void execute('approve', async () => {
      const approved = await request<Plan>(`/api/automation/plans/${selected.id}/approve`, { method: 'POST', body: JSON.stringify({ proposed_hash: selected.proposed_hash }) })
      setSelected(approved); setMessage('The exact steps and schedule are approved. No run has started.')
      await load()
    })
  }
  const save = () => {
    if (!selected) return
    void execute('save', async () => {
      await request(`/api/automation/plans/${selected.id}/save`, { method: 'POST' })
      setSelected(await request<Plan>(`/api/automation/plans/${selected.id}`))
      setMessage(selected.schedule ? 'Automation saved. Its schedule uses the existing FlowForge scheduler.' : 'Automation saved. You can run it manually.')
      await refresh(); await load()
    })
  }
  const start = () => {
    if (!selected) return
    void execute('run', async () => {
      const started = await request<{ id: string }>(`/api/automation/plans/${selected.id}/run`, { method: 'POST' })
      setRunId(started.id); setRun(null); setMessage('Run started. Results will appear below after each step finishes.')
      await refresh(); await load()
    })
  }
  const cancel = () => {
    if (!selected) return
    void execute('cancel', async () => {
      const cancelled = await request<Plan>(`/api/automation/plans/${selected.id}/cancel`, { method: 'POST' })
      setSelected(cancelled); setMessage('Plan cancelled before execution.'); await load()
    })
  }
  const cancelRun = () => {
    if (!runId) return
    void execute('cancel-run', async () => {
      await request(`/api/runs/${runId}/cancel`, { method: 'POST' })
      setRun(await request<Run>(`/api/runs/${runId}`)); setMessage('The run was cancelled.')
      await refresh()
    })
  }
  const retryRun = () => {
    if (!runId) return
    void execute('retry-run', async () => {
      const restarted = await request<{ id: string }>(`/api/runs/${runId}/retry`, { method: 'POST' })
      setRunId(restarted.id); setRun(null); setMessage('A new run was started. Prior read-only steps will execute again.')
      await refresh()
    })
  }
  const showArtifact = (id: string) => void execute('artifact', async () => setArtifact(await request<Artifact>(`/api/automation/artifacts/${id}`)))

  return <div className="automation-page">
    <section className="panel automation-composer">
      <div className="panel-head"><div><div className="section-kicker"><Sparkles size={14}/> PLAN AN AUTOMATION</div><h2>What would you like FlowForge to do?</h2><p>Describe the goal in plain language. You will review every step before it can run.</p></div></div>
      <form onSubmit={propose}><label className="field-label" htmlFor="automation-request">Your request</label><textarea id="automation-request" required minLength={10} maxLength={3000} rows={4} value={intent} onChange={event => setIntent(event.target.value)} placeholder="Every weekday morning, list my open tasks, prioritize them, and save a daily plan."/><div className="automation-compose-foot"><span>Uses your configured AI provider to propose a plan. It does not execute the request.</span><button className="btn primary" disabled={!!busy || intent.trim().length < 10} type="submit">{busy === 'propose' ? <RefreshCw size={15} className="spin"/> : <Sparkles size={15}/>} Propose plan</button></div></form>
    </section>
    {error && <div className="error-banner" role="alert"><AlertCircle size={16}/>{error}<button onClick={() => setError('')}>Dismiss</button></div>}
    {message && <div className="automation-message" role="status"><Check size={16}/>{message}</div>}
    <div className="automation-columns">
      <aside className="panel automation-saved"><div className="panel-head"><div><h2>Saved proposals</h2><p>Review, approve, or revisit</p></div></div>{plans.length ? <div className="automation-plan-list">{plans.map(plan => <button key={plan.id} className={`automation-plan-row ${selected?.id === plan.id ? 'selected' : ''}`} onClick={() => { setSelected(plan); setEditing(false); setRun(null); setRunId(null); setArtifact(null); setError('') }}><span><b>{plan.title}</b><small>{statusText[plan.status] || plan.status}{plan.workflow_id ? ' · saved workflow' : ''}</small></span><ArrowRight size={15}/></button>)}</div> : <div className="empty-inline">No proposals yet. Describe a task above to begin.</div>}</aside>
      <div className="automation-main">
        {selected ? <section className="panel automation-review"><div className="panel-head"><div><div className="section-kicker">PROPOSED EXECUTION</div><h2>{selected.title}</h2><p>{selected.summary}</p></div><span className={`mini-status ${selected.status === 'approved' ? 'completed' : ''}`}>{statusText[selected.status] || selected.status}</span></div>
          {selected.questions.length > 0 && <div className="automation-questions"><b>Details needed before execution</b>{selected.questions.map(question => <p key={question}>{question}</p>)}<span>Edit the plan when you can provide the missing details.</span></div>}
          {!editing ? <><ol className="automation-steps">{selected.definition.nodes.map((node, index) => { const tool = tools.find(item => item.id === node.config.tool_id); const parents = selected.definition.edges.filter(edge => edge.target === node.id).map(edge => edge.source); return <li key={node.id}><span className="automation-step-number">{index + 1}</span><div><div className="automation-step-heading"><b>{node.name}</b><span className={`automation-effect ${tool?.side_effect || 'none'}`}>{node.type === 'manual' ? 'Approval gate' : tool?.side_effect === 'none' ? 'Reads workspace' : tool?.side_effect === 'local_write' ? 'Writes workspace' : 'Uses AI provider'}</span></div><p>{node.type === 'manual' ? node.config.message : tool?.description || node.config.tool_id}</p>{parents.length > 0 && <small>After {parents.join(', ')}</small>}{node.type === 'tool' && <details><summary>Exact tool and parameters</summary><code>{node.config.tool_id} · v{tool?.id ? '1' : '?'}</code><pre>{JSON.stringify(node.config.arguments || {}, null, 2)}</pre></details>}</div></li> })}</ol>
            {selected.schedule && <div className="automation-schedule"><Clock3 size={17}/><span><b>{selected.schedule.recurrence || 'One time'}</b><small>First run {selected.schedule.starts_at} · {selected.schedule.timezone}</small></span></div>}
            {selected.status === 'awaiting_approval' && <p className="automation-approval-note"><ShieldCheck size={16}/> Approval applies to the exact steps, targets, parameters, and schedule shown above. Editing invalidates it.</p>}
            <div className="automation-actions">{!selected.workflow_id && selected.status !== 'cancelled' && <button className="btn secondary" disabled={!!busy} onClick={beginEdit}>Edit steps</button>}{selected.status === 'awaiting_approval' && <button className="btn primary" disabled={!!busy} onClick={approve}><ShieldCheck size={15}/> Approve exact plan</button>}{selected.status === 'approved' && <><button className="btn secondary" disabled={!!busy} onClick={save}><Save size={15}/> Save{selected.schedule ? ' schedule' : ''}</button><button className="btn primary" disabled={!!busy} onClick={start}><ArrowRight size={15}/> Run now</button></>}{!selected.workflow_id && ['awaiting_approval', 'needs_clarification'].includes(selected.status) && <button className="btn quiet" disabled={!!busy} onClick={cancel}>Cancel proposal</button>}</div></> : <div className="automation-editor"><label className="field-label">Plan title<input value={editTitle} onChange={event => setEditTitle(event.target.value)}/></label><label className="field-label">Expected result<textarea rows={2} value={editSummary} onChange={event => setEditSummary(event.target.value)}/></label><div className="automation-editor-title"><b>Steps</b><div><button className="text-action" onClick={() => setEditSteps(items => [...items, { id: `step${crypto.randomUUID().slice(0, 6)}`, name: 'New step', kind: 'tool', tool_id: tools[0]?.id || '', argumentsText: '{}', depends_on: items.at(-1)?.id || '', approval_message: '' }])}><Plus size={14}/> Tool step</button><button className="text-action" onClick={() => setEditSteps(items => [...items, { id: `step${crypto.randomUUID().slice(0, 6)}`, name: 'Review', kind: 'approval', tool_id: '', argumentsText: '{}', depends_on: items.at(-1)?.id || '', approval_message: 'Approve the next step' }])}><Plus size={14}/> Approval gate</button></div></div>{editSteps.map((step, index) => <div className="automation-edit-step" key={`${step.id}-${index}`}><div className="automation-edit-head"><b>Step {index + 1}</b><button className="icon-button" aria-label={`Remove step ${index + 1}`} onClick={() => setEditSteps(items => items.filter((_, position) => position !== index))}><X size={14}/></button></div><div className="field-grid"><label className="field-label">ID<input value={step.id} onChange={event => updateStep(index, 'id', event.target.value)}/></label><label className="field-label">Name<input value={step.name} onChange={event => updateStep(index, 'name', event.target.value)}/></label></div><label className="field-label">Type<select value={step.kind} onChange={event => updateStep(index, 'kind', event.target.value)}><option value="tool">Registered tool</option><option value="approval">Approval gate</option></select></label>{step.kind === 'tool' ? <><label className="field-label">Tool<select value={step.tool_id} onChange={event => updateStep(index, 'tool_id', event.target.value)}>{tools.map(tool => <option value={tool.id} key={tool.id}>{tool.name} · {tool.id}</option>)}</select></label><label className="field-label">Parameters (JSON)<textarea rows={4} spellCheck={false} value={step.argumentsText} onChange={event => updateStep(index, 'argumentsText', event.target.value)}/></label></> : <label className="field-label">Approval message<input value={step.approval_message} onChange={event => updateStep(index, 'approval_message', event.target.value)}/></label>}<label className="field-label">After step IDs (comma separated)<input value={step.depends_on} onChange={event => updateStep(index, 'depends_on', event.target.value)} placeholder="Leave blank for the first step"/></label></div>)}<div className="field-grid"><label className="field-label">First run (ISO 8601 with offset)<input value={scheduleAt} onChange={event => setScheduleAt(event.target.value)} placeholder="2026-10-26T09:00:00+05:30"/></label><label className="field-label">Repeat<select value={recurrence} onChange={event => setRecurrence(event.target.value)}><option value="">One time</option><option value="daily">Daily</option><option value="weekdays">Weekdays</option><option value="weekly">Weekly</option></select></label></div><label className="field-label">Schedule timezone<input value={timezone} onChange={event => setTimezone(event.target.value)} placeholder="Asia/Calcutta"/></label><div className="automation-actions"><button className="btn secondary" onClick={() => setEditing(false)}>Discard edits</button><button className="btn primary" disabled={!!busy || !editSteps.length} onClick={saveEdit}>{busy === 'edit' ? <RefreshCw size={14} className="spin"/> : <Save size={14}/>} Validate and save edits</button></div></div>}
        </section> : <section className="panel automation-empty"><Sparkles size={24}/><h2>Plan a useful action</h2><p>FlowForge will propose steps using registered tools and show exactly what would be read or changed before you approve.</p></section>}
        {run && <section className="panel automation-run" aria-live="polite"><div className="panel-head"><div><h2>Current execution</h2><p>Run {run.id.slice(0, 8)} · {statusText[run.status] || run.status}</p></div><div className="automation-run-actions">{['running', 'paused'].includes(run.status) && <button className="btn secondary" disabled={!!busy} onClick={cancelRun}>Cancel run</button>}{run.status === 'failed' && <button className="btn secondary" disabled={!!busy} onClick={retryRun}><RefreshCw size={14}/> Retry eligible run</button>}</div></div>{run.error && <div className="error-banner"><AlertCircle size={15}/>{run.error}</div>}<ol>{run.nodes.map(node => { const result = outputFor(node); return <li key={node.node_id}><span className={`automation-run-state ${node.status}`}>{node.status === 'completed' ? <Check size={14}/> : node.status === 'running' ? <RefreshCw size={14} className="spin"/> : <Clock3 size={14}/>}</span><div><b>{node.node_name}</b><small>{statusText[node.status] || node.status} · {node.attempts} attempt{node.attempts === 1 ? '' : 's'}</small>{node.failure_reason && <p className="automation-failure">{node.failure_reason}</p>}{result && <details><summary>{result.verification === 'verified' ? 'Verified result' : 'Candidate output'}</summary><pre>{JSON.stringify(result, null, 2)}</pre></details>}</div></li> })}</ol><button className="text-action" onClick={onHistory}>Open execution history <ArrowRight size={14}/></button></section>}
        <section className="panel automation-artifacts"><div className="panel-head"><div><h2>Generated artifacts</h2><p>Saved outputs verified in local storage</p></div><FileText size={17}/></div>{artifacts.length ? <div className="automation-artifact-list">{artifacts.map(item => <button key={item.id} onClick={() => showArtifact(item.id)}><FileText size={16}/><span><b>{item.title}</b><small>{item.kind.replace('_', ' ')} · {new Date(item.created_at).toLocaleDateString()}</small></span><ArrowRight size={14}/></button>)}</div> : <div className="empty-inline">No saved artifacts yet.</div>}{artifact && <div className="automation-artifact-detail"><div><b>{artifact.title}</b><button className="icon-button" aria-label="Close artifact" onClick={() => setArtifact(null)}><X size={14}/></button></div><pre>{JSON.stringify(artifact.content, null, 2)}</pre></div>}</section>
      </div>
    </div>
  </div>
}
