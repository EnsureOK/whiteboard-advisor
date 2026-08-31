import { useMemo, useState } from "react";
import type { ArtifactOut } from "./api";
import { Icon, Spinner } from "./icons";
import type { IconName } from "./icons";
import { Markdown } from "./markdown";

interface Props {
  artifacts: ArtifactOut[];
  running: boolean;
}

const TYPE_ICON: Record<string, IconName> = {
  review_matrix: "table",
  plan_doc: "fileText",
  visit_outline: "users",
  followup_msg: "pen",
  checklist: "listChecks",
  report: "chart",
};

/** 工件类型 -> 可下载的办公文件格式 */
const TYPE_EXPORTS: Record<string, { fmt: string; label: string }[]> = {
  review_matrix: [{ fmt: "xlsx", label: "Excel" }],
  plan_doc: [
    { fmt: "docx", label: "Word" },
    { fmt: "pptx", label: "PPT" },
  ],
  visit_outline: [
    { fmt: "docx", label: "Word" },
    { fmt: "pptx", label: "PPT" },
  ],
  followup_msg: [{ fmt: "docx", label: "Word" }],
};

/** 右栏:工作区(工件列表 + 保单检视矩阵视图 + 导出) */
export default function ArtifactPane({ artifacts, running }: Props) {
  const [activeId, setActiveId] = useState<string | null>(null);
  const active = artifacts.find((a) => a.id === activeId) || artifacts[0] || null;

  return (
    <aside className="wb-right">
      <div className="wb-right-head">
        工作区
        <span className="wb-right-status">
          {running ? (
            <><Spinner size={11} /> 生成中</>
          ) : (
            <span className="wb-mono">{artifacts.length} 份</span>
          )}
        </span>
      </div>
      <div className="wb-artifact-list">
        {artifacts.length === 0 && (
          <div className="wb-empty">任务产出的方案书、检视报告会出现在这里,<br />可导出 PDF 或发给客户。</div>
        )}
        {artifacts.map((a) => (
          <button
            key={a.id}
            className={"wb-artifact-item" + (active?.id === a.id ? " active" : "")}
            onClick={() => setActiveId(a.id)}
          >
            <Icon name={TYPE_ICON[a.type] || "fileText"} size={14} />
            <span className="wb-artifact-name">{a.title}</span>
            <span className="wb-artifact-meta">v{a.version}</span>
          </button>
        ))}
      </div>

      {active && <ArtifactView artifact={active} />}
    </aside>
  );
}

