import { useEffect, useRef, useState } from "react";
import type { Bootstrap, Citation, KbDoc } from "./api";
import { api } from "./api";
import { Icon } from "./icons";
import type { IconName } from "./icons";

interface Props {
  kbSummary: Bootstrap["kb"];
  activeClientId: string | null;
  activeClientName: string;
  onSummaryChange: (kb: Bootstrap["kb"]) => void;
  onToast: (msg: string) => void;
}

const DOC_ICON: Record<string, IconName> = {
  pdf: "fileText",
  docx: "fileText",
  txt: "fileText",
  md: "fileText",
  html: "globe",
  text: "pen",
  url: "link",
};

const STATUS_LABEL: Record<string, string> = {
  indexed: "已入库",
  failed: "失败",
  parsing: "解析中",
  uploaded: "上传中",
};

/** 知识库视图:上传 / 文档列表 / 详情与 chunk / 检索测试 */
export default function KnowledgeView({ kbSummary, activeClientId, activeClientName, onSummaryChange, onToast }: Props) {
  const [docs, setDocs] = useState<KbDoc[]>([]);
  const [activeDoc, setActiveDoc] = useState<KbDoc | null>(null);
  const [filter, setFilter] = useState("");
  const [dragging, setDragging] = useState(false);
  const [pasteOpen, setPasteOpen] = useState(false);
  const [pasteTitle, setPasteTitle] = useState("");
  const [pasteText, setPasteText] = useState("");
  const [urlValue, setUrlValue] = useState("");
  const [scope, setScope] = useState("global");
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<Citation[] | null>(null);
  const [searching, setSearching] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const reload = async () => {
    const list = await api.kbDocuments();
    setDocs(list);
    onSummaryChange({ docs: list.length, indexed: list.filter((d) => d.status === "indexed").length });
    return list;
  };

  useEffect(() => {
    reload().catch((e) => onToast(`加载文档列表失败: ${e.message}`));
    const timer = setInterval(() => {
      // 解析中的文档轮询刷新
      setDocs((prev) => {
        if (prev.some((d) => d.status === "parsing" || d.status === "uploaded")) {
          reload().catch(() => {});
        }
        return prev;
      });
    }, 2000);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const openDoc = async (id: string) => {
    const doc = await api.kbDocument(id);
    setActiveDoc(doc);
  };

  const doUpload = async (files: FileList | null) => {
    if (!files?.length) return;
    for (const file of Array.from(files)) {
      try {
        await api.kbUpload(file, file.name.replace(/\.[^.]+$/, ""), scope, []);
        onToast(`「${file.name}」上传成功,正在解析入库…`);
      } catch (e: any) {
        onToast(`「${file.name}」上传失败: ${e.message}`);
      }
    }
    await reload();
  };

  const submitPaste = async () => {
    if (!pasteText.trim() || !pasteTitle.trim()) return;
    try {
      await api.kbUploadText(pasteTitle, pasteText, scope, []);
      onToast("文本已入库");
      setPasteOpen(false);
      setPasteTitle("");
      setPasteText("");
      await reload();
    } catch (e: any) {
      onToast(`入库失败: ${e.message}`);
    }
  };

  const submitUrl = async () => {
    if (!urlValue.trim()) return;
    try {
      onToast("正在抓取网页…");
      await fetch("/api/kb/documents/url", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: urlValue.trim(), scope }),
      }).then(async (r) => {
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`);
        return r.json();
      });
      onToast("网页已入库");
      setUrlValue("");
      await reload();
    } catch (e: any) {
      onToast(`抓取失败: ${e.message}`);
    }
  };

  const doSearch = async () => {
    if (!query.trim()) return;
    setSearching(true);
    try {
      const r = await api.kbSearch(query, activeClientId || undefined);
      setHits(r.hits);
    } catch (e: any) {
      onToast(`检索失败: ${e.message}`);
    } finally {
      setSearching(false);
    }
  };

  const filtered = docs.filter(
    (d) => !filter || d.title.includes(filter) || d.tags.some((t) => t.includes(filter))
  );

  return (
    <div className="wb-kb">
      {/* 左:文档列表 */}
      <div className="wb-kb-list">
        <div className="wb-section">
          知识库 <span className="wb-count">{kbSummary.indexed}/{kbSummary.docs} 已入库</span>
        </div>
        <div className="wb-kb-search">
          <input
            className="wb-text-input"
            style={{ width: "100%" }}
            placeholder="搜索标题或标签…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        </div>
        <div className="wb-kb-list-scroll">
          {filtered.map((d) => (
            <div key={d.id} className={"wb-kb-doc" + (activeDoc?.id === d.id ? " active" : "")} onClick={() => openDoc(d.id)}>
              <div className="wb-kb-doc-title">
                <Icon name={DOC_ICON[d.docType] || "fileText"} size={13.5} />
                <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{d.title}</span>
                <span className={"wb-status " + d.status} style={{ marginLeft: "auto", flexShrink: 0 }}>
                  <i />{STATUS_LABEL[d.status] || d.status}
                </span>
              </div>
              <div className="wb-kb-doc-meta">
                <span className="wb-mono">{d.chunkCount} 段</span>
                <span>{d.scope === "global" ? "全局" : "客户私有"}</span>
                {d.tags.map((t) => <span key={t}>#{t}</span>)}
              </div>
            </div>
          ))}
          {filtered.length === 0 && <div className="wb-empty">还没有文档,从右侧上传开始。</div>}
        </div>
      </div>

      {/* 右:上传 + 详情 + 检索测试 */}
      <div className="wb-kb-main">
        <div className="wb-kb-section">
          <div className="wb-kb-section-title"><Icon name="upload" size={14} /> 上传资料</div>
          <div
            className={"wb-drop" + (dragging ? " drag" : "")}
            onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => { e.preventDefault(); setDragging(false); doUpload(e.dataTransfer.files); }}
            onClick={() => fileRef.current?.click()}
          >
            拖拽文件到这里,或点击选择文件<br />
            <span style={{ fontSize: 11.5, color: "var(--w-faint)" }}>支持 PDF / Word / TXT / Markdown / HTML,单文件 ≤ 20MB</span>
          </div>
          <input
            ref={fileRef}
            type="file"
            multiple
            accept=".pdf,.docx,.txt,.md,.html,.htm"
            style={{ display: "none" }}
            onChange={(e) => { doUpload(e.target.files); e.target.value = ""; }}
          />
          <div className="wb-drop-row">
            <select className="wb-select" value={scope} onChange={(e) => setScope(e.target.value)}>
              <option value="global">归档:全局知识库</option>
              {activeClientId && <option value={`client:${activeClientId}`}>归档:{activeClientName} 私有</option>}
            </select>
            <button className="wb-btn ghost" onClick={() => setPasteOpen(!pasteOpen)}>
              <Icon name="pen" size={12} /> 粘贴文本
            </button>
            <input
              className="wb-text-input"
              style={{ flex: 1, minWidth: 200 }}
              placeholder="或输入网页 URL 抓取正文…"
              value={urlValue}
              onChange={(e) => setUrlValue(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && submitUrl()}
            />
            <button className="wb-btn ghost" onClick={submitUrl}>抓取</button>
          </div>
          {pasteOpen && (
            <div className="wb-drop-row" style={{ flexDirection: "column", alignItems: "stretch" }}>
              <input className="wb-text-input" placeholder="文档标题" value={pasteTitle} onChange={(e) => setPasteTitle(e.target.value)} />
              <textarea className="wb-textarea" placeholder="粘贴要入库的文本内容…" value={pasteText} onChange={(e) => setPasteText(e.target.value)} />
              <button className="wb-btn" style={{ alignSelf: "flex-start" }} onClick={submitPaste}>入库</button>
            </div>
          )}
        </div>

        {activeDoc && (
          <div className="wb-kb-section">
            <div className="wb-kb-section-title">
              <Icon name="fileText" size={14} /> 《{activeDoc.title}》
              <span className={"wb-status " + activeDoc.status}><i />{STATUS_LABEL[activeDoc.status] || activeDoc.status}</span>
              <span style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
                <button className="wb-chip" onClick={async () => { await api.kbReindex(activeDoc.id); onToast("已重新入库"); await reload(); }}>
                  重建索引
                </button>
                <button
                  className="wb-chip"
                  style={{ color: "var(--w-bad)" }}
                  onClick={async () => {
                    if (!confirm(`确定删除《${activeDoc.title}》及其全部 ${activeDoc.chunkCount} 个片段?`)) return;
                    await api.kbDelete(activeDoc.id);
                    setActiveDoc(null);
                    onToast("已删除");
                    await reload();
                  }}
                >
                  删除
                </button>
              </span>
            </div>
            {activeDoc.status === "failed" && (
              <div className="wb-hit-text" style={{ color: "var(--w-bad-ink)" }}>失败原因: {activeDoc.error}</div>
            )}
            <div style={{ fontSize: 12, color: "var(--w-muted)", marginBottom: 8 }}>
              <span className="wb-mono">{activeDoc.chunkCount}</span> 个片段 · <span className="wb-mono">{(activeDoc.sizeBytes / 1024).toFixed(1)} KB</span> ·
              向量化: {activeDoc.chunks?.[0]?.hasEmbedding ? "千帆 embedding" : "演示模式"}
            </div>
            {(activeDoc.chunks || []).slice(0, 12).map((c) => (
              <div key={c.id} className="wb-chunk">
                <div className="wb-chunk-idx">#{c.seq + 1}</div>
                {c.text}
              </div>
            ))}
            {(activeDoc.chunks?.length || 0) > 12 && (
              <div className="wb-chunk-idx">…共 {activeDoc.chunks!.length} 个片段,仅展示前 12 个</div>
            )}
          </div>
        )}

        <div className="wb-kb-section">
          <div className="wb-kb-section-title">
            <Icon name="search" size={14} /> 检索测试
            <span style={{ fontSize: 11.5, color: "var(--w-muted)", fontWeight: 400 }}>看看 AI 会召回什么(含当前客户私有库)</span>
          </div>
          <div className="wb-drop-row" style={{ marginTop: 0 }}>
            <input
              className="wb-text-input"
              style={{ flex: 1 }}
              placeholder="输入问题,如:甲状腺结节买重疾险会除外吗?"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && doSearch()}
            />
            <button className="wb-btn" disabled={searching} onClick={doSearch}>{searching ? "检索中…" : "检索"}</button>
          </div>
          {hits !== null && (
            <div style={{ marginTop: 12 }}>
              {hits.length === 0 && <div className="wb-empty">没有召回结果,试试换一个说法。</div>}
              {hits.map((h, i) => (
                <div key={h.chunkId} className="wb-hit">
                  <div className="wb-hit-head">
                    [{i + 1}] 《{h.docTitle}》
                    <span className="wb-hit-score">score {h.score.toFixed(3)}</span>
                  </div>
                  <div className="wb-hit-text">{h.text}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
