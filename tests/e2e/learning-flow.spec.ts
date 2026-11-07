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
  await page.getByLabel('Título de la nueva conversación').fill('Segundo diálogo')
  await page.getByRole('button',{name:'Nueva conversación',exact:true}).click()
  await expect(page.getByRole('combobox',{name:'Conversación'})).toContainText('Segundo diálogo')
  page.once('dialog',dialog=>void dialog.accept())
  await page.getByRole('button',{name:'Eliminar conversación',exact:true}).click()
  await expect(page.getByRole('combobox',{name:'Conversación'})).not.toContainText('Segundo diálogo')
  await page.getByRole('link',{name:'Materiales generados',exact:true}).click()
  await page.getByLabel('Tema',{exact:true}).fill('Derivada')
  await page.getByRole('button',{name:'Generar material',exact:true}).click()
  await test.step('phase-artifact-generation',async()=>{await expect(page.getByRole('heading',{name:'summary: Derivada',exact:true})).toBeVisible({timeout:60_000})})
  await expect(page.getByRole('link',{name:'fuente.md',exact:true})).toBeVisible()
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
  const reviewed=await read(context,`/attempts/${attempt.id}`)
  expect(reviewed.questions[0].usesDocuments).toBe(true)
  expect(reviewed.questions[0].usedDocuments[0].name).toBe('fuente.md')
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
  expect((await read(context,`/courses/${courseId}/artifacts`)).length).toBe(0)
  expect((await context.request.get(origin+`/api/v1/courses/${courseId}/materials/${material.id}/versions/${material.versionId}/content`)).status()).toBe(404)
  await page.getByRole('button',{name:'Cerrar sesión'}).click()
  await expect(page.getByRole('button',{name:'Entrar',exact:true})).toBeVisible()
  expect(await page.evaluate(()=>Object.keys(localStorage).filter(k=>k.startsWith('exam-draft:')))).toEqual([])
})

test('TODO collaboration invitations profile notifications and departures',async({page,context,browser})=>{
  const suffix=randomUUID().slice(0,8),password='Synthetic-only passphrase 1234'
  async function account(ctx:BrowserContext,label:string) {
    const email=`${label}-${suffix}@example.com`,nickname=`${label}_${suffix}`
    expect((await mutation(ctx,'/auth/register',{email,nickname,displayName:label,password,acceptTerms:true,acknowledgeAI:true})).status()).toBe(201)
    let token=''
    await expect.poll(async()=>{const r=await ctx.request.get('http://mail-stub:8025/messages');const text=(await r.json()).messages.find((m:{to:string})=>m.to.includes(email))?.text??'';token=text.match(/token=([A-Za-z0-9_-]+)/)?.[1]??'';return !!token}).toBeTruthy()
    expect((await mutation(ctx,'/auth/verify-email',{token})).ok()).toBeTruthy()
    expect((await mutation(ctx,'/auth/login',{email,password})).ok()).toBeTruthy()
    return {email,nickname}
  }
  await account(context,'coordinator')
  const guest=await browser.newContext({baseURL:origin})
  try {
    const student=await account(guest,'guest')
    const other=await guest.newPage()
    const c=await mutation(context,'/courses',{name:'Colaboración '+suffix,description:'Prueba aislada',objectives:'Colaborar'})
    expect(c.status()).toBe(201)
    const course=(await c.json()).data
    await page.goto(`/cursos/${course.id}/configuracion`)
    await page.getByLabel('Correo de la persona').fill(student.email)
    await page.getByRole('button',{name:'Enviar invitación',exact:true}).click()
    await expect(page.getByRole('status')).toContainText('recibirá la invitación')
    await other.goto('/invitaciones')
    await other.getByRole('button',{name:'Rechazar '+course.name,exact:true}).click()
    await expect(other.getByRole('status')).toContainText('rechazada')
    await page.getByLabel('Correo de la persona').fill(student.email)
    await page.getByRole('button',{name:'Enviar invitación',exact:true}).click()
    await expect(page.getByLabel('Correo de la persona')).toHaveValue('')
    await other.reload()
    await other.getByRole('button',{name:'Aceptar '+course.name,exact:true}).click()
    await expect(other.getByRole('status')).toContainText('aceptada')
    await other.goto(`/cursos/${course.id}/configuracion`)
    await expect(other.getByRole('heading',{name:'Miembros',exact:true})).toBeVisible()
    await expect(other.getByText('coordinator',{exact:true})).toBeVisible()
    await expect(other.getByRole('button',{name:'Guardar curso',exact:true})).toHaveCount(0)
    await other.goto('/notificaciones')
    await other.getByRole('button',{name:'Marcar como leída',exact:true}).first().click()
    await other.goto('/cuenta')
    await other.getByLabel('Nombre visible',{exact:true}).fill('Nombre actualizado')
    await other.getByRole('button',{name:'Guardar perfil',exact:true}).click()
    await expect(other.getByRole('status')).toContainText('Perfil actualizado')
    await other.goto('/actividad')
    await expect(other.getByText('Perfil actualizado',{exact:true})).toBeVisible()
    await other.goto(`/cursos/${course.id}/configuracion`)
    other.once('dialog',dialog=>void dialog.accept())
    await other.getByRole('button',{name:'Salir del curso',exact:true}).click()
    await expect(other).toHaveURL(origin+'/')
    expect((await read(guest,'/courses')).length).toBe(0)
    await other.goto('/cuenta')
    await other.getByLabel('Escribe tu nickname para confirmar').fill(student.nickname)
    await other.getByRole('button',{name:'Solicitar eliminación de cuenta',exact:true}).click()
    await expect(other.getByRole('button',{name:'Entrar',exact:true})).toBeVisible()
    await page.getByLabel('Escribe el nombre del curso para confirmar').fill(course.name)
    await page.getByRole('button',{name:'Solicitar eliminación del curso',exact:true}).click()
    await expect(page).toHaveURL(origin+'/')
  } finally { await guest.close() }
})
