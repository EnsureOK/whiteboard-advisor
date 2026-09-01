import { useRef, useState } from "react";
import type { Client, ClientType, Engagement, Todo } from "./api";
import { Icon, Spinner } from "./icons";
import type { IconName } from "./icons";
import { Markdown } from "./markdown";

interface Props {
  clients: Client[];
  todos: Todo[];
  activeId: string | null;
  creating: boolean;
  briefing: { date: string; content: string } | null;
  onSelect: (id: string) => void;
  onCreate: (name: string, type: ClientType, files: File[]) => void;
  onOpenTodo: (todo: Todo) => void;
  onToggleTodo: (todo: Todo) => void;
}

const TYPE_ICON: Record<ClientType, IconName> = {
  personal: "user",
  family: "users",
  company: "building",
};

const TYPE_LABEL: Record<ClientType, string> = {
  personal: "个人",
  family: "家庭",
  company: "企业",
};

/** 事项 -> 状态色:红=理赔(风险),琥珀=在等结果(投保/续期),中性=商机(咨询/在谈) */
function engagementTone(e: Engagement): { cls: string; live: boolean } {
  if (e.kind === "claim") return { cls: "bad", live: true };
  if (e.kind === "underwriting") return { cls: "warn", live: true };
  if (e.kind === "renewal" || e.kind === "preservation") return { cls: "warn", live: false };
  return { cls: "", live: false };
}

function memberBadgeTone(badge: string): string {
  if (badge.includes("承保")) return "ok";
  if (badge.includes("理赔")) return "bad";
  if (badge.includes("待") || badge.includes("方案") || badge.includes("投保")) return "warn";
  return "";
}

/** 左栏:今日待办 + 客户列表(个人/家庭/企业,托管保单与进行中事项聚合) */
export default function ClientPane({
  clients, todos, activeId, creating, briefing, onSelect, onCreate, onOpenTodo, onToggleTodo,
}: Props) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set(clients.slice(0, 1).map((c) => c.id)));
  const [adding, setAdding] = useState(false);
  const [briefOpen, setBriefOpen] = useState(
    () => briefing !== null && localStorage.getItem("wb_brief_closed") !== briefing?.date
  );

  const toggle = (id: string) => {
    const next = new Set(expanded);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setExpanded(next);
  };

  return (
    <aside className="wb-side">
      {briefing && (
        <div className="wb-brief">
          <button className="wb-brief-head" onClick={() => {
            const next = !briefOpen;
            setBriefOpen(next);
            if (!next) localStorage.setItem("wb_brief_closed", briefing.date);
            else localStorage.removeItem("wb_brief_closed");
          }}>
            <Icon name="sparkles" size={12} />
            <span>今日简报</span>
            <span className="wb-count wb-mono">{briefing.date.slice(5)}</span>
            <span className={"wb-client-caret" + (briefOpen ? " open" : "")} style={{ marginLeft: "auto" }}>
              <Icon name="chevronRight" size={11} strokeWidth={2.2} />
            </span>
          </button>
          {briefOpen && <Markdown className="wb-brief-body" text={briefing.content} />}
        </div>
      )}
      <div className="wb-section">
        今日待办 <span className="wb-count">{todos.length}</span>
      </div>
      <div className="wb-todos">
        {todos.length === 0 && (
          <div style={{ padding: "2px 14px", fontSize: 12, color: "var(--w-faint)" }}>今天没有待办</div>
        )}
        {todos.map((t) => (
          <div key={t.id} className="wb-todo" onClick={() => onOpenTodo(t)}>
            <span className={"wb-todo-dot" + (t.priority === "high" ? " high" : "")} />
            <div style={{ minWidth: 0 }}>
              <div className="wb-todo-title">{t.title}</div>
              {t.detail && <div className="wb-todo-detail">{t.detail}</div>}
            </div>
            <button
              className="wb-todo-done"
              title="标记完成"
              onClick={(e) => { e.stopPropagation(); onToggleTodo(t); }}
            >
              <Icon name="check" size={12} strokeWidth={2.2} />
            </button>
          </div>
        ))}
      </div>

      <div className="wb-section" style={{ marginTop: 6 }}>
        客户 <span className="wb-count">{clients.length}</span>
        <button className="wb-section-action" title="新增客户" onClick={() => setAdding(true)}>
          <Icon name="plus" size={13} />
        </button>
      </div>
      <div className="wb-tree">
        {clients.map((c) => {
          const open = expanded.has(c.id);
          return (
            <div key={c.id} className={"wb-client" + (c.id === activeId ? " active" : "")}>
              <button className="wb-client-head" onClick={() => { onSelect(c.id); toggle(c.id); }}>
                <span className={"wb-client-caret" + (open ? " open" : "")}>
                  <Icon name="chevronRight" size={11} strokeWidth={2.2} />
                </span>
                <span className="wb-client-type" title={TYPE_LABEL[c.type]}>
                  <Icon name={TYPE_ICON[c.type]} size={13.5} />
                </span>
                <span className="wb-client-name">{c.name}</span>
              </button>
              <div className="wb-client-meta">
                {c.policies.length > 0 && (
                  <span className="wb-client-count">托管 {c.policies.length} 单</span>
                )}
                {c.engagements.slice(0, 2).map((e) => {
                  const tone = engagementTone(e);
                  return (
                    <span key={e.id} className={"wb-tag " + tone.cls} title={e.title + (e.note ? ` — ${e.note}` : "")}>
                      <i className={tone.live ? "live" : ""} />
                      {e.kindLabel}
                      {e.line ? `·${e.line}` : ""}
                    </span>
                  );
                })}
                {c.engagements.length > 2 && (
                  <span
                    className="wb-tag"
                    title={c.engagements.slice(2).map((e) => `${e.kindLabel} ${e.title}`).join("\n")}
                  >
                    +{c.engagements.length - 2}
                  </span>
                )}
              </div>
              {open &&
                c.members.map((m) => (
                  <div key={m.id} className="wb-member" onClick={() => onSelect(c.id)}>
                    <span className="wb-avatar">{m.name.slice(0, 1)}</span>
                    <span>
                      {m.name}
                      <span className="wb-member-rel">
                        {" "}{m.relation}{m.birthday ? ` · ${m.birthday.slice(0, 4)}` : ""}
                      </span>
                    </span>
                    {m.badge && (
                      <span className={"wb-tag wb-member-badge " + memberBadgeTone(m.badge)}>
                        <i />{m.badge}
                      </span>
                    )}
                  </div>
                ))}
            </div>
          );
        })}

        {adding ? (
          <NewClientForm
            creating={creating}
            onSubmit={(name, type, files) => { onCreate(name, type, files); setAdding(false); }}
            onCancel={() => setAdding(false)}
          />
        ) : (
          <button className="wb-side-add" onClick={() => setAdding(true)}>
            <Icon name="plus" size={13} /> 新增客户
          </button>
        )}
      </div>
    </aside>
  );
}

