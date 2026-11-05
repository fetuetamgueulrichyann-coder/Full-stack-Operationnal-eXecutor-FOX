export type Role = "owner" | "tester";

export interface User {
  id: number;
  username: string;
  role: Role;
  timezone?: string | null;
}

export interface AuthResponse {
  user: User;
  csrf_token: string;
}

export interface ChatSummary {
  id: number;
  title: string | null;
  created_at: string;
  updated_at: string;
  messages: number;
}

export interface ChatsResponse {
  chats: ChatSummary[];
  current: number | null;
  private: boolean;
}

export interface HistoryMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  at: string;
}

export interface PendingAction {
  id: string;
  summary: string;
}

export interface MessageResponse {
  reply: string;
  pending: PendingAction[];
  chat_id: number | null;
  private: boolean;
}

export interface EventTask {
  id: number;
  title: string;
  [key: string]: unknown;
}

export interface AgendaEvent {
  id: number;
  title: string;
  location: string | null;
  start: string;
  end: string;
  repeat?: string;
  occurrence?: string | null;
  tasks?: EventTask[];
}

export interface EventsResponse {
  today: string;
  events: AgendaEvent[];
}

export interface EventSaved {
  event: AgendaEvent;
  conflicts: AgendaEvent[];
}

export type TaskStatus = "a_faire" | "en_cours" | "terminee" | "annulee";

export interface Task {
  id: number;
  title: string;
  priority: "haute" | "normale" | "basse";
  status: TaskStatus;
  overdue: boolean;
  due_at: string | null;
  event_id: number | null;
  event_title?: string;
}

export interface TasksResponse {
  tasks: Task[];
}

export type QuoteStatus = "brouillon" | "envoye" | "accepte" | "refuse";

export interface QuoteSummary {
  id: number;
  number: string;
  status: QuoteStatus;
  status_label: string;
  client: string;
  total: string;
  created_at: string | null;
}

export interface QuoteLine {
  description: string;
  quantity: string;
  unit_price: string;
  total: string;
}

export interface Quote extends QuoteSummary {
  client_id: number | null;
  currency: string;
  vat_rate: string;
  notes: string | null;
  lines: QuoteLine[];
  subtotal: string;
  vat: string;
}

export interface Client {
  id: number;
  name: string;
  phone: string | null;
  email: string | null;
  address: string | null;
  notes: string | null;
}

export interface MailDraft {
  to: string;
  subject: string;
  body: string;
}
