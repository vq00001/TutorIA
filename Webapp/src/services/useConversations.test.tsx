import { act, renderHook, waitFor } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { useConversations } from './useConversations'

const m=vi.hoisted(()=>({request:vi.fn(),allPages:vi.fn()}))
vi.mock('./http',()=>m)

it('does not show a late response from the previously selected conversation',async()=>{
  m.allPages.mockResolvedValue({data:[{id:'first',title:'Uno'},{id:'second',title:'Dos'}]})
  let finish!:(value:unknown)=>void
  const delayed=new Promise(resolve=>{finish=resolve})
  m.request.mockImplementation((path:string)=>path.includes('/first/')?delayed:Promise.resolve({data:[{id:'new',sequenceNo:1,role:'user',parts:[],createdAt:'2026-11-06T12:00:00Z'}]}))
  const {result}=renderHook(()=>useConversations('course'))
  await waitFor(()=>expect(result.current.selected).toBe('first'))
  act(()=>result.current.select('second'))
  await waitFor(()=>expect(result.current.messages[0]?.id).toBe('new'))
  await act(async()=>{finish({data:[{id:'old',sequenceNo:1,role:'user',parts:[],createdAt:'2026-11-06T12:00:00Z'}]});await delayed})
  expect(result.current.messages.map(m=>m.id)).toEqual(['new'])
})
