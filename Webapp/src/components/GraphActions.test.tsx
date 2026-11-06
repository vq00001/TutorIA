import { fireEvent,render,screen,waitFor } from '@testing-library/react'
import { beforeEach,expect,it,vi } from 'vitest'
import { GraphActions } from './GraphActions'

const { request }=vi.hoisted(()=>({request:vi.fn()}))
vi.mock('../services/http',()=>({request}))
beforeEach(()=>{request.mockReset()})

it('loads an existing plan and labels its stale or retired state',async()=>{
  request.mockResolvedValue({data:{stale:true,status:'blocked',steps:[{concept_id:'c',concept_label:'Concepto retirado',position:1,state:'blocked',reason:'RETIRED_CONCEPT',estimated_minutes:20}]}})
  render(<GraphActions courseId="course" coordinator={false}/>)
  expect(await screen.findByRole('heading',{name:/Mi ruta.*pendiente de actualizar/})).toBeVisible()
  expect(screen.getByText(/concepto retirado/)).toBeVisible()
  expect(request).toHaveBeenCalledWith('/courses/course/roadmaps/current')
})

it('requests the default plan without creating another custom goal',async()=>{
  let built=false
  request.mockImplementation(async(_path:string,options?:RequestInit)=>{
    if(options?.method==='POST'){built=true;return {data:{id:'route',goalId:'default'}}}
    return {data:built?{stale:false,status:'active',steps:[]}:null}
  })
  render(<GraphActions courseId="course" coordinator={false}/>)
  await waitFor(()=>expect(request).toHaveBeenCalledWith('/courses/course/roadmaps/current'))
  fireEvent.click(screen.getByRole('button',{name:'Crear mi ruta de estudio'}))
  expect(await screen.findByRole('heading',{name:'Mi ruta'})).toBeVisible()
  expect(request).toHaveBeenCalledWith('/courses/course/roadmaps',{method:'POST',body:'{}'})
  expect(request.mock.calls.some(([path])=>String(path).includes('/goals'))).toBe(false)
})
