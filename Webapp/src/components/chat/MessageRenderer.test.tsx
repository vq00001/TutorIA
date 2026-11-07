import { render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { MessageRenderer } from './MessageRenderer'

vi.mock('./MarkdownContent',()=>({MarkdownContent:()=>null}))
it('names the original document while keeping the citation link',()=>{
  render(<MessageRenderer message={{id:'reply',role:'assistant',parts:[],createdAt:new Date(),usesDocuments:true,citations:[{id:'source',status:'available',label:'Fuente 1',documentName:'apuntes.md',contentUrl:'/api/v1/original'}]}}/>)
  expect(screen.getByText('Respuesta basada en documentos del curso.')).toBeInTheDocument()
  expect(screen.getByText(/apuntes.md/)).toBeInTheDocument()
  expect(screen.getByRole('link',{name:'Fuente 1'})).toHaveAttribute('href','/api/v1/original')
})
