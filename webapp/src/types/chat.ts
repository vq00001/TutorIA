import type { ChartPayload } from "./charts"

// DESDE EL BACKEND

export interface SourceDocumentDto {
  documentId: string
  name: string
  relevance: number | null
  section?: string
  locator?: Record<string, unknown>
  snippet?: string
}

export interface MessageDto {
  id: string
  role: 'user' | 'assistant'
  content: string
  createdAt: string
  usedDocuments: boolean | null
  sourceDocuments: SourceDocumentDto[] | null
  inReplyTo?: string | null
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
  sourceDocuments?: SourceDocumentDto[]
}

export type ChatMessageDto = Omit<ChatMessage, 'createdAt'> & {
  createdAt: string
}

