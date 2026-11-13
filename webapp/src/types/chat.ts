import type { ChartPayload } from "./charts"

// DESDE EL BACKEND

export interface SourceDocumentDto {
  documentId: string
  name: string
  relevance: number | null
}

export interface MessageDto {
  id: string
  role: 'user' | 'assistant'
  content: string
  createdAt: string
  usedDocuments: boolean | null
  sourceDocuments: SourceDocumentDto[] | null
}

export interface ConversationDto {
  id: string
  courseId: string
  title: string
  messages: MessageDto[]
  createdAt: string
  updatedAt: string
}

// FRONTEND

export type MessagePart =
  | { type: 'markdown'; content: string }
  | { type: 'chart'; chart: ChartPayload }


export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  parts: MessagePart[]
  createdAt: Date
  awaitingResponse?: boolean
  isError?: boolean
}

export type ChatMessageDto = Omit<ChatMessage, 'createdAt'> & {
  createdAt: string
}