/** 新建客户:名称 + 类型 + 资料上传(多文件/图片,文档自动入该客户私有知识库) */
function NewClientForm({
  creating,
  onSubmit,
  onCancel,
}: {
  creating: boolean;
  onSubmit: (name: string, type: ClientType, files: File[]) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState("");
  const [type, setType] = useState<ClientType>("family");
  const [files, setFiles] = useState<File[]>([]);
  const [dragging, setDragging] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const addFiles = (list: FileList | null) => {
    if (!list) return;
    setFiles((prev) => [...prev, ...Array.from(list)]);
  };

  const submit = () => {
    const n = name.trim();
    if (!n || creating) return;
    onSubmit(n, type, files);
  };

  return (
    <div className="wb-newclient">
      <input
        className="wb-text-input"
        autoFocus
        placeholder={type === "company" ? "企业名称,如:华兴商贸" : "客户名称,如:李强一家"}
        value={name}
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && submit()}
      />
      <div className="wb-seg">
        {(["personal", "family", "company"] as ClientType[]).map((t) => (
          <button key={t} className={type === t ? "on" : ""} onClick={() => setType(t)}>
            {TYPE_LABEL[t]}
          </button>
        ))}
      </div>
      <div
        className={"wb-drop-mini" + (dragging ? " drag" : "")}
        onClick={() => fileRef.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => { e.preventDefault(); setDragging(false); addFiles(e.dataTransfer.files); }}
      >
        拖入保单、体检报告、照片…<br />文档自动入该客户私有知识库
      </div>
      <input
        ref={fileRef}
        type="file"
        multiple
        accept=".pdf,.docx,.txt,.md,.html,.htm,.png,.jpg,.jpeg,.webp,.gif,.heic"
        style={{ display: "none" }}
        onChange={(e) => { addFiles(e.target.files); e.target.value = ""; }}
      />
      {files.length > 0 && (
        <div className="wb-file-pills">
          {files.map((f, i) => (
            <span key={`${f.name}-${i}`} className="wb-file-pill">
              <Icon name={f.type.startsWith("image/") ? "image" : "fileText"} size={11} />
              <span>{f.name}</span>
              <button title="移除" onClick={() => setFiles(files.filter((_, j) => j !== i))}>
                <Icon name="x" size={11} />
              </button>
            </span>
          ))}
        </div>
      )}
      <div className="wb-newclient-actions">
        <button className="wb-btn ghost" onClick={onCancel}>取消</button>
        <button className="wb-btn" disabled={!name.trim() || creating} onClick={submit}>
          {creating ? <Spinner size={12} /> : null}
          {creating ? "创建中" : files.length > 0 ? `创建并上传 ${files.length} 份资料` : "创建"}
        </button>
      </div>
    </div>
  );
}
