import { beforeEach, expect, it, vi } from 'vitest'
import { databaseApi } from './databaseApi'
import { describeUploadResults, type MaterialUploadResult } from './uploadResults'

const { request, allPages } = vi.hoisted(()=>({request:vi.fn(),allPages:vi.fn()}))
vi.mock('./http',()=>({request,allPages}))
beforeEach(()=> { request.mockReset(); allPages.mockReset() })

it('keeps accepted files and individual errors when a batch only partially succeeds',async()=>{
  const results:MaterialUploadResult[]=[
    {name:'good.md',data:{materialId:'new',versionId:'v',runId:'r',status:'queued'}},
    {name:'copy.txt',error:{code:'DUPLICATE_DOCUMENT'}},
  ]
  request.mockResolvedValue({data:{results}})
  allPages.mockResolvedValue({data:[{id:'existing'},{id:'new'}]})
  const response=await databaseApi.createCourseMaterials('course',[new File(['text'],'good.md')])
  expect(response.data).toHaveLength(2)
  expect(response.results).toEqual(results)
  expect(describeUploadResults(response.results)).toBe('1 archivo(s) aceptado(s). copy.txt: ya existe en este curso.')
})

it('does not expose an unrecognized server error as user-facing content',()=>{
  const notice=describeUploadResults([{name:'source.txt',error:{code:'PRIVATE_SQL_CONTENT'}}])
  expect(notice).toContain('0 archivo(s) aceptado(s)')
  expect(notice).toContain('source.txt: no se pudo aceptar el archivo.')
  expect(notice).not.toContain('PRIVATE_SQL_CONTENT')
})
