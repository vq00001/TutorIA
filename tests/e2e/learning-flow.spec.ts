import { test,expect, type BrowserContext } from '@playwright/test'
import { randomUUID } from 'node:crypto'

const origin='https://web-test:8443'
async function mutation(context:BrowserContext,path:string,body:unknown={},method='POST',extra:Record<string,string>={}) {
  let token=(await context.cookies()).find(c=>c.name==='tutoria_csrf')?.value
  if(!token) {
    const csrf=await context.request.get(origin+'/api/v1/auth/csrf')
    expect(csrf.ok()).toBeTruthy()
    token=(await csrf.json()).data.csrfToken
  }
  if(typeof token !== 'string' || !token) throw new Error('E2E_CSRF_UNAVAILABLE')
  return context.request.fetch(origin+'/api/v1'+path,{method,data:body,headers:{Origin:origin,'X-CSRF-Token':token,'Idempotency-Key':randomUUID(),...extra}})
}
async function read(context:BrowserContext,path:string) {
  const response=await context.request.get(origin+'/api/v1'+path)
  expect(response.ok()).toBeTruthy()
  return (await response.json()).data
}

test('TEST-073 isolated learning journey and source withdrawal',async({page,context})=>{
  const health=await context.request.get(origin+'/api/v1/health')
  expect(health.status()).toBe(200)
  expect(await health.json()).toEqual({status:'ok'})
  const suffix=randomUUID().slice(0,8)
  const email=`synthetic-${suffix}@example.com`
  const password='Synthetic-only passphrase 1234'
  await page.goto('/')
  await page.getByRole('button',{name:'Crear una cuenta'}).click()
  await page.getByLabel('Alias',{exact:true}).fill('Synthetic student')
  await page.getByLabel('Nickname',{exact:true}).fill('synthetic_'+suffix)
  await page.getByLabel('Correo',{exact:true}).fill(email)
  await page.getByLabel('Contraseña',{exact:true}).fill(password)
  for(const checkbox of await page.getByRole('checkbox').all()) await checkbox.check()
  await page.getByRole('button',{name:'Registrarme'}).click()
  await expect(page.getByRole('alert')).toContainText('Revisa tu correo')
  let link=''
  await expect.poll(async()=>{
    const response=await context.request.get('http://mail-stub:8025/messages')
    const messages=(await response.json()).messages
    const text=messages.find((m:{to:string})=>m.to.includes(email))?.text??''
    link=text.match(/https:\/\/web-test:8443\/cuenta\/verify_email\?token=[A-Za-z0-9_-]+/)?.[0]??''
    return Boolean(link)
  }).toBeTruthy()
  await page.goto(link)
  await expect(page.getByRole('alert')).toContainText('Correo verificado')
  expect(new URL(page.url()).search).toBe('')
  await page.getByLabel('Correo',{exact:true}).fill(email)
  await page.getByLabel('Contraseña',{exact:true}).fill(password)
  await page.getByRole('button',{name:'Entrar',exact:true}).click()
  await expect(page.getByRole('button',{name:'Cerrar sesión'})).toBeVisible()
  const cookies=await context.cookies()
  const session=cookies.find(c=>c.name==='tutoria_session')
  expect(session?.secure).toBeTruthy();expect(session?.httpOnly).toBeTruthy();expect(session?.sameSite).toBe('Lax')
  await page.goto('/cursos/crear')
  await page.getByLabel(/Nombre del curso/).fill('Curso sintético '+suffix)
  await page.getByLabel('Descripción',{exact:true}).fill('Curso aislado para pruebas')
  await page.getByLabel('Objetivos y alcance').fill('Comprender la derivada')
  await page.getByRole('button',{name:'Crear curso',exact:true}).click()
  await expect(page).toHaveURL(/\/cursos\/[a-f0-9-]{36}$/)
  const courseId=new URL(page.url()).pathname.split('/')[2]
  await page.goto('/materiales?courseId='+courseId)
  await page.locator('input[type=file]').setInputFiles({name:'fuente.md',mimeType:'text/markdown',buffer:Buffer.from('# Derivada\nLa derivada mide la tasa de cambio de una función.\n')})
  await test.step('phase-material-publication', async()=> { await expect.poll(async()=> (await read(context,`/courses/${courseId}/materials`))[0]?.status,{timeout:60_000}).toBe('ready') })
  const material=(await read(context,`/courses/${courseId}/materials`))[0]
  await test.step('phase-concept-extraction', async()=> { await expect.poll(async()=> (await read(context,`/courses/${courseId}/concepts/drafts`)).length,{timeout:60_000}).toBeGreaterThan(0) })
  await page.goto(`/cursos/${courseId}/grafo`)
  await page.getByRole('button',{name:'Aprobar concepto',exact:true}).first().click()
  await expect.poll(async()=> (await read(context,`/courses/${courseId}/knowledge-graph`)).nodes.length).toBeGreaterThan(0)
  const graph=await read(context,`/courses/${courseId}/knowledge-graph`)
  await page.goto(`/cursos/${courseId}`)
  await page.getByLabel('Escribe tu pregunta').fill('¿Qué mide la derivada?')
  await page.getByRole('button',{name:/Enviar/}).click()
  await test.step('phase-chat-response', async()=> { await expect(page.getByText('La derivada mide la tasa de cambio según el material.',{exact:true})).toBeVisible({timeout:60_000}) })
  const citation=page.getByRole('link',{name:'Fuente 1',exact:true})
  await expect(citation).toBeVisible()
  const download=await context.request.get((await citation.getAttribute('href'))!)
  expect(download.ok()).toBeTruthy()
  expect(await download.text()).toContain('La derivada mide la tasa de cambio')
  const generated=await mutation(context,`/courses/${courseId}/exams`,{title:'Diagnóstico sintético',kind:'diagnostic',questionCount:1,durationSeconds:600,conceptIds:[graph.nodes[0].key],questionKinds:['alternatives']})
  expect(generated.status()).toBe(202)
  const examId=(await generated.json()).data.id
  await test.step('phase-exam-generation', async()=> { await expect.poll(async()=> (await read(context,`/courses/${courseId}/exams`)).find((e:{id:string})=>e.id===examId)?.status,{timeout:60_000}).toBe('ready') })
  const started=await mutation(context,`/courses/${courseId}/exams/${examId}/attempts`)
  expect(started.status()).toBe(201)
  const attempt=(await started.json()).data
  const serialized=JSON.stringify(attempt)
  expect(serialized).not.toContain('expected_answer');expect(serialized).not.toContain('option_ids');expect(serialized).not.toContain('rubric')
  const question=attempt.questions[0]
  const saved=await mutation(context,`/attempts/${attempt.id}/answers/${question.id}`,{optionIds:[question.options[0].id],textAnswer:null,flagged:false},'PUT',{'If-Match':'0'})
  expect(saved.ok()).toBeTruthy()
  expect((await mutation(context,`/attempts/${attempt.id}/submit`)).status()).toBe(202)
  await test.step('phase-exam-grading', async()=> { await expect.poll(async()=> (await read(context,`/attempts/${attempt.id}`)).status,{timeout:60_000}).toBe('graded') })
  expect(Number((await read(context,`/attempts/${attempt.id}`)).score_percent)).toBe(100)
  const mastery=await read(context,`/courses/${courseId}/mastery/me`)
  expect(mastery[0].level).toBe('developing')
  await page.goto(`/cursos/${courseId}/grafo`)
  await page.getByRole('button',{name:'Crear mi ruta de estudio'}).click()
  await expect(page.getByRole('heading',{name:/Mi ruta/})).toBeVisible()
  const removed=await mutation(context,`/courses/${courseId}/materials/${material.id}`,{},'DELETE')
  expect(removed.status()).toBe(202)
  const deletion=(await removed.json()).data.deletionRequestId
  await test.step('phase-source-purge', async()=> { await expect.poll(async()=> (await read(context,`/deletion-requests/${deletion}`)).state,{timeout:60_000}).toBe('completed') })
  expect((await read(context,`/courses/${courseId}/materials`)).length).toBe(0)
  expect((await context.request.get(origin+`/api/v1/courses/${courseId}/materials/${material.id}/versions/${material.versionId}/content`)).status()).toBe(404)
  await page.getByRole('button',{name:'Cerrar sesión'}).click()
  await expect(page.getByRole('button',{name:'Entrar',exact:true})).toBeVisible()
  expect(await page.evaluate(()=>Object.keys(localStorage).filter(k=>k.startsWith('exam-draft:')))).toEqual([])
})
