import React, { useEffect, useRef, useState } from "react";
import type { Citation, Client, MessageOut, TaskOut } from "./api";
import { Icon, Spinner } from "./icons";
import type { IconName } from "./icons";
import { Markdown } from "./markdown";

interface Props {
  client: Client;
  messages: MessageOut[];
  task: TaskOut | null;
  running: boolean;
  streaming: boolean;
  llmAvailable: boolean;
  onSend: (text: string, files: File[]) => void;
  onQuickCommand: (label: string) => void;
  onApprovePlan: (plan?: { tool: string; title: string; query?: string }[]) => void;
  onRevisePlan: (instruction: string) => Promise<void> | void;
  onConfirmApproval: (eventId: string) => void;
  onToggleRight?: () => void;
  /** 使用引导:显示状态与交互 */
  onboarding?: {
    visible: boolean;
    loggedIn: boolean;
    welcomeClaimed: boolean;
    onDismiss: () => void;
    onGoBilling: () => void;
  };
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
  onSend, onQuickCommand, onApprovePlan, onRevisePlan, onConfirmApproval, onToggleRight, onboarding,
}: Props) {
  const [text, setText] = useState("");
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const attachRef = useRef<HTMLInputElement>(null);
  const [expandedCite, setExpandedCite] = useState<string | null>(null);
  const [editingPlan, setEditingPlan] = useState(false);
  const [draftPlan, setDraftPlan] = useState<{ tool: string; title: string; query?: string }[]>([]);
  const [reviseText, setReviseText] = useState("");
  const [revising, setRevising] = useState(false);

  // 任务切换/计划被 AI 更新时,退出编辑态并同步草稿
  useEffect(() => {
    setEditingPlan(false);
    setDraftPlan(task?.plan || []);
    setReviseText("");
  }, [task?.id, task?.plan]);
  const listRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight });
  }, [messages, task?.events.length, task?.status]);

  const submit = () => {
    const t = text.trim();
    if ((!t && pendingFiles.length === 0) || running) return;
    onSend(t || "请看我刚上传的资料。", pendingFiles);
    setText("");
    setPendingFiles([]);
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

  // 任务卡按创建时间插入消息流(修"新消息插到任务卡上方"的时序错乱)
  const lastMsgId = messages[messages.length - 1]?.id;
  const taskPos = task
    ? messages.filter((m) => m.createdAt <= task.createdAt).length
    : messages.length;
  const beforeTask = messages.slice(0, taskPos);
  const afterTask = messages.slice(taskPos);

  const renderMsg = (m: MessageOut) => (
    <div key={m.id} className={"wb-msg " + m.role}>
      {m.role === "assistant" && (m.toolEvents?.length || 0) > 0 && (
        <div className="wb-tools">
          {m.toolEvents!.map((t, i) => (
            <div key={i} className="wb-tool-row">
              {t.running ? (
                <Spinner size={11} />
              ) : (
                <span className="wb-tool-check"><Icon name="check" size={11} strokeWidth={2.6} /></span>
              )}
              <span className={"wb-tool-name" + (t.running ? " running" : "")}>{t.label}</span>
              {t.summary && <span className="wb-tool-sum">{t.summary}</span>}
            </div>
          ))}
        </div>
      )}
      {m.role === "assistant" ? (
        <div className={"wb-bubble" + (streaming && m.id === lastMsgId ? " streaming" : "")}>
          <Markdown text={m.content} />
        </div>
      ) : (
        <div className="wb-bubble">{m.content}</div>
      )}
      {m.role === "assistant" && m.citations.length > 0 && (
        <div className="wb-cites">
          {(() => {
            let kbIdx = 0;
            let webIdx = 0;
            return m.citations.map((c) => {
              const isWeb = c.scope === "web";
              const label = isWeb ? `W${++webIdx}` : `${++kbIdx}`;
              if (isWeb) {
                // 联网来源:点击新窗打开真实原文(URL 在 title 可预览)
                return (
                  <button
                    key={c.chunkId}
                    className="wb-cite web"
                    title={c.url || c.docTitle}
                    onClick={() => c.url && window.open(c.url, "_blank", "noopener")}
                  >
                    <Icon name="globe" size={10} /> [{label}] {c.docTitle}
                  </button>
                );
              }
              return (
                <React.Fragment key={c.chunkId}>
                  <button
                    className={"wb-cite" + (expandedCite === c.chunkId ? " open" : "")}
                    title={`${c.docTitle} · 相关度 ${c.score.toFixed(2)}`}
                    onClick={() => setExpandedCite(expandedCite === c.chunkId ? null : c.chunkId)}
                  >
                    [{label}] {c.docTitle}
                  </button>
                  {expandedCite === c.chunkId && (
                    <div className="wb-cite-pop">
                      {c.text}
                      {"\n"}
                      <span className="src">《{c.docTitle}》 · score {c.score.toFixed(2)}</span>
                    </div>
                  )}
                </React.Fragment>
              );
            });
          })()}
        </div>
      )}
    </div>
  );

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
          {onToggleRight && (
            <button className="wb-right-toggle" title="工作区" onClick={onToggleRight}>
              <Icon name="table" size={15} />
            </button>
          )}
        </span>
      </div>

      <div className="wb-chat" ref={listRef}>
        <div className="wb-thread">
          {onboarding?.visible && (
            <OnboardingPanel
              loggedIn={onboarding.loggedIn}
              welcomeClaimed={onboarding.welcomeClaimed}
              isSampleClient={client.name.includes("示例")}
              onDismiss={onboarding.onDismiss}
              onGoBilling={onboarding.onGoBilling}
              onTryReview={() => onQuickCommand("检视保单")}
            />
          )}
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

          {beforeTask.map(renderMsg)}

          {/* 计划卡 / 执行时间线(按创建时间插在消息流中) */}
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
                  editingPlan ? (
                    <div className="wb-plan-edit">
                      {draftPlan.map((s, i) => (
                        <div key={i} className="wb-plan-edit-row">
                          <span className="wb-plan-num wb-mono">{i + 1}</span>
                          <input
                            className="wb-text-input"
                            value={s.title}
                            onChange={(e) =>
                              setDraftPlan(draftPlan.map((x, j) => (j === i ? { ...x, title: e.target.value } : x)))
                            }
                          />
                          <button
                            className="wb-plan-edit-del"
                            title="删除此步"
                            onClick={() => setDraftPlan(draftPlan.filter((_, j) => j !== i))}
                          >
                            <Icon name="x" size={12} />
                          </button>
                        </div>
                      ))}
                      <button
                        className="wb-plan-add"
                        onClick={() => setDraftPlan([...draftPlan, { tool: "generic", title: "" }])}
                      >
                        <Icon name="plus" size={12} /> 添加步骤
                      </button>
                    </div>
                  ) : (
                    <div>
                      {task.plan.map((s, i) => (
                        <div key={i} className="wb-plan-step">
                          <Icon name="circle" size={13} strokeWidth={1.6} />
                          {s.title}
                        </div>
                      ))}
                    </div>
                  )
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
                <>
                  {/* 让 AI 改计划 */}
                  <div className="wb-plan-revise">
                    <input
                      className="wb-text-input"
                      placeholder="告诉 AI 怎么改,如:去掉审批,加一步生成客户讲解 PPT"
                      value={reviseText}
                      onChange={(e) => setReviseText(e.target.value)}
                      onKeyDown={async (e) => {
                        if (e.key === "Enter" && reviseText.trim() && !revising) {
                          setRevising(true);
                          await onRevisePlan(reviseText.trim());
                          setReviseText("");
                          setRevising(false);
                        }
                      }}
                    />
                    <button
                      className="wb-btn ghost"
                      disabled={!reviseText.trim() || revising}
                      onClick={async () => {
                        setRevising(true);
                        await onRevisePlan(reviseText.trim());
                        setReviseText("");
                        setRevising(false);
                      }}
                    >
                      {revising ? <Spinner size={12} /> : null}
                      AI 修改
                    </button>
                  </div>
                  <div className="wb-card-actions">
                    {editingPlan ? (
                      <>
                        <button
                          className="wb-btn"
                          disabled={running || draftPlan.every((s) => !s.title.trim())}
                          onClick={() => {
                            const cleaned = draftPlan.filter((s) => s.title.trim());
                            setEditingPlan(false);
                            onApprovePlan(cleaned);
                          }}
                        >
                          按调整后的计划执行
                        </button>
                        <button
                          className="wb-btn ghost"
                          onClick={() => {
                            setDraftPlan(task.plan);
                            setEditingPlan(false);
                          }}
                        >
                          取消编辑
                        </button>
                      </>
                    ) : (
                      <>
                        <button className="wb-btn" disabled={running} onClick={() => onApprovePlan()}>
                          按此计划执行
                        </button>
                        <button
                          className="wb-btn ghost"
                          onClick={() => {
                            setDraftPlan(task.plan.map((s) => ({ ...s })));
                            setEditingPlan(true);
                          }}
                        >
                          手动调整
                        </button>
                      </>
                    )}
                  </div>
                </>
              )}
            </div>
          )}

          {afterTask.map(renderMsg)}
        </div>
      </div>

      <div className="wb-composer">
        <div className="wb-composer-inner">
          <div className="wb-cbox">
            {pendingFiles.length > 0 && (
              <div className="wb-file-pills" style={{ marginBottom: 6 }}>
                {pendingFiles.map((f, i) => (
                  <span key={`${f.name}-${i}`} className="wb-file-pill">
                    <Icon name={f.type.startsWith("image/") ? "image" : "fileText"} size={11} />
                    <span>{f.name}</span>
                    <button title="移除" onClick={() => setPendingFiles(pendingFiles.filter((_, j) => j !== i))}>
                      <Icon name="x" size={11} />
                    </button>
                  </span>
                ))}
              </div>
            )}
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
              <button
                className="wb-attach"
                title="附件:体检报告/保单等,自动入该客户私有知识库"
                disabled={running}
                onClick={() => attachRef.current?.click()}
              >
                <Icon name="paperclip" size={14} />
              </button>
              <input
                ref={attachRef}
                type="file"
                multiple
                accept=".pdf,.docx,.txt,.md,.html,.htm,.png,.jpg,.jpeg,.webp,.gif,.heic"
                style={{ display: "none" }}
                onChange={(e) => {
                  if (e.target.files) setPendingFiles((prev) => [...prev, ...Array.from(e.target.files!)]);
                  e.target.value = "";
                }}
              />
              {QUICK_COMMANDS.map((label) => (
                <button key={label} className="wb-chip" disabled={running} onClick={() => onQuickCommand(label)}>
                  {label}
                </button>
              ))}
              <button
                className="wb-send"
                title="发送(⏎)"
                disabled={(!text.trim() && pendingFiles.length === 0) || running}
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

