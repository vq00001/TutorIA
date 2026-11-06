import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { expect, it, vi } from 'vitest'
import { MaterialsPage } from './MaterialsPage'

const { addMaterials }=vi.hoisted(()=>({addMaterials:vi.fn()}))
vi.mock('../contexts/AppDataContext',()=>({useAppData:()=>({
  status:'ready',courses:[{id:'course',name:'Synthetic'}],materialsByCourse:{},
  addMaterials,removeMaterial:vi.fn(),
})}))

it('uploads valid files from a mixed selection and names every rejected file',async()=>{
  addMaterials.mockResolvedValue({materials:[],results:[{name:'good.md',data:{materialId:'accepted',status:'queued'}}]})
  const {container}=render(<MemoryRouter><MaterialsPage/></MemoryRouter>)
  const valid=new File(['# valid'],'good.md')
  const invalid=new File(['unsupported'],'bad.pdf')
  const oversized=new File(['large'],'large.txt')
  Object.defineProperty(oversized,'size',{value:30_000_001})
  fireEvent.change(container.querySelector('input[type=file]')!,{target:{files:[invalid,valid,oversized]}})
  const notice=await screen.findByRole('status')
  expect(addMaterials).toHaveBeenCalledWith('course',[valid])
  expect(notice).toHaveTextContent('1 archivo(s) aceptado(s)')
  expect(notice).toHaveTextContent('bad.pdf: solo se admiten TXT y Markdown')
  expect(notice).toHaveTextContent('large.txt: supera el máximo de 30 MB')
})
