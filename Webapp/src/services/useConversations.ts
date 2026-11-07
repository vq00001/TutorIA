import { useCallback, useEffect, useRef, useState } from 'react'
import { allPages, request } from './http'
import type { ChatMessage, ChatMessageDto } from '../types'

export interface Conversation {id:string;title:string;created_at:string}
export function useConversations(courseId:string) {
  const [threads,setThreads]=useState<Conversation[]>([]),[selected,setSelected]=useState(''),[messages,setMessages]=useState<ChatMessage[]>([])
  const [busy,setBusy]=useState(false),[error,setError]=useState('')
  const current=useRef({courseId,selected:''})
  const pending=useRef(false)
  const select=useCallback((id:string)=>{current.current={courseId,selected:id};setSelected(id);setMessages([]);setError('')},[courseId])
  const loadMessages=useCallback(async(id:string)=>{
    let after=0;const data:ChatMessageDto[]=[]
    while(true){const r=await request<{data:Array<ChatMessageDto&{sequenceNo:number}>}>(`/courses/${courseId}/conversations/${id}/messages?after=${after}`);data.push(...r.data);if(r.data.length<100)break;after=r.data[r.data.length-1].sequenceNo}
    if(current.current.courseId===courseId&&current.current.selected===id)setMessages(data.map(m=>({...m,createdAt:new Date(m.createdAt)})))
  },[courseId])
  useEffect(()=>{let live=true;current.current={courseId,selected:''};setThreads([]);setSelected('');setMessages([]);setError('');void allPages<Conversation>(`/courses/${courseId}/conversations`).then(r=>{if(live){setThreads(r.data);select(r.data[0]?.id??'')}}).catch(e=>{if(live)setError(e.message)});return()=>{live=false;current.current={courseId:'',selected:''}}},[courseId,select])
  useEffect(()=>{if(!selected||current.current.selected!==selected)return;let live=true;const refresh=()=>loadMessages(selected).catch(e=>{if(live)setError(e.message)});void refresh();const timer=setInterval(()=>{if(document.visibilityState==='visible')void refresh()},2000);return()=>{live=false;clearInterval(timer)}},[selected,loadMessages])
  async function createRaw(title:string) {const r=await request<{data:{id:string}}>(`/courses/${courseId}/conversations`,{method:'POST',body:JSON.stringify({title:title.trim()||'Nueva conversación'})});if(current.current.courseId!==courseId)return '';setThreads(old=>[...old,{id:r.data.id,title:title.trim()||'Nueva conversación',created_at:new Date().toISOString()}]);select(r.data.id);return r.data.id}
  async function create(title:string) {if(pending.current)return '';pending.current=true;setBusy(true);setError('');try{return await createRaw(title)}catch(e){setError(e instanceof Error?e.message:'No se pudo crear la conversación.');return ''}finally{pending.current=false;setBusy(false)}}
  async function remove() {if(!selected||pending.current)return;setBusy(true);pending.current=true;try{await request(`/courses/${courseId}/conversations/${selected}`,{method:'DELETE'});const remaining=threads.filter(t=>t.id!==selected);setThreads(remaining);select(remaining[0]?.id??'')}catch(e){setError(e instanceof Error?e.message:'No se pudo eliminar.')}finally{pending.current=false;setBusy(false)}}
  async function send(content:string) {if(pending.current)return;pending.current=true;setBusy(true);setError('');try{const id=selected||await createRaw('Nueva conversación');if(!id)return;await request(`/courses/${courseId}/conversations/${id}/messages`,{method:'POST',body:JSON.stringify({content})});await loadMessages(id)}catch(e){setError(e instanceof Error?e.message:'No se pudo enviar.')}finally{pending.current=false;setBusy(false)}}
  return {threads,selected,messages,busy,error,select,create,remove,send}
}
