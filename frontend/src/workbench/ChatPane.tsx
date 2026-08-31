import React, { useEffect, useRef, useState } from "react";
import type { Citation, Client, MessageOut, TaskOut } from "./api";
import { Icon, Spinner } from "./icons";
import type { IconName } from "./icons";

interface Props {
  client: Client;
  messages: MessageOut[];
  task: TaskOut | null;
  running: boolean;
  streaming: boolean;
  llmAvailable: boolean;
  onSend: (text: string) => void;
  onQuickCommand: (label: string) => void;
  onApprovePlan: () => void;
  onConfirmApproval: (eventId: string) => void;
}

const QUICK_COMMANDS = ["检视保单", "生成方案", "准备面谈", "写跟进"];

const SUGGESTS: { label: string; icon: IconName; desc: string }[] = [
  { label: "检视保单", icon: "table", desc: "盘点托管保单,逐维度计算保障缺口,生成检视矩阵" },
  { label: "生成方案", icon: "fileText", desc: "结合在谈事项与预算,草拟保障方案书" },
  { label: "准备面谈", icon: "users", desc: "汇总要点与提问清单,引用知识库出处" },
  { label: "写跟进", icon: "pen", desc: "起草面谈后的跟进消息,可直接发给客户" },
];

const TYPE_LABEL: Record<string, string> = { personal: "个人", family: "家庭", company: "企业" };

