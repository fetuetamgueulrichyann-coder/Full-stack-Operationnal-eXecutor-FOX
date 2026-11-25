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

/** Fichier joint à un message : envoyé par POST /files, relu dans l'historique. */
export interface FileInfo {
  id: string;
  name: string;
  kind: "image" | "pdf" | "word" | "excel" | "text" | "archive";
  size: number;
}

export interface HistoryMessage {
  id: string;
  role: "user" | "assistant";
  text: string | null; // une réponse peut manquer (message interrompu)
  at: string;
  files?: FileInfo[];
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
  files?: FileInfo[]; // fichiers créés par FOX pour cette réponse (PDF, Word, Excel, zip)
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
  reminder_minutes?: number;
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
  archived?: boolean;
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

export interface MemoryItem {
  id: number;
  content: string;
  source: "user" | "fox";
  created_at: string;
}

export interface JournalEntry {
  at: string;
  text: string;
}

export interface BriefingSettings {
  enabled: boolean;
  time: string;
}

export interface TimezoneInfo {
  timezone: string;
  default: string;
  custom: boolean;
  now: string;
}

export interface Business {
  name: string | null;
  phone: string | null;
  email: string | null;
  address: string | null;
  tax_id: string | null;
}

export type ProjectStatus = "en_cours" | "suspendu" | "termine";

export interface Project {
  id: number;
  name: string;
  status: ProjectStatus;
  status_label: string;
  client_id: number | null;
  client: string | null;
  quote: string | null;
  start_date: string | null;
  end_date: string | null;
  notes: string | null;
  archived: boolean;
  currency: string;
  income: string;
  income_label: string;
  expense: string;
  expense_label: string;
  margin: string;
  margin_label: string;
  budget: string | null;
  budget_label: string | null;
  budget_left: string | null;
  budget_left_label: string | null;
  budget_used_percent: number | null;
}

export interface LedgerEntry {
  id: number;
  kind: "recette" | "depense";
  kind_label: string;
  amount: string;
  amount_label: string;
  currency: string;
  date: string;
  category: string | null;
  description: string;
  project_id: number | null;
  project: string | null;
}

export interface LedgerSummary {
  month: string;
  currency: string;
  income: string;
  income_label: string;
  expense: string;
  expense_label: string;
  balance: string;
  balance_label: string;
  entries: number;
  by_category: { kind: "recette" | "depense"; category: string; total: string; total_label: string }[];
}

export interface VoiceConfig {
  enabled: boolean;
  providers: string[];
  voice_choice: boolean;
  voice: string;
  voices: { name: string; label: string }[];
  speed: number;
  daily_limit: number;
  used: number;
}

/** Réglages de confidentialité (GET/POST /privacy/settings, POST /privacy/consent). Voir docs/API.md. */
export type PrivacyLevelId = "standard" | "protege" | "strict";
export type NotificationDetail = "discret" | "complet";

export interface PrivacyLevel {
  id: PrivacyLevelId;
  label: string;
  summary: string;
  applied: boolean;
}

export interface PrivacySettings {
  enforcement_active: boolean;
  enforcement_notice: string | null;
  level: PrivacyLevelId;
  levels: PrivacyLevel[];
  default_level: PrivacyLevelId;
  preferred_provider: string | null;
  provider_choices: { id: string; label: string }[];
  notification_detail: NotificationDetail;
  consent: { current_version: number; version: number | null; at: string | null; read: boolean; notice: string };
  block_all: boolean;
}

/** GET /privacy/info : fournisseurs, garanties déclarées, suivi des envois, catégories de données. */
export interface PrivacyInfo {
  enforcement_active: boolean;
  providers: { id: string; label: string; kind: string; retention: string; available: boolean }[];
  declared: { gemini_tier: string; groq_zdr: boolean; note: string };
  tracking: { active: boolean; notice: string };
  categories: { id: string; label: string }[];
}
