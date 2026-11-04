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
