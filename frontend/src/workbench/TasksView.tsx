import { useEffect, useState } from "react";
import type { Client } from "./api";
import { api } from "./api";
import { Icon, Spinner } from "./icons";

type TaskRow = Awaited<ReturnType<typeof api.listTasks>>[number];

interface Props {
  clients: Client[];
  onOpenTask: (clientId: string, taskId: string) => void;
  onToast: (msg: string) => void;
}

const STATUS_META: Record<string, { label: string; cls: string }> = {
  planned: { label: "待确认", cls: "warn" },
  approved: { label: "已确认", cls: "" },
  running: { label: "执行中", cls: "run" },
  waiting_confirm: { label: "等审批", cls: "warn" },
  done: { label: "已完成", cls: "ok" },
  failed: { label: "失败", cls: "bad" },
};

const KIND_LABEL: Record<string, string> = {
  policy_review: "保单检视",
  generate_plan: "方案书",
  prepare_visit: "面谈准备",
  followup: "跟进",
  generic: "任务",
};

/** 任务中心:全部客户的任务与状态(服务端自动执行,离开页面照跑)+ 批量 fan-out。 */
export default function TasksView({ clients, onOpenTask, onToast }: Props) {
  const [rows, setRows] = useState<TaskRow[] | null>(null);
  const [batchOpen, setBatchOpen] = useState(false);
  const [batchKind, setBatchKind] = useState("policy_review");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [launching, setLaunching] = useState(false);

  const launchBatch = async () => {
    if (picked.size === 0 || launching) return;
    setLaunching(true);
    try {
      const r = await api.createTaskBatch([...picked], batchKind);
      onToast(`批量任务已发起:${r.tasks.length} 个客户并行执行中`);
      setBatchOpen(false);
      setPicked(new Set());
      setRows(await api.listTasks());
    } catch (e: any) {
      onToast(`批量发起失败: ${e.message}`);
    } finally {
      setLaunching(false);
    }
  };

  useEffect(() => {
    let stop = false;
    const load = async () => {
      try {
        const r = await api.listTasks();
        if (!stop) setRows(r);
      } catch (e: any) {
        if (!stop && rows === null) onToast(`加载任务失败: ${e.message}`);
      }
    };
    load();
    const timer = setInterval(load, 3000); // 有进行中任务的实时性
    return () => {
      stop = true;
      clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="wb-tasks">
      <div className="wb-tasks-inner">
        <div className="wb-tasks-head">
          任务中心
          <span className="wb-count wb-mono">{rows?.length ?? "…"}</span>
          <span className="wb-tasks-hint">任务在服务端执行,切换页面或关闭窗口后仍会继续</span>
          <button className="wb-btn" onClick={() => setBatchOpen(!batchOpen)}>
            <Icon name="listChecks" size={13} /> 批量任务
          </button>
        </div>

        {batchOpen && (
          <div className="wb-batch-form">
            <div className="wb-batch-row">
              <select className="wb-select" value={batchKind} onChange={(e) => setBatchKind(e.target.value)}>
                <option value="policy_review">检视保单</option>
                <option value="generate_plan">生成方案书</option>
                <option value="prepare_visit">准备面谈</option>
                <option value="followup">写跟进</option>
              </select>
              <span className="wb-tasks-hint">
                选中 <span className="wb-mono">{picked.size}</span> 个客户,并行执行(同时最多 3 个,其余排队)
              </span>
              <button className="wb-btn" disabled={picked.size === 0 || launching} onClick={launchBatch}>
                {launching ? <Spinner size={12} /> : null}
                发起并执行
              </button>
            </div>
            <div className="wb-batch-clients">
              {clients.map((c) => (
                <label key={c.id} className={"wb-batch-client" + (picked.has(c.id) ? " on" : "")}>
                  <input
                    type="checkbox"
                    checked={picked.has(c.id)}
                    onChange={(e) => {
                      const next = new Set(picked);
                      if (e.target.checked) next.add(c.id);
                      else next.delete(c.id);
                      setPicked(next);
                    }}
                  />
                  {c.name}
                </label>
              ))}
            </div>
          </div>
        )}
        {rows !== null && rows.length === 0 && (
          <div className="wb-empty">
            还没有任务。回到工作台,对助理说一句「帮XX客户做一次保单检视」试试。
          </div>
        )}
        {(rows || []).map((t) => {
          const meta = STATUS_META[t.status] || { label: t.status, cls: "" };
          const active = t.status === "running";
          return (
            <button key={t.id} className="wb-task-row" onClick={() => onOpenTask(t.clientId, t.id)}>
              <span className={"wb-task-status " + meta.cls}>
                {active ? <Spinner size={11} /> : <i />}
                {meta.label}
              </span>
              <span className="wb-task-title">{t.title}</span>
              {t.batchId && <span className="wb-tag" title={`批量组 ${t.batchId.slice(0, 6)}`}>批量</span>}
              <span className="wb-tag">{KIND_LABEL[t.kind] || t.kind}</span>
              <span className="wb-task-meta wb-mono">
                {t.stepsDone}/{t.stepsTotal}
              </span>
              <span className="wb-task-meta wb-mono">{t.createdAt.slice(5, 16).replace("T", " ")}</span>
              <Icon name="chevronRight" size={12} />
            </button>
          );
        })}
      </div>
    </div>
  );
}