function ArtifactView({ artifact }: { artifact: ArtifactOut }) {
  const content = artifact.content || {};
  const isMatrix = content.kind === "review_matrix";
  const isDoc = content.kind === "doc";

  const exportText = useMemo(() => {
    if (isDoc) {
      const lines = [`《${artifact.title}》`, content.summary || ""];
      for (const sec of content.sections || []) {
        lines.push(`\n【${sec.heading}】\n${sec.body}`);
      }
      return lines.join("\n");
    }
    if (!isMatrix) return JSON.stringify(content, null, 2);
    const lines = [`《${artifact.title}》`, content.summary || ""];
    for (const row of content.rows || []) {
      lines.push(`\n【${row.member}】`);
      for (const col of content.cols || []) {
        const cell = row.cells?.[col];
        if (cell) lines.push(`  ${col}: ${cell.text}`);
      }
    }
    for (const ex of content.extras || []) {
      lines.push(`\n附注: ${ex.line} ${ex.productName || ""} 保额 ${(ex.amount / 10000).toFixed(0)} 万`);
    }
    return lines.join("\n");
  }, [artifact, isMatrix, isDoc]);

  const exportPdf = () => {
    const win = window.open("", "_blank");
    if (!win) return;
    const tableHtml = isMatrix ? matrixHtml(content) : `<pre>${exportText}</pre>`;
    win.document.write(`<!doctype html><html><head><meta charset="utf-8"><title>${artifact.title}</title>
      <style>body{font-family:-apple-system,"PingFang SC",sans-serif;padding:32px;color:#1A1A19}
      h1{font-size:20px} pre{white-space:pre-wrap;font-size:13px}
      table{border-collapse:collapse;width:100%;font-size:12.5px}
      th,td{border:1px solid #E7E7E4;padding:7px 8px;text-align:center}
      th{background:#F7F7F5}.rowhead{background:#F7F7F5;font-weight:600}</style></head>
      <body><h1>${artifact.title}</h1><p style="color:#6E6E6B;font-size:12px">${content.summary || ""}</p>${tableHtml}
      <script>window.onload=()=>window.print()<\/script></body></html>`);
    win.document.close();
  };

  const sendToWechat = async () => {
    try {
      await navigator.clipboard.writeText(exportText);
      alert("已复制工件文本,可粘贴到微信发给客户。(对接微信发送通道后为一键发送)");
    } catch {
      alert("复制失败,请手动选择文本。");
    }
  };

  return (
    <>
      <div className="wb-artifact-view">
        <div className="wb-artifact-title">{artifact.title}</div>
        <div className="wb-artifact-summary">
          <span className="wb-mono">v{artifact.version}</span> · {new Date(artifact.createdAt).toLocaleString("zh-CN")}
          {content.summary ? <><br />{content.summary}</> : null}
        </div>
        {isMatrix ? (
          <>
            <MatrixTable content={content} />
            {(content.extras || []).length > 0 && (
              <div className="wb-extras">
                <b>未计入矩阵的保单</b>
                {content.extras.map((ex: any, i: number) => (
                  <div key={i}>
                    {ex.line} {ex.productName || ""} · 保额 <span className="wb-mono">{(ex.amount / 10000).toFixed(0)} 万</span>
                  </div>
                ))}
              </div>
            )}
          </>
        ) : isDoc ? (
          <div className="wb-doc">
            {(content.sections || []).map((sec: any, i: number) => (
              <section key={i}>
                <div className="wb-doc-heading">{sec.heading}</div>
                <Markdown className="wb-doc-body" text={sec.body} />
              </section>
            ))}
          </div>
        ) : (
          <pre style={{ whiteSpace: "pre-wrap", fontSize: 12.5 }}>{exportText}</pre>
        )}
      </div>
      <div className="wb-artifact-actions">
        {(TYPE_EXPORTS[artifact.type] || []).map((e) => (
          <button
            key={e.fmt}
            className="wb-btn"
            onClick={() => window.open(`/api/workbench/artifacts/${artifact.id}/export?fmt=${e.fmt}`)}
          >
            <Icon name="download" size={13} /> {e.label}
          </button>
        ))}
        <button className="wb-btn ghost" onClick={exportPdf}>PDF</button>
        <button className="wb-btn ghost" onClick={sendToWechat}>
          <Icon name="send" size={13} /> 发微信
        </button>
      </div>
    </>
  );
}

function MatrixTable({ content }: { content: any }) {
  return (
    <table className="wb-matrix">
      <thead>
        <tr>
          <th>成员</th>
          {(content.cols || []).map((c: string) => <th key={c}>{c}</th>)}
        </tr>
      </thead>
      <tbody>
        {(content.rows || []).map((row: any, ri: number) => (
          <tr key={row.memberId}>
            <td className="rowhead">{row.member}</td>
            {(content.cols || []).map((col: string, ci: number) => {
              const cell = row.cells?.[col];
              if (!cell) return <td key={col}><span className="wb-cell empty">—</span></td>;
              return (
                <td key={col}>
                  <span
                    className={`wb-cell ${cell.level} fill`}
                    style={{ animationDelay: `${(ri * 5 + ci) * 70}ms` }}
                    title={`已有 ${(cell.current / 10000).toFixed(0)} 万 / 建议 ${(cell.need / 10000).toFixed(0)} 万`}
                  >
                    <i />
                    {cell.text}
                  </span>
                </td>
              );
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function matrixHtml(content: any): string {
  const head = (content.cols || []).map((c: string) => `<th>${c}</th>`).join("");
  const rows = (content.rows || [])
    .map(
      (row: any) =>
        `<tr><td class="rowhead">${row.member}</td>` +
        (content.cols || [])
          .map((col: string) => {
            const cell = row.cells?.[col];
            return `<td>${cell ? cell.text : "—"}</td>`;
          })
          .join("") +
        `</tr>`
    )
    .join("");
  return `<table><thead><tr><th>成员</th>${head}</tr></thead><tbody>${rows}</tbody></table>`;
}
