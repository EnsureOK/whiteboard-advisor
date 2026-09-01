/** 工作台前端 API 封装(REST + SSE)。 */

export interface Member {
  id: string;
  name: string;
  relation: string;
  badge: string;
  birthday: string | null;
  notes: string;
  seq: number;
}

export interface Policy {
  id: string;
  clientId: string;
  memberId: string | null;
  line: string;
  productName: string;
  insurer: string;
  amount: number;
  premium: number;
  effectiveDate: string | null;
  expiryDate: string | null;
  status: string;
  statusLabel: string;
  notes: string;
}

export interface Engagement {
  id: string;
  clientId: string;
  kind: string;
  kindLabel: string;
  title: string;
  line: string;
  policyId: string | null;
  status: string;
  note: string;
  createdAt: string;
}

export interface ClientFileOut {
  id: string;
  clientId: string;
  filename: string;
  contentType: string;
  sizeBytes: number;
  kind: "image" | "document";
  kbDocId: string | null;
  createdAt: string;
}

export type ClientType = "personal" | "family" | "company";

export interface Client {
  id: string;
  name: string;
  type: ClientType;
  notes: string;
  nextContact: string | null;
  members: Member[];
  policies: Policy[];
  engagements: Engagement[];
  fileCount: number;
}

export interface Todo {
  id: string;
  clientId: string | null;
  title: string;
  detail: string;
  priority: string;
  status: string;
  due: string | null;
}

export interface Citation {
  chunkId: string;
  docId: string;
  docTitle: string;
  docType: string;
  scope: string;
  text: string;
  score: number;
  /** scope=web 时的真实来源链接(新窗打开) */
  url?: string;
}

export interface TaskEventOut {
  id: string;
  seq: number;
  type: string;
  title: string;
  status: string;
  payload: any;
  createdAt: string;
}

export interface TaskOut {
  id: string;
  clientId: string;
  title: string;
  kind: string;
  status: string;
  plan: { tool: string; title: string; query?: string }[];
  events: TaskEventOut[];
  createdAt: string;
  updatedAt: string;
}

export interface ArtifactOut {
  id: string;
  clientId: string;
  taskId: string | null;
  type: string;
  title: string;
  version: number;
  content: any;
  createdAt: string;
}

export interface ToolEvent {
  name: string;
  label: string;
  summary?: string;
  /** 仅流式过程中出现:true 表示还在执行 */
  running?: boolean;
}

export interface MessageOut {
  id: string;
  clientId: string;
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  toolEvents?: ToolEvent[];
  taskId: string | null;
  createdAt: string;
}

export interface KbDoc {
  id: string;
  title: string;
  filename: string;
  docType: string;
  sizeBytes: number;
  tags: string[];
  scope: string;
  status: string;
  error: string;
  chunkCount: number;
  sourceUrl: string | null;
  createdAt: string;
  updatedAt: string;
  chunks?: { id: string; seq: number; text: string; hasEmbedding: boolean }[];
}

export interface Bootstrap {
  clients: Client[];
  todos: Todo[];
  kb: { docs: number; indexed: number };
  engagementKinds: Record<string, string>;
  policyStatuses: Record<string, string>;
  llm: boolean;
  embedding: boolean;
}

// ---------- 登录态(localStorage) ----------

const TOKEN_KEY = "wb_token";

export const authToken = {
  get: () => localStorage.getItem(TOKEN_KEY),
  set: (t: string) => localStorage.setItem(TOKEN_KEY, t),
  clear: () => localStorage.removeItem(TOKEN_KEY),
};

function authHeaders(): Record<string, string> {
  const t = authToken.get();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(url, {
    ...init,
    headers: { "Content-Type": "application/json", ...authHeaders(), ...(init?.headers || {}) },
  });
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}));
    throw new Error(detail.detail || `${resp.status} ${resp.statusText}`);
  }
  return resp.json();
}

// ---------- 计费类型 ----------

export interface CreditBalance {
  planTokens: number;
  packTokens: number;
  planCredits: number;
  packCredits: number;
  totalCredits: number;
}

export interface BillingStatus {
  plan: string;
  planName: string;
  features: string[];
  active: boolean;
  planExpiresAt: string | null;
  credits: CreditBalance;
  monthConsumedCredits: number;
  hasCredits: boolean;
  welcomeClaimed: boolean;
  welcomeCredits: number;
}

export interface PlanDef {
  id: string;
  name: string;
  priceCents: number;
  days: number;
  monthlyCredits: number;
  features: string[];
}

export interface PackDef {
  id: string;
  name: string;
  priceCents: number;
  credits: number;
}

export interface AuthUser {
  id: string;
  username: string;
  displayName: string;
  plan: string;
}

