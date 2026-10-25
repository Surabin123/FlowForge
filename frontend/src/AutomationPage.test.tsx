import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import AutomationPage from './AutomationPage'

const proposal = {
  id: 'plan-one', request_text: 'Prepare my daily plan', title: 'Daily plan', summary: 'Read real tasks and save a plan.',
  status: 'awaiting_approval', questions: [], proposed_hash: 'a'.repeat(64), workflow_id: null,
  provider_model: 'mock', prompt_version: 'automation-planner-v1', schedule: null,
  definition: { nodes: [
    { id: 'read', name: 'Read tasks', type: 'tool', config: { tool_id: 'tasks.list_open', arguments: { limit: 30 } } },
    { id: 'save', name: 'Save result', type: 'tool', config: { tool_id: 'artifacts.save', arguments: { title: 'Plan', kind: 'daily_plan', source_node: 'read' } } },
  ], edges: [{ source: 'read', target: 'save' }] },
}
const catalog = [
  { id: 'tasks.list_open', name: 'List tasks', description: 'Read stored tasks', permission: 'read', side_effect: 'none', input_schema: {} },
  { id: 'artifacts.save', name: 'Save artifact', description: 'Persist a result', permission: 'write', side_effect: 'local_write', input_schema: {} },
]

afterEach(() => cleanup())

describe('AutomationPage', () => {
  it('requires a review and approval before executing and shows verified output', async () => {
    let current = { ...proposal }
    const calls: string[] = []
    const request = async <T,>(path: string, init?: RequestInit): Promise<T> => {
      calls.push(`${init?.method || 'GET'} ${path}`)
      if (path === '/api/automation/tools') return catalog as T
      if (path === '/api/automation/artifacts') return [] as T
      if (path === '/api/automation/plans' && init?.method === 'POST') return current as T
      if (path === '/api/automation/plans') return (calls.some(call => call.includes('POST /api/automation/plans')) ? [current] : []) as T
      if (path.endsWith('/approve')) { current = { ...current, status: 'approved' }; return current as T }
      if (path.endsWith('/run')) return { id: 'run-one', status: 'running' } as T
      if (path === '/api/runs/run-one') return { id: 'run-one', status: 'completed', error: null, nodes: [{ node_id: 'read', node_name: 'Read tasks', status: 'completed', output: JSON.stringify({ tasks: [{ id: 'task-one' }], verification: 'verified' }), error: null, failure_reason: null, attempts: 1 }] } as T
      if (path === '/api/automation/plans/plan-one') return current as T
      throw Error(`Unexpected ${path}`)
    }
    render(<AutomationPage request={request} refresh={async () => {}} onHistory={() => {}} />)
    fireEvent.change(screen.getByLabelText('Your request'), { target: { value: 'Prepare a daily plan from my actual tasks' } })
    fireEvent.click(screen.getByRole('button', { name: 'Propose plan' }))
    expect(await screen.findByRole('heading', { name: 'Daily plan' })).toBeInTheDocument()
    expect(screen.getByText('Reads workspace')).toBeInTheDocument()
    expect(screen.getByText('Writes workspace')).toBeInTheDocument()
    expect(calls).not.toContain('POST /api/automation/plans/plan-one/run')
    fireEvent.click(screen.getByRole('button', { name: 'Approve exact plan' }))
    expect(await screen.findByRole('button', { name: 'Run now' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Run now' }))
    await waitFor(() => expect(calls).toContain('POST /api/automation/plans/plan-one/run'))
    expect(await screen.findByText(/Run run-one · Succeeded/)).toBeInTheDocument()
    expect(screen.getByText('Verified result')).toBeInTheDocument()
  })

  it('shows the provider configuration failure without claiming a plan was created', async () => {
    const request = async <T,>(path: string, init?: RequestInit): Promise<T> => {
      if (path === '/api/automation/tools') return catalog as T
      if (path === '/api/automation/artifacts' || path === '/api/automation/plans' && !init) return [] as T
      throw Error('Configure FLOWFORGE_AI_API_KEY and FLOWFORGE_AI_MODEL')
    }
    render(<AutomationPage request={request} refresh={async () => {}} onHistory={() => {}} />)
    fireEvent.change(screen.getByLabelText('Your request'), { target: { value: 'Prepare my daily plan from tasks' } })
    fireEvent.click(screen.getByRole('button', { name: 'Propose plan' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Configure FLOWFORGE_AI_API_KEY')
    expect(screen.getByText(/No proposals yet/)).toBeInTheDocument()
  })

  it('shows a failed step and retries an eligible run through the API', async () => {
    const calls: string[] = []
    const approved = { ...proposal, status: 'approved' }
    const request = async <T,>(path: string, init?: RequestInit): Promise<T> => {
      calls.push(`${init?.method || 'GET'} ${path}`)
      if (path === '/api/automation/tools') return catalog as T
      if (path === '/api/automation/artifacts') return [] as T
      if (path === '/api/automation/plans') return [approved] as T
      if (path.endsWith('/run')) return { id: 'failed-run' } as T
      if (path === '/api/runs/failed-run') return { id: 'failed-run', status: 'failed', error: 'The task read failed', nodes: [{ node_id: 'read', node_name: 'Read tasks', status: 'failed', output: null, error: null, failure_reason: 'Database temporarily unavailable', attempts: 1 }] } as T
      if (path.endsWith('/retry')) return { id: 'retry-run' } as T
      if (path === '/api/runs/retry-run') return { id: 'retry-run', status: 'completed', error: null, nodes: [] } as T
      throw Error(`Unexpected ${path}`)
    }
    render(<AutomationPage request={request} refresh={async () => {}} onHistory={() => {}} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Run now' }))
    expect(await screen.findByText('Database temporarily unavailable')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Retry eligible run' }))
    await waitFor(() => expect(calls).toContain('POST /api/runs/failed-run/retry'))
    expect(await screen.findByText(/Run retry-ru · Succeeded/)).toBeInTheDocument()
  })
})