/** 首次使用引导:四步上手,可关闭(rail 问号可重新打开) */
function OnboardingPanel({
  loggedIn,
  welcomeClaimed,
  isSampleClient,
  onDismiss,
  onGoBilling,
  onTryReview,
}: {
  loggedIn: boolean;
  welcomeClaimed: boolean;
  isSampleClient: boolean;
  onDismiss: () => void;
  onGoBilling: () => void;
  onTryReview: () => void;
}) {
  const step1Done = loggedIn && welcomeClaimed;
  const steps = [
    {
      done: step1Done,
      title: step1Done ? "已领取免费积分" : "登录并领取 2,000 免费积分",
      desc: "手机号登录即可,积分用于 AI 对话与文档生成,永不过期。",
      action: step1Done ? null : { label: loggedIn ? "去领取" : "去登录", onClick: onGoBilling },
    },
    {
      done: false,
      title: "用示例客户体验一次「检视保单」",
      desc: isSampleClient
        ? "就是当前这位——点右侧按钮发起,确认计划后看 AI 逐步执行、生成检视矩阵。"
        : "点左侧「示例·陈家明一家」,再发送快捷指令「检视保单」。",
      action: isSampleClient ? { label: "立即发起", onClick: onTryReview } : null,
    },
    {
      done: false,
      title: "新增你的第一个真实客户",
      desc: "左栏「+ 新增客户」,选择个人/家庭/企业;拖入保单、体检报告等资料,会自动进入该客户的私有知识库,AI 回答时能直接引用。",
      action: null,
    },
    {
      done: false,
      title: "对话提问,或让助理产出文档",
      desc: "直接输入问题(回答自动引用知识库);说「出一份保障方案书」即可生成 Word/PPT,在右侧工作区下载。",
      action: null,
    },
  ];
  return (
    <div className="wb-onboard">
      <div className="wb-onboard-head">
        <span className="wb-onboard-title">
          <Icon name="sparkles" size={14} /> 四步上手
        </span>
        <button className="wb-onboard-close" title="关闭引导(左侧 ? 可再次打开)" onClick={onDismiss}>
          <Icon name="x" size={13} />
        </button>
      </div>
      {steps.map((s, i) => (
        <div key={i} className={"wb-onboard-step" + (s.done ? " done" : "")}>
          <span className="wb-onboard-num">
            {s.done ? <Icon name="check" size={11} strokeWidth={2.6} /> : i + 1}
          </span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="wb-onboard-step-title">{s.title}</div>
            <div className="wb-onboard-step-desc">{s.desc}</div>
          </div>
          {s.action && (
            <button className="wb-btn" onClick={s.action.onClick}>
              {s.action.label}
            </button>
          )}
        </div>
      ))}
    </div>
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
