import { useCallback, useEffect, useState } from "react";
import "./workbench.css";
import type { ArtifactOut, BillingStatus, Bootstrap, Client, ClientType, MessageOut, TaskOut, Todo } from "./api";
import { api, authToken, chatStream } from "./api";
import ClientPane from "./ClientPane";
import ChatPane from "./ChatPane";
import ArtifactPane from "./ArtifactPane";
import KnowledgeView from "./KnowledgeView";
import BillingView from "./BillingView";
import { Icon } from "./icons";

const fmtCredits = (n: number) => (n >= 10_000 ? `${(n / 1000).toFixed(1)}k` : n.toLocaleString());

const QUICK_KIND: Record<string, string> = {
  检视保单: "policy_review",
  生成方案: "generate_plan",
  准备面谈: "prepare_visit",
  写跟进: "followup",
};

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export default function Workbench() {
  const [boot, setBoot] = useState<Bootstrap | null>(null);
  const [view, setView] = useState<"home" | "kb" | "billing">("home");
  const [billing, setBilling] = useState<BillingStatus | null>(null);
  const [clients, setClients] = useState<Client[]>([]);
  const [todos, setTodos] = useState<Todo[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<MessageOut[]>([]);
  const [artifacts, setArtifacts] = useState<ArtifactOut[]>([]);
  const [task, setTask] = useState<TaskOut | null>(null);
  const [running, setRunning] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [creating, setCreating] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  const client = clients.find((c) => c.id === activeId) || null;

  const showToast = useCallback((msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(null), 2600);
  }, []);

  // 初始化
  useEffect(() => {
    api
      .bootstrap()
      .then((b) => {
        setBoot(b);
        setClients(b.clients);
        setTodos(b.todos);
        setActiveId(b.clients[0]?.id ?? null);
      })
      .catch((e) => showToast(`初始化失败: ${e.message}`));
    // 已登录用户拉积分余额(未登录静默跳过)
    if (authToken.get()) {
      api.billingStatus().then(setBilling).catch(() => authToken.clear());
    }
    // 从 Stripe Checkout 回跳
    const pay = new URLSearchParams(location.search).get("pay");
    if (pay === "success") showToast("支付成功,权益到账中(异步支付以回调为准)");
    if (pay === "cancel") showToast("已取消支付");
  }, [showToast]);

  const refreshBilling = useCallback(() => {
    if (authToken.get()) {
      api.billingStatus().then(setBilling).catch(() => {});
    } else {
      setBilling(null);
    }
  }, []);

  // 切换客户 -> 加载消息与工件
  useEffect(() => {
    if (!activeId) return;
    setTask(null);
    api.messages(activeId).then(setMessages).catch(() => setMessages([]));
    api.artifacts(activeId).then(setArtifacts).catch(() => setArtifacts([]));
  }, [activeId]);

  const refreshArtifacts = useCallback(async () => {
    if (!activeId) return;
    setArtifacts(await api.artifacts(activeId));
  }, [activeId]);

  // ---------- 对话 ----------
  const handleSend = async (text: string) => {
    if (!client || streaming) return;
    const localUser: MessageOut = {
      id: `local-${Date.now()}`,
      clientId: client.id,
      role: "user",
      content: text,
      citations: [],
      taskId: null,
      createdAt: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, localUser]);
    setStreaming(true);
    try {
      let acc = "";
      const localAssistant: MessageOut = {
        id: `local-a-${Date.now()}`,
        clientId: client.id,
        role: "assistant",
        content: "",
        citations: [],
        toolEvents: [],
        taskId: null,
        createdAt: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, localAssistant]);
      const patchAssistant = (patch: (m: MessageOut) => MessageOut) =>
        setMessages((prev) => prev.map((m) => (m.id === localAssistant.id ? patch(m) : m)));

      const { citations, toolEvents, content } = await chatStream(client.id, text, {
        onDelta: (delta) => {
          acc += delta;
          patchAssistant((m) => ({ ...m, content: acc }));
        },
        onToolStart: (ev) =>
          patchAssistant((m) => ({ ...m, toolEvents: [...(m.toolEvents || []), ev] })),
        onToolEnd: (ev) =>
          patchAssistant((m) => {
            const list = [...(m.toolEvents || [])];
            // 把最早一个仍在 running 的同名(或未命名)事件落成完成态
            const idx = list.findIndex((t) => t.running);
            if (idx >= 0) list[idx] = { ...ev, running: false };
            else list.push(ev);
            return { ...m, toolEvents: list };
          }),
      });
      patchAssistant((m) => ({
        ...m,
        citations,
        toolEvents,
        // 服务端可能清洗过全文(剔除伪工具调用),以它为准
        ...(content !== undefined ? { content } : {}),
      }));
      // agent 可能在对话中生成了工件(generate_document)
      if (toolEvents.some((t) => t.name === "generate_document")) {
        await refreshArtifacts();
      }
      refreshBilling();
    } catch (e: any) {
      showToast(`对话失败: ${e.message}`);
    } finally {
      setStreaming(false);
    }
  };

  // ---------- 任务流 ----------
  const handleQuickCommand = async (label: string) => {
    if (!client || running) return;
    try {
      const t = await api.createTask(client.id, QUICK_KIND[label] || "generic", label);
      setTask(t);
    } catch (e: any) {
      showToast(`创建任务失败: ${e.message}`);
    }
  };

  const runSteps = useCallback(
    async (taskId: string) => {
      setRunning(true);
      try {
        for (;;) {
          const r = await api.stepTask(taskId);
          setTask((prev) =>
            prev && prev.id === taskId
              ? { ...prev, status: r.taskStatus, events: [...prev.events, r.event] }
              : prev
          );
          if (r.awaiting) return;          // 停在审批卡,等用户确认
          if (r.taskStatus === "done") break;
          await sleep(650);                // 节奏感:时间线逐步点亮
        }
        await refreshArtifacts();
        showToast("任务完成,工件已生成");
      } catch (e: any) {
        showToast(`执行失败: ${e.message}`);
      } finally {
        setRunning(false);
      }
    },
    [refreshArtifacts, showToast]
  );

  const handleApprovePlan = async () => {
    if (!task) return;
    try {
      const t = await api.approveTask(task.id);
      setTask({ ...t, events: [] });
      runSteps(task.id);
    } catch (e: any) {
      showToast(`确认失败: ${e.message}`);
    }
  };

  const handleConfirmApproval = async (eventId: string) => {
    if (!task) return;
    try {
      const { event } = await api.confirmEvent(task.id, eventId);
      setTask((prev) =>
        prev
          ? { ...prev, events: prev.events.map((e) => (e.id === event.id ? event : e)) }
          : prev
      );
      runSteps(task.id);
    } catch (e: any) {
      showToast(`确认失败: ${e.message}`);
    }
  };

  // ---------- 客户 ----------
  const handleCreateClient = async (name: string, type: ClientType, files: File[]) => {
    setCreating(true);
    try {
      const c = await api.createClient(name, type);
      if (files.length > 0) {
        try {
          await api.uploadClientFiles(c.id, files);
          showToast(`已创建「${c.name}」,${files.length} 份资料上传成功,解析入库中…`);
        } catch (e: any) {
          showToast(`客户已创建,但资料上传失败: ${e.message}`);
        }
        // 上传会改变 fileCount,重取一次
        const b = await api.bootstrap();
        setClients(b.clients);
      } else {
        setClients((prev) => [...prev, c]);
        showToast(`已创建「${c.name}」`);
      }
      setActiveId(c.id);
    } catch (e: any) {
      showToast(`创建失败: ${e.message}`);
    } finally {
      setCreating(false);
    }
  };

  const handleToggleTodo = async (todo: Todo) => {
    try {
      const t = await api.patchTodo(todo.id, "done");
      setTodos((prev) => prev.filter((x) => x.id !== t.id));
    } catch (e: any) {
      showToast(`操作失败: ${e.message}`);
    }
  };

  if (!boot) {
    return (
      <div className="wb-root">
        <div className="wb-empty" style={{ flex: 1, alignSelf: "center" }}>正在加载工作台…</div>
      </div>
    );
  }

  return (
    <div className="wb-root">
      {/* 图标栏 */}
      <nav className="wb-rail">
        <div className="wb-rail-logo">
          <Icon name="shield" size={15} strokeWidth={2} />
        </div>
        <button
          className={"wb-rail-btn" + (view === "home" ? " active" : "")}
          title="工作台"
          onClick={() => setView("home")}
        >
          <Icon name="home" size={18} />
        </button>
        <button
          className={"wb-rail-btn" + (view === "kb" ? " active" : "")}
          title={`知识库 · ${boot.kb.indexed}/${boot.kb.docs} 已入库`}
          onClick={() => setView("kb")}
        >
          <Icon name="database" size={18} />
        </button>
        <button
          className="wb-rail-btn"
          title={`待办 ${todos.length} 项`}
          onClick={() => setView("home")}
        >
          <Icon name="listChecks" size={18} />
          {todos.length > 0 && <span className="wb-badge" />}
        </button>
        <div className="wb-rail-foot">
          <button
            className={"wb-rail-credits" + (view === "billing" ? " active" : "")}
            title={billing ? `可用 ${billing.credits.totalCredits.toLocaleString()} 积分` : "登录 / 套餐与积分"}
            onClick={() => setView("billing")}
          >
            {billing ? (
              <span className="wb-mono">{fmtCredits(billing.credits.totalCredits)}</span>
            ) : (
              <Icon name="user" size={17} />
            )}
          </button>
          <span
            className={"wb-live-dot" + (boot.llm ? "" : " off")}
            title={boot.llm ? "千帆已连接" : "演示模式(未配置千帆 key)"}
          />
        </div>
      </nav>

      {view === "billing" ? (
        <BillingView
          onToast={showToast}
          onAuthChange={(_user, st) => setBilling(st)}
        />
      ) : view === "kb" ? (
        <KnowledgeView
          kbSummary={boot.kb}
          activeClientId={activeId}
          activeClientName={client?.name || ""}
          onSummaryChange={(kb) => setBoot({ ...boot, kb })}
          onToast={showToast}
        />
      ) : client ? (
        <>
          <ClientPane
            clients={clients}
            todos={todos}
            activeId={activeId}
            creating={creating}
            onSelect={setActiveId}
            onCreate={handleCreateClient}
            onOpenTodo={(t) => t.clientId && setActiveId(t.clientId)}
            onToggleTodo={handleToggleTodo}
          />
          <ChatPane
            client={client}
            messages={messages}
            task={task}
            running={running || streaming}
            streaming={streaming}
            llmAvailable={boot.llm}
            onSend={handleSend}
            onQuickCommand={handleQuickCommand}
            onApprovePlan={handleApprovePlan}
            onConfirmApproval={handleConfirmApproval}
          />
          <ArtifactPane artifacts={artifacts} running={running} />
        </>
      ) : (
        <div className="wb-empty" style={{ flex: 1, alignSelf: "center" }}>左侧选择或新建一个客户开始。</div>
      )}

      {toast && <div className="wb-toast">{toast}</div>}
    </div>
  );
}