export const api = {
  bootstrap: () => req<Bootstrap>("/api/workbench/bootstrap"),

  createClient: (name: string, type: ClientType, notes = "") =>
    req<Client>("/api/workbench/clients", { method: "POST", body: JSON.stringify({ name, type, notes }) }),

  patchClient: (id: string, patch: Partial<{ name: string; notes: string; nextContact: string }>) =>
    req<Client>(`/api/workbench/clients/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),

  createMember: (clientId: string, member: { name: string; relation?: string; birthday?: string | null }) =>
    req<Member>(`/api/workbench/clients/${clientId}/members`, {
      method: "POST",
      body: JSON.stringify(member),
    }),

  createPolicy: (clientId: string, policy: Partial<Policy> & { line: string }) =>
    req<Policy>(`/api/workbench/clients/${clientId}/policies`, {
      method: "POST",
      body: JSON.stringify(policy),
    }),

  createEngagement: (clientId: string, engagement: { kind: string; title?: string; line?: string; note?: string }) =>
    req<Engagement>(`/api/workbench/clients/${clientId}/engagements`, {
      method: "POST",
      body: JSON.stringify(engagement),
    }),

  patchEngagement: (id: string, patch: Partial<{ status: string; title: string; note: string }>) =>
    req<Engagement>(`/api/workbench/engagements/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),

  uploadClientFiles: (clientId: string, files: File[]) => {
    const form = new FormData();
    for (const f of files) form.append("files", f);
    return fetch(`/api/workbench/clients/${clientId}/files`, {
      method: "POST",
      headers: authHeaders(),
      body: form,
    }).then(
      async (resp) => {
        if (!resp.ok) {
          const detail = await resp.json().catch(() => ({}));
          throw new Error(detail.detail || `${resp.status}`);
        }
        return resp.json() as Promise<ClientFileOut[]>;
      }
    );
  },

  clientFiles: (clientId: string) => req<ClientFileOut[]>(`/api/workbench/clients/${clientId}/files`),

  deleteClientFile: (clientId: string, fileId: string) =>
    req<{ ok: boolean }>(`/api/workbench/clients/${clientId}/files/${fileId}`, { method: "DELETE" }),

  clientFileRawUrl: (clientId: string, fileId: string) =>
    `/api/workbench/clients/${clientId}/files/${fileId}/raw`,

  messages: (clientId: string) => req<MessageOut[]>(`/api/workbench/clients/${clientId}/messages`),

  artifacts: (clientId: string) => req<ArtifactOut[]>(`/api/workbench/artifacts?clientId=${clientId}`),

  getTask: (id: string) => req<TaskOut>(`/api/workbench/tasks/${id}`),

  createTaskBatch: (clientIds: string[], kind: string, message?: string, autoRun = true) =>
    req<{ batchId: string; tasks: TaskOut[] }>("/api/workbench/tasks/batch", {
      method: "POST",
      body: JSON.stringify({ clientIds, kind, message, autoRun }),
    }),

  listTasks: (clientId?: string) =>
    req<
      {
        id: string;
        clientId: string;
        batchId: string | null;
        clientName: string;
        title: string;
        kind: string;
        status: string;
        stepsDone: number;
        stepsTotal: number;
        createdAt: string;
        updatedAt: string;
      }[]
    >(`/api/workbench/tasks${clientId ? `?clientId=${clientId}` : ""}`),

  createTask: (clientId: string, kind: string, message?: string) =>
    req<TaskOut>("/api/workbench/tasks", {
      method: "POST",
      body: JSON.stringify({ clientId, kind, message }),
    }),

  approveTask: (id: string, plan?: { tool: string; title: string; query?: string }[]) =>
    req<TaskOut>(`/api/workbench/tasks/${id}/approve`, {
      method: "POST",
      body: JSON.stringify(plan ? { plan } : {}),
    }),

  reviseTask: (id: string, instruction: string) =>
    req<TaskOut>(`/api/workbench/tasks/${id}/revise`, {
      method: "POST",
      body: JSON.stringify({ instruction }),
    }),

  reviseArtifact: (id: string, instruction: string) =>
    req<ArtifactOut>(`/api/workbench/artifacts/${id}/revise`, {
      method: "POST",
      body: JSON.stringify({ instruction }),
    }),

  stepTask: (id: string) =>
    req<{ event: TaskEventOut; awaiting: boolean; taskStatus: string }>(
      `/api/workbench/tasks/${id}/step`,
      { method: "POST" }
    ),

  confirmEvent: (id: string, eventId: string) =>
    req<{ event: TaskEventOut }>(`/api/workbench/tasks/${id}/confirm`, {
      method: "POST",
      body: JSON.stringify({ eventId }),
    }),

  patchTodo: (id: string, status: string) =>
    req<Todo>(`/api/workbench/todos/${id}`, { method: "PATCH", body: JSON.stringify({ status }) }),

  kbDocuments: () => req<KbDoc[]>("/api/kb/documents"),

  kbDocument: (id: string) => req<KbDoc>(`/api/kb/documents/${id}`),

  kbDelete: (id: string) => req<{ ok: boolean }>(`/api/kb/documents/${id}`, { method: "DELETE" }),

  kbReindex: (id: string) => req<KbDoc>(`/api/kb/documents/${id}/reindex`, { method: "POST" }),

  kbUpload: (file: File, title: string, scope: string, tags: string[]) => {
    const form = new FormData();
    form.append("file", file);
    form.append("title", title);
    form.append("scope", scope);
    form.append("tags", JSON.stringify(tags));
    return fetch("/api/kb/documents", { method: "POST", body: form }).then(async (resp) => {
      if (!resp.ok) {
        const detail = await resp.json().catch(() => ({}));
        throw new Error(detail.detail || `${resp.status}`);
      }
      return resp.json() as Promise<KbDoc>;
    });
  },

  kbUploadText: (title: string, text: string, scope: string, tags: string[]) =>
    req<KbDoc>("/api/kb/documents/text", {
      method: "POST",
      body: JSON.stringify({ title, text, scope, tags }),
    }),

  kbSearch: (query: string, clientId?: string) =>
    req<{ query: string; hits: Citation[] }>("/api/kb/search", {
      method: "POST",
      body: JSON.stringify({ query, clientId }),
    }),

  // ---------- 登录与计费 ----------

  authRegister: (username: string, password: string) =>
    req<{ token: string; user: AuthUser }>("/api/auth/register", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),

  authLogin: (username: string, password: string) =>
    req<{ token: string; user: AuthUser }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),

  authMe: () => req<{ user: AuthUser }>("/api/auth/me").then((r) => r.user),

  authSmsSend: (phone: string) =>
    req<{ sent: boolean; cooldown: number; provider: string }>("/api/auth/sms/send", {
      method: "POST",
      body: JSON.stringify({ phone }),
    }),

  authSmsVerify: (phone: string, code: string) =>
    req<{ token: string; user: AuthUser }>("/api/auth/sms/verify", {
      method: "POST",
      body: JSON.stringify({ phone, code }),
    }),

  billingClaimWelcome: () =>
    req<{ claimed: boolean; alreadyClaimed: boolean; status: BillingStatus }>(
      "/api/billing/claim-welcome",
      { method: "POST" }
    ),

  billingPlans: () =>
    req<{ plans: PlanDef[]; packs: PackDef[]; tokensPerCredit: number; stripe: boolean }>(
      "/api/billing/plans"
    ),

  billingStatus: () => req<BillingStatus>("/api/billing/status"),

  billingCheckout: (item: string) =>
    req<{ orderId: string; checkoutUrl: string | null; demo: boolean; status?: BillingStatus }>(
      "/api/billing/checkout",
      { method: "POST", body: JSON.stringify({ item }) }
    ),

  billingRedeem: (code: string) =>
    req<{ ok: boolean; status: BillingStatus }>("/api/billing/redeem", {
      method: "POST",
      body: JSON.stringify({ code }),
    }),

  billingLedger: () =>
    req<{ id: string; deltaCredits: number; source: string; ref: string; createdAt: string }[]>(
      "/api/billing/ledger"
    ),

  billingOrders: () =>
    req<
      {
        id: string;
        item: string;
        itemName: string;
        amountCents: number;
        channel: string;
        status: string;
        createdAt: string;
        paidAt: string | null;
      }[]
    >("/api/billing/orders"),
};

