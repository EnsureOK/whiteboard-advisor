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

export interface MessageOut {
  id: string;
  clientId: string;
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
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

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}));
    throw new Error(detail.detail || `${resp.status} ${resp.statusText}`);
  }
  return resp.json();
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
    return fetch(`/api/workbench/clients/${clientId}/files`, { method: "POST", body: form }).then(
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

  createTask: (clientId: string, kind: string, message?: string) =>
    req<TaskOut>("/api/workbench/tasks", {
      method: "POST",
      body: JSON.stringify({ clientId, kind, message }),
    }),

  approveTask: (id: string) =>
    req<TaskOut>(`/api/workbench/tasks/${id}/approve`, { method: "POST", body: JSON.stringify({}) }),

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
};

/** SSE 流式对话:逐 token 回调,结束时返回引用。 */
export async function chatStream(
  clientId: string,
  message: string,
  onDelta: (text: string) => void
): Promise<{ citations: Citation[] }> {
  const resp = await fetch("/api/workbench/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
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
        if (evt.type === "delta") onDelta(evt.text);
        if (evt.type === "done") citations = evt.citations || [];
      } catch {
        /* 忽略不完整帧 */
      }
    }
  }
  return { citations };
}