export default function ChatPane({
  client, messages, task, running, streaming, llmAvailable,
  onSend, onQuickCommand, onApprovePlan, onConfirmApproval,
}: Props) {
  const [text, setText] = useState("");
  const [expandedCite, setExpandedCite] = useState<string | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight });
  }, [messages, task?.events.length, task?.status]);

  const submit = () => {
    const t = text.trim();
    if (!t || running) return;
    onSend(t);
    setText("");
    if (inputRef.current) inputRef.current.style.height = "auto";
  };

  const autoGrow = (el: HTMLTextAreaElement) => {
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 140) + "px";
  };

  const doneCount = task
    ? task.events.filter((e) => e.status === "done" || e.status === "confirmed").length
    : 0;
  const openEngagements = client.engagements;

  return (
    <main className="wb-main">
      {/* 客户上下文条 */}
      <div className="wb-context">
        <span className="wb-context-name">{client.name}</span>
        <span className="wb-context-pill">{TYPE_LABEL[client.type] || client.type}</span>
        <span className="wb-context-pill">{client.members.length} 位成员</span>
        <span className="wb-context-pill">托管 {client.policies.length} 单</span>
        <span className="wb-context-right">
          {openEngagements.slice(0, 3).map((e) => (
            <span key={e.id} className={"wb-tag " + (e.kind === "claim" ? "bad" : e.kind === "underwriting" || e.kind === "renewal" ? "warn" : "")} title={e.title}>
              <i />{e.kindLabel}
            </span>
          ))}
        </span>
      </div>

      <div className="wb-chat" ref={listRef}>
        <div className="wb-thread">
          {messages.length === 0 && !task && (
            <div className="wb-hero">
              <div className="wb-hero-logo"><Icon name="shield" size={17} strokeWidth={2} /></div>
              <div className="wb-hero-title">{client.name} 的专属助理</div>
              <div className="wb-hero-sub">
                直接提问,或选择一个任务;回答会引用知识库与该客户的私有资料。
                {!llmAvailable && <span style={{ color: "var(--w-warn-ink)" }}>(当前为演示模式,未配置千帆 key)</span>}
              </div>
              <div className="wb-suggests">
                {SUGGESTS.map((s) => (
                  <button key={s.label} className="wb-suggest" disabled={running} onClick={() => onQuickCommand(s.label)}>
                    <Icon name={s.icon} size={15} />
                    <div className="wb-suggest-title">{s.label}</div>
                    <div className="wb-suggest-desc">{s.desc}</div>
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((m, idx) => (
            <div key={m.id} className={"wb-msg " + m.role}>
              <div
                className={
                  "wb-bubble" +
                  (m.role === "assistant" && streaming && idx === messages.length - 1 ? " streaming" : "")
                }
              >
                {m.content}
              </div>
              {m.role === "assistant" && m.citations.length > 0 && (
                <div className="wb-cites">
                  {m.citations.map((c, i) => (
                    <React.Fragment key={c.chunkId}>
                      <button
                        className={"wb-cite" + (expandedCite === c.chunkId ? " open" : "")}
                        title={`${c.docTitle} · 相关度 ${c.score.toFixed(2)}`}
                        onClick={() => setExpandedCite(expandedCite === c.chunkId ? null : c.chunkId)}
                      >
                        [{i + 1}] {c.docTitle}
                      </button>
                      {expandedCite === c.chunkId && (
                        <div className="wb-cite-pop">
                          {c.text}
                          {"\n"}
                          <span className="src">《{c.docTitle}》 · score {c.score.toFixed(2)}</span>
                        </div>
                      )}
                    </React.Fragment>
                  ))}
                </div>
              )}
            </div>
          ))}

          {/* 计划卡 / 执行时间线 */}
          {task && (
            <div className="wb-card">
              <div className="wb-card-head">
                <Icon name="clipboard" size={14} />
                {task.title}
                <span
                  className={
                    "wb-card-status" +
                    (task.status === "planned" ? " waiting" : task.status === "done" ? " done-ok" : "")
                  }
                >
                  {task.status === "planned" ? (
                    <>待确认计划</>
                  ) : task.status === "done" ? (
                    <><Icon name="check" size={12} strokeWidth={2.5} /> 已完成</>
                  ) : (
                    <>
                      <Spinner size={11} /> 执行中
                      <span className="wb-mono">{doneCount}/{task.plan.length}</span>
                    </>
                  )}
                </span>
              </div>
              <div className="wb-card-body">
                {task.status === "planned" ? (
                  <div>
                    {task.plan.map((s, i) => (
                      <div key={i} className="wb-plan-step">
                        <Icon name="circle" size={13} strokeWidth={1.6} />
                        {s.title}
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="wb-timeline">
                    {task.events.map((e, ei) => {
                      const isLast = ei === task.events.length - 1;
                      const runningNow = e.status === "running" || (isLast && running && e.status !== "waiting_confirm" && task.status !== "done");
                      return (
                        <div key={e.id} className="wb-tl-item">
                          <span className="wb-tl-icon">
                            {e.status === "done" || e.status === "confirmed" ? (
                              <span className="done"><Icon name="check" size={13} strokeWidth={2.6} /></span>
                            ) : e.status === "waiting_confirm" ? (
                              <span className="approval" />
                            ) : e.status === "failed" ? (
                              <span className="failed"><Icon name="x" size={13} strokeWidth={2.4} /></span>
                            ) : runningNow ? (
                              <span className="running"><Spinner size={13} /></span>
                            ) : (
                              <span className="pending" />
                            )}
                          </span>
                          <div style={{ flex: 1, minWidth: 0 }}>
                            <div className={"wb-tl-title" + (runningNow ? " running-text" : "")}>{e.title}</div>
                            {e.type === "tool" && e.status === "done" && renderToolDetail(e.payload)}
                            {e.type === "approval" && e.status === "waiting_confirm" && (
                              <div className="wb-approval">
                                <div className="wb-approval-title">
                                  <Icon name="alert" size={13} /> 需要你的确认
                                </div>
                                <div className="wb-approval-hint">{e.payload?.hint || "该步骤涉及敏感信息,请确认后继续。"}</div>
                                <button className="wb-btn" onClick={() => onConfirmApproval(e.id)}>确认并继续</button>
                              </div>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
              {task.status === "planned" && (
                <div className="wb-card-actions">
                  <button className="wb-btn" disabled={running} onClick={onApprovePlan}>
                    按此计划执行
                  </button>
                  <button className="wb-btn ghost">调整计划</button>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      <div className="wb-composer">
        <div className="wb-composer-inner">
          <div className="wb-cbox">
            <textarea
              ref={inputRef}
              className="wb-input"
              rows={1}
              placeholder={`询问,或给 ${client.name} 的助理布置一个任务…`}
              value={text}
              onChange={(e) => { setText(e.target.value); autoGrow(e.target); }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  submit();
                }
              }}
            />
            <div className="wb-cbar">
              {QUICK_COMMANDS.map((label) => (
                <button key={label} className="wb-chip" disabled={running} onClick={() => onQuickCommand(label)}>
                  {label}
                </button>
              ))}
              <button
                className="wb-send"
                title="发送(⏎)"
                disabled={!text.trim() || running}
                onClick={submit}
              >
                {streaming ? <Spinner size={13} /> : <Icon name="arrowUp" size={14} strokeWidth={2.4} />}
              </button>
            </div>
          </div>
        </div>
      </div>
    </main>
  );
}

function renderToolDetail(payload: any) {
  if (!payload) return null;
  if (payload.hits) {
    return (
      <div className="wb-tl-detail">
        {payload.hits.slice(0, 3).map((h: Citation, i: number) => (
          <div key={h.chunkId}>[{i + 1}] 《{h.docTitle}》 {h.score.toFixed(2)}</div>
        ))}
      </div>
    );
  }
  if (payload.policies) {
    const n = payload.policies.count ?? payload.policies.policies?.length ?? 0;
    return (
      <div className="wb-tl-detail">
        {n > 0 ? `调取 ${n} 份托管保单` : "该客户暂无托管保单"}
      </div>
    );
  }
  if (payload.rows) {
    return <div className="wb-tl-detail">{payload.rows.length} 行 × {payload.cols.length} 个保障维度</div>;
  }
  if (payload.artifact) {
    return <div className="wb-tl-detail">工件《{payload.artifact.title}》 v{payload.artifact.version}</div>;
  }
  return null;
}
