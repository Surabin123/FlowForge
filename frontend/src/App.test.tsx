import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import App from './App'

const store={tasks:[],subjects:[],sessions:[],documents:[],workflows:[],runs:[],agents:[],notifications:[],settings:{}} as {
 tasks:{id:string;title:string;description:string;due_at:string|null;priority:string;category:string;status:string;recurrence:string|null;reminder_at:string|null;reminder_enabled?:boolean|number;completed_at:string|null}[]
 subjects:never[];sessions:never[];documents:never[];workflows:never[];runs:never[];agents:never[];notifications:never[];settings:Record<string,string>
}

beforeEach(()=>{
 localStorage.clear();store.tasks=[];store.notifications=[]
 vi.stubGlobal('fetch',vi.fn(async(input:RequestInfo|URL,init?:RequestInit)=>{
  const url=String(input),method=init?.method||'GET'
  let result:unknown={}
  if(url==='/api/bootstrap')result=store
  else if(url==='/api/tasks'&&method==='POST'){
   const body=JSON.parse(String(init?.body));const task={id:`task-${store.tasks.length+1}`,description:'',due_at:null,priority:'medium',category:'personal',status:'open',recurrence:null,reminder_at:null,completed_at:null,...body};store.tasks.push(task);result=task
  }else if(url.startsWith('/api/tasks/')&&method==='PATCH'){
   const id=url.split('/').at(-1)!;const task=store.tasks.find(x=>x.id===id)!;Object.assign(task,JSON.parse(String(init?.body)));if(task.status==='completed')task.completed_at=new Date().toISOString();result=task
  }else result={deleted:true}
  return {ok:true,status:200,json:async()=>result} as Response
 }))
})
afterEach(()=>{cleanup();vi.unstubAllGlobals()})

describe('FlowForge task workspace',()=>{
 it('creates a task and displays the persisted row',async()=>{
  render(<App />)
  await screen.findByRole('heading',{name:/Good (morning|afternoon|evening)/})
  fireEvent.click(screen.getByRole('button',{name:'Tasks & Reminders'}))
  fireEvent.click(screen.getAllByRole('button',{name:/New task/})[1])
  fireEvent.change(screen.getByPlaceholderText('e.g. Submit project outline'),{target:{value:'Revise chemistry'}})
  fireEvent.click(screen.getByRole('button',{name:'Save'}))
  const row=await screen.findByText('Revise chemistry')
  expect(row).toBeInTheDocument()
  expect(store.tasks).toHaveLength(1)
  expect(store.tasks[0].title).toBe('Revise chemistry')
 })

 it('completes a task through its status control',async()=>{
  store.tasks.push({id:'task-1',title:'Submit notes',description:'',due_at:null,priority:'medium',category:'study',status:'open',recurrence:null,reminder_at:null,completed_at:null})
  render(<App />)
  await screen.findByRole('heading',{name:/Good (morning|afternoon|evening)/})
  fireEvent.click(screen.getByRole('button',{name:'Tasks & Reminders'}))
  const row=screen.getByText('Submit notes').closest('.table-row') as HTMLElement
  fireEvent.click(within(row).getByRole('button',{name:'Toggle completion'}))
  await waitFor(()=>expect(store.tasks[0].status).toBe('completed'))
 })

 it('saves a recurring reminder and its enabled state',async()=>{
  render(<App />)
  await screen.findByRole('heading',{name:/Good (morning|afternoon|evening)/})
  fireEvent.click(screen.getByRole('button',{name:'Tasks & Reminders'}))
  fireEvent.click(screen.getAllByRole('button',{name:/New task/})[1])
  fireEvent.change(screen.getByPlaceholderText('e.g. Submit project outline'),{target:{value:'Review flash cards'}})
  fireEvent.change(screen.getByLabelText('Reminder at'),{target:{value:'2026-10-22T13:00'}})
  fireEvent.change(screen.getByLabelText('Repeat reminder'),{target:{value:'weekly'}})
  fireEvent.click(screen.getByRole('button',{name:'Save'}))
  await waitFor(()=>expect(store.tasks[0].reminder_at).toBeTruthy())
  expect(store.tasks[0].recurrence).toBe('weekly')
  expect(store.tasks[0].reminder_enabled).toBe(true)
 })
})
