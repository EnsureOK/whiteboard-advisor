import { useEffect, useState } from "react";
import { api } from "./api";
import { Icon, Spinner } from "./icons";

type TaskRow = Awaited<ReturnType<typeof api.listTasks>>[number];

interface Props {
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

/** 任务中心:全部客户的任务与状态(服务端自动执行,离开页面照跑)。 */
export default function TasksView({ onOpenTask, onToast }: Props) {
  const [rows, setRows] = useState<TaskRow[] | null>(null);

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
        </div>
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
