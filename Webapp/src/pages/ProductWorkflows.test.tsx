import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, expect, it, vi } from 'vitest'
import { AccountPage } from './AccountPage'
import { CourseSettingsPage } from './CourseSettingsPage'
import { InvitationsPage } from './InvitationsPage'
import { ActivityPage } from './ActivityPage'
import { ArtifactsPage } from './ArtifactsPage'

const m=vi.hoisted(()=>({request:vi.fn(),allPages:vi.fn(),refreshAccount:vi.fn(),profile:{id:'me',nickname:'student',displayName:'Student',email:'me@example.com',timezone:'America/Santiago',rowVersion:3},courses:[{id:'course',name:'Biología',description:'Descripción',objectives:'Aprender',myRole:'coordinator',rowVersion:7}]}))
vi.mock('../services/http',()=>({request:m.request,allPages:m.allPages}))
vi.mock('../contexts/AppDataContext',()=>({useAppData:()=>m}))
vi.mock('../components/chat/MarkdownContent',()=>({MarkdownContent:({content}:{content:string})=><p>{content}</p>}))
beforeEach(()=>{vi.clearAllMocks();m.refreshAccount.mockResolvedValue(undefined)})
function atCourse(element:React.ReactNode) {return render(<MemoryRouter initialEntries={['/cursos/course']}><Routes><Route path="/cursos/:courseId" element={element}/></Routes></MemoryRouter>)}

it('saves public profile with version and does not send sensitive fields',async()=>{
  m.request.mockResolvedValue({data:{}})
  render(<AccountPage/>)
  fireEvent.change(screen.getByLabelText('Nombre visible'),{target:{value:'Nuevo nombre'}})
  fireEvent.click(screen.getByRole('button',{name:'Guardar perfil'}))
  await screen.findByText('Perfil actualizado.')
  expect(m.request).toHaveBeenCalledWith('/student/me',{method:'PATCH',headers:{'If-Match':'3'},body:JSON.stringify({displayName:'Nuevo nombre',nickname:'student',timezone:'America/Santiago'})})
  expect(m.refreshAccount).toHaveBeenCalled()
})

it('requires matching nickname before allowing account deletion',()=>{
  render(<AccountPage/>)
  const button=screen.getByRole('button',{name:'Solicitar eliminación de cuenta'})
  expect(button).toBeDisabled()
  fireEvent.change(screen.getByLabelText('Escribe tu nickname para confirmar'),{target:{value:'student'}})
  expect(button).toBeEnabled()
})

it('reports a failed course departure and keeps the member list visible',async()=>{
  m.request.mockResolvedValueOnce({data:[{id:'me',displayName:'Student',nickname:'student',role:'coordinator',rowVersion:1}]}).mockRejectedValueOnce(new Error('LAST_COORDINATOR'))
  vi.spyOn(window,'confirm').mockReturnValueOnce(true)
  atCourse(<CourseSettingsPage/>)
  await screen.findByText('Student')
  fireEvent.click(screen.getByRole('button',{name:'Salir del curso'}))
  expect(await screen.findByRole('status')).toHaveTextContent('LAST_COORDINATOR')
  expect(m.refreshAccount).not.toHaveBeenCalled()
})

it.each([true,false])('responds to a directed invitation: accept=%s',async accept=>{
  const invitation={id:'invite',courseId:'course',courseName:'Biología',status:'pending',expiresAt:'2026-12-01T00:00:00Z'}
  m.request.mockResolvedValueOnce({data:[invitation]}).mockResolvedValueOnce({data:{}}).mockResolvedValueOnce({data:[]})
  render(<MemoryRouter><InvitationsPage/></MemoryRouter>)
  fireEvent.click(await screen.findByRole('button',{name:`${accept?'Aceptar':'Rechazar'} Biología`}))
  await waitFor(()=>expect(m.refreshAccount).toHaveBeenCalled())
  expect(m.request).toHaveBeenCalledWith('/invitations/invite/respond',{method:'POST',body:JSON.stringify({accept})})
})

it('marks only the selected notification as read',async()=>{
  m.allPages.mockResolvedValue({data:[{id:'event',action:'invitation.received',createdAt:'2026-11-06T12:00:00Z',readAt:null}]})
  m.request.mockResolvedValue({data:{readAt:'2026-11-06T12:01:00Z'}})
  render(<MemoryRouter><ActivityPage notifications/></MemoryRouter>)
  fireEvent.click(await screen.findByRole('button',{name:'Marcar como leída'}))
  await waitFor(()=>expect(screen.queryByRole('button',{name:'Marcar como leída'})).not.toBeInTheDocument())
  expect(m.request).toHaveBeenCalledWith('/notifications/event/read',{method:'POST',body:'{}'})
})

it('displays generated materials with their original document links',async()=>{
  m.allPages.mockResolvedValue({data:[{id:'artifact',title:'Resumen',content:{markdown:'Contenido generado'},createdAt:'2026-11-06T12:00:00Z',usedDocuments:[{id:'source',name:'guia.md',contentUrl:'/api/v1/original'}]}]})
  atCourse(<ArtifactsPage/>)
  await screen.findByText('Contenido generado')
  expect(screen.getByRole('link',{name:'guia.md'})).toHaveAttribute('href','/api/v1/original')
})