export interface ChatStreamHandlers {
  onDelta: (text: string) => void;
  /** agent 开始调用一个工具 */
  onToolStart?: (ev: ToolEvent) => void;
  /** 工具执行完,带一行结果摘要 */
  onToolEnd?: (ev: ToolEvent) => void;
  /** plan-first:agent 生成了待确认的任务计划 */
  onTaskCreated?: (task: TaskOut) => void;
}

/** SSE 流式对话:逐 token + 工具过程回调,结束时返回引用与工具事件。 */
export async function chatStream(
  clientId: string,
  message: string,
  handlers: ChatStreamHandlers
): Promise<{ citations: Citation[]; toolEvents: ToolEvent[]; content?: string }> {
  const resp = await fetch("/api/workbench/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ clientId, message }),
  });
  if (!resp.ok || !resp.body) {
    const detail = await resp.json().catch(() => ({}));
    throw new Error(detail.detail || `${resp.status}`);
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let citations: Citation[] = [];
  let toolEvents: ToolEvent[] = [];
  let content: string | undefined;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() || "";
    for (const part of parts) {
      const line = part.trim();
      if (!line.startsWith("data:")) continue;
      try {
        const evt = JSON.parse(line.slice(5));
        if (evt.type === "delta") handlers.onDelta(evt.text);
        if (evt.type === "tool_start")
          handlers.onToolStart?.({ name: evt.name, label: evt.label, running: true });
        if (evt.type === "tool_end")
          handlers.onToolEnd?.({ name: evt.name, label: evt.label, summary: evt.summary });
        if (evt.type === "task_created") handlers.onTaskCreated?.(evt.task);
        if (evt.type === "done") {
          citations = evt.citations || [];
          toolEvents = evt.toolEvents || [];
          if (typeof evt.content === "string") content = evt.content;
        }
      } catch {
        /* 忽略不完整帧 */
      }
    }
  }
  return { citations, toolEvents, content };
}
