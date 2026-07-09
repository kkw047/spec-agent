import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Bot,
  ClipboardCheck,
  Download,
  FileText,
  FolderOpen,
  Loader2,
  MessageSquare,
  RefreshCw,
  Send,
  SlidersHorizontal,
  Upload,
  X,
} from "lucide-react";

// 0. API 설정
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? (import.meta.env.DEV ? "http://localhost:8000" : "");

// 1. 사용자 입력 기본값
const starterMessage = {
  role: "assistant",
  content:
    "특허 초안 관련 자료를 보내 주세요. 회의록, 아이디어 메모, 도면 설명, 상담 기록을 함께 주면 제가 부족한 항목을 먼저 확인하고 초안을 정리하겠습니다.",
};

// 1.1 상태 라벨
const statusLabel = {
  complete: "완료",
  missing: "부족",
  needs_review: "검토",
  blocked: "차단",
  pending: "대기",
  running: "진행",
  warning: "주의",
};

// 1.2 필수항목 초기 표시
const defaultChecklist = [
  "발명 명칭",
  "기술분야",
  "배경기술/종래 문제",
  "해결하려는 과제",
  "구성요소와 해결수단",
  "작동 방식/실시예",
  "효과와 근거",
  "도면 설명",
  "부호의 설명",
  "산업상 이용가능성",
].map((label, index) => ({
  key: `pending-${index}`,
  label,
  status: "pending",
  question: "자료 분석 후 자동으로 판정됩니다.",
}));

// 1.3 파일명/다운로드 처리
function fileName(path) {
  if (!path) return "";
  return path.split(/[\\/]/).pop();
}

function downloadUrl(path, sessionId) {
  const name = fileName(path);
  return name && sessionId
    ? `${API_BASE_URL}/api/files/${encodeURIComponent(sessionId)}/${encodeURIComponent(name)}`
    : "";
}

// 1.4 첨부파일 중복 처리
function attachmentKey(file) {
  return `${file.webkitRelativePath || file.name}:${file.size}:${file.lastModified}`;
}

function mergeFiles(current, incoming) {
  const map = new Map(current.map((file) => [attachmentKey(file), file]));
  for (const file of incoming) {
    map.set(attachmentKey(file), file);
  }
  return [...map.values()];
}

export default function App() {
  // 2. 입력 페이지 상태
  const [sessionId, setSessionId] = useState("");
  const [caseName, setCaseName] = useState("새 출원 준비 건");
  const [useRag, setUseRag] = useState(true);
  const [message, setMessage] = useState("");
  const [queuedFiles, setQueuedFiles] = useState([]);
  const [messages, setMessages] = useState([starterMessage]);
  const [result, setResult] = useState(null);
  const [activeTab, setActiveTab] = useState("draft");
  const [showDetails, setShowDetails] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const fileInputRef = useRef(null);
  const folderInputRef = useRef(null);
  const messagesEndRef = useRef(null);

  // 2.1 응답 데이터 표시 상태
  const checklist = useMemo(() => result?.checklist || [], [result]);
  const steps = result?.steps || [];
  const materials = result?.materials || [];
  const references = result?.references || [];
  const priorArtCandidates = result?.prior_art_candidates || [];
  const reviewItems = result?.review_items || [];
  const displayChecklist = checklist.length ? checklist : defaultChecklist;

  // 2.2 대화 스크롤 처리
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ block: "end" });
  }, [messages, loading]);

  // 2.3 체크리스트 요약
  const checklistSummary = useMemo(() => {
    const total = displayChecklist.length;
    const complete = displayChecklist.filter((item) => item.status === "complete").length;
    const missing = displayChecklist.filter((item) => item.status === "missing").length;
    const review = displayChecklist.filter((item) => item.status === "needs_review").length;
    return { total, complete, missing, review };
  }, [displayChecklist]);

  // 3. 사용자 파일 입력 처리
  const handlePickFiles = (event) => {
    setQueuedFiles((current) => mergeFiles(current, Array.from(event.target.files || [])));
    event.target.value = "";
  };

  const handleDropFiles = (event) => {
    event.preventDefault();
    const droppedFiles = Array.from(event.dataTransfer.files || []);
    if (droppedFiles.length) {
      setQueuedFiles((current) => mergeFiles(current, droppedFiles));
    }
  };

  const removeQueuedFile = (target) => {
    setQueuedFiles((current) => current.filter((file) => file !== target));
  };

  // 3.1 세션 초기화
  const resetSession = () => {
    setSessionId("");
    setCaseName("새 출원 준비 건");
    setMessage("");
    setQueuedFiles([]);
    setMessages([starterMessage]);
    setResult(null);
    setError("");
    setActiveTab("draft");
  };

  // 4. 사용자 입력 전송
  const sendTurn = async () => {
    if (!message.trim() && queuedFiles.length === 0) return;
    const userContent = message.trim() || `${queuedFiles.length}개 자료를 업로드했습니다.`;
    const filesToSend = queuedFiles;
    setMessages((current) => [...current, { role: "user", content: userContent }]);
    setMessage("");
    setQueuedFiles([]);
    setLoading(true);
    setError("");

    try {
      // 4.2 API 호출 데이터
      // POST /api/agent/message 로 message, session_id, case_name, use_rag, files를 보냅니다.
      const formData = new FormData();
      formData.append("message", userContent);
      formData.append("session_id", sessionId);
      formData.append("case_name", caseName);
      formData.append("use_rag", String(useRag));
      for (const file of filesToSend) {
        formData.append("files", file, file.webkitRelativePath || file.name);
      }

      // 4.3 백엔드 호출
      // 백엔드는 Guardrail -> 파일 추출 -> 토큰화/DB 저장 -> RAG -> OpenAI LLM -> Word/Markdown 저장 순서로 처리합니다.
      const response = await fetch(`${API_BASE_URL}/api/agent/message`, {
        method: "POST",
        body: formData,
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || "Agent 응답 생성에 실패했습니다.");
      }
      setSessionId(data.session_id);
      setCaseName(data.case_name || caseName);
      setResult(data);
      setActiveTab(data.blocked ? "review" : "draft");
      setMessages((current) => [...current, { role: "assistant", content: data.reply }]);
    } catch (err) {
      setError(err.message);
      setMessages((current) => [
        ...current,
        { role: "assistant", content: "요청 처리 중 오류가 발생했습니다. 백엔드 실행 상태와 API 설정을 확인해 주세요." },
      ]);
    } finally {
      setLoading(false);
    }
  };

  // 4.1 단축키 전송
  const handleKeyDown = (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      sendTurn();
    }
  };

  return (
    <div className="app-shell">
      {/* UI: 좌측 처리 흐름 */}
      <aside className="side-panel">
        <div className="brand-row">
          <div className="brand-mark">S</div>
          <div>
            <strong>SPEC Agent</strong>
            <span>출원명세서 초안 작성 보조 Agent</span>
          </div>
        </div>

        <div className="panel-section">
          <span className="panel-title">처리 흐름</span>
        </div>

        <div className="step-list">
          {steps.length ? steps.map((step) => (
            <div className={`step ${step.status}`} key={step.key}>
              <span>{statusLabel[step.status] || step.status}</span>
              <strong>{step.title}</strong>
              <p>{step.detail || step.tool}</p>
            </div>
          )) : (
            <div className="step pending">
              <span>대기</span>
              <strong>자료 수신 전</strong>
              <p>메시지 또는 파일을 보내면 분석이 시작됩니다.</p>
            </div>
          )}
        </div>

        <button className="icon-button ghost" type="button" onClick={resetSession}>
          <RefreshCw size={18} />
          <span>새 세션</span>
        </button>
      </aside>

      {/* UI: 채팅 입력페이지 */}
      <main className="workspace">
        <section className="chat-panel">
          <header className="workspace-head">
            <div>
              <label htmlFor="caseName">사건명</label>
              <input id="caseName" value={caseName} onChange={(event) => setCaseName(event.target.value)} />
            </div>
            <div className="detail-control">
              <button
                className="detail-button"
                type="button"
                title="참고자료 보강 같은 세부 옵션을 엽니다."
                onClick={() => setShowDetails((current) => !current)}
              >
                <SlidersHorizontal size={18} />
                <span>상세</span>
              </button>
              {showDetails ? (
                <div className="detail-menu">
                  <strong>참고자료 보강</strong>
                  <p>업로드 자료와 특허로 안내 자료에서 관련 문장을 찾아 초안 근거를 보강합니다.</p>
                  <div className="segmented">
                    <button className={useRag ? "active" : ""} type="button" onClick={() => setUseRag(true)}>
                      사용
                    </button>
                    <button className={!useRag ? "active" : ""} type="button" onClick={() => setUseRag(false)}>
                      끄기
                    </button>
                  </div>
                </div>
              ) : null}
            </div>
          </header>

          {error ? <div className="notice error">{error}</div> : null}

          <div className="message-list">
            {messages.map((item, index) => (
              <article className={`message ${item.role}`} key={`${item.role}-${index}`}>
                <div className="avatar">{item.role === "assistant" ? <Bot size={18} /> : <MessageSquare size={18} />}</div>
                <p>{item.content}</p>
              </article>
            ))}
            {loading ? (
              <article className="message assistant">
                <div className="avatar"><Loader2 className="spin" size={18} /></div>
                <p>자료를 읽고 필수항목을 점검하는 중입니다.</p>
              </article>
            ) : null}
            <div ref={messagesEndRef} />
          </div>

          <div className="composer" onDragOver={(event) => event.preventDefault()} onDrop={handleDropFiles}>
            <div className={`attachment-tray ${queuedFiles.length ? "has-files" : ""}`}>
              {queuedFiles.length ? (
                queuedFiles.map((file) => (
                  <button className="file-chip" type="button" key={attachmentKey(file)} onClick={() => removeQueuedFile(file)}>
                    <FileText size={15} />
                    <span>{file.webkitRelativePath || file.name}</span>
                    <X size={14} />
                  </button>
                ))
              ) : (
                <button className="drop-hint" type="button" onClick={() => fileInputRef.current?.click()}>
                  <Upload size={16} />
                  <span>파일을 여기에 끌어오거나 선택하세요.</span>
                </button>
              )}
            </div>
            <textarea
              value={message}
              placeholder="회의록, 아이디어, 도면 설명, 보완 답변을 입력하세요."
              onChange={(event) => setMessage(event.target.value)}
              onKeyDown={handleKeyDown}
            />
            <div className="composer-actions">
              <input ref={fileInputRef} type="file" multiple hidden onChange={handlePickFiles} />
              <input ref={folderInputRef} type="file" multiple hidden webkitdirectory="" onChange={handlePickFiles} />
              <button className="icon-button" type="button" onClick={() => fileInputRef.current?.click()}>
                <Upload size={18} />
                <span>파일</span>
              </button>
              <button className="icon-button" type="button" onClick={() => folderInputRef.current?.click()}>
                <FolderOpen size={18} />
                <span>폴더</span>
              </button>
              <button className="icon-button primary" type="button" onClick={sendTurn} disabled={loading}>
                {loading ? <Loader2 className="spin" size={18} /> : <Send size={18} />}
                <span>보내기</span>
              </button>
            </div>
          </div>
        </section>

        {/* UI: 오른쪽 결과/체크리스트 */}
        <section className="result-area">
          <div className="summary-panel">
            <div className="summary-metrics">
              <div className="summary-card">
                <ClipboardCheck size={18} />
                <strong>{checklistSummary.complete}/{checklistSummary.total}</strong>
                <span>필수항목</span>
              </div>
              <div className="summary-card warn">
                <AlertTriangle size={18} />
                <strong>{checklistSummary.missing + checklistSummary.review}</strong>
                <span>보완</span>
              </div>
            </div>
            {result?.docx_path || result?.markdown_path ? (
              <div className="quick-downloads">
                {result.docx_path ? (
                  <a className="icon-button primary" href={downloadUrl(result.docx_path, result.session_id)}>
                    <Download size={18} />
                    <span>Word 받기</span>
                  </a>
                ) : null}
                {result.markdown_path ? (
                  <a className="icon-button" href={downloadUrl(result.markdown_path, result.session_id)}>
                    <Download size={18} />
                    <span>Markdown</span>
                  </a>
                ) : null}
              </div>
            ) : null}
            <p className="checklist-note">
              완료는 자료에서 항목이 확인됐다는 뜻입니다. 특허성, 권리범위, 출원 가능성 검토 완료가 아닙니다.
            </p>
            <div className="inline-checklist">
              {displayChecklist.map((item) => (
                <div className={`mini-check ${item.status}`} key={item.key}>
                  <span>{statusLabel[item.status] || item.status}</span>
                  <strong>{item.label}</strong>
                  <p>{item.evidence || item.question || "확인 필요"}</p>
                </div>
              ))}
            </div>
          </div>

          <div className="tabs">
            <button className={activeTab === "draft" ? "active" : ""} type="button" onClick={() => setActiveTab("draft")}>
              초안
            </button>
            <button className={activeTab === "review" ? "active" : ""} type="button" onClick={() => setActiveTab("review")}>
              검토 {reviewItems.length ? `(${reviewItems.length})` : ""}
            </button>
            <button className={activeTab === "prior" ? "active" : ""} type="button" onClick={() => setActiveTab("prior")}>
              선행기술 {priorArtCandidates.length ? `(${priorArtCandidates.length})` : ""}
            </button>
            <button className={activeTab === "references" ? "active" : ""} type="button" onClick={() => setActiveTab("references")}>
              근거 자료
            </button>
          </div>

          {!result ? (
            <div className="empty-state">아직 분석 결과가 없습니다.</div>
          ) : (
            <>
              {result.blocked ? <div className="notice error">{result.blocked_reason}</div> : null}

              {activeTab === "draft" ? (
                <div className="draft-preview">
                  <pre>{result.markdown || "생성된 본문이 없습니다."}</pre>
                  <div className="download-row">
                    {result.markdown_path ? (
                      <a className="icon-button" href={downloadUrl(result.markdown_path, result.session_id)}>
                        <Download size={18} />
                        <span>Markdown</span>
                      </a>
                    ) : null}
                    {result.docx_path ? (
                      <a className="icon-button" href={downloadUrl(result.docx_path, result.session_id)}>
                        <Download size={18} />
                        <span>Word</span>
                      </a>
                    ) : null}
                  </div>
                </div>
              ) : null}

              {activeTab === "review" ? (
                <div className="list-stack">
                  <h2>검토 필요 항목</h2>
                  {reviewItems.length ? reviewItems.map((item, index) => (
                    <article className={`review-row ${item.severity}`} key={`${item.title}-${index}`}>
                      <strong>{item.title}</strong>
                      <p>{item.description}</p>
                      <span>{item.human_owner}</span>
                    </article>
                  )) : <p className="muted">없음</p>}
                </div>
              ) : null}

              {activeTab === "prior" ? (
                <div className="list-stack">
                  <h2>KIPRISPlus 선행기술 후보</h2>
                  <p className="section-note">
                    자동 검색된 국내 특허·실용 후보입니다. 원형 값은 검색어와 문헌 텍스트의 자동 유사도이며,
                    최종 신규성·진보성·등록 가능성 판단은 사람 검토가 필요합니다.
                  </p>
                  {priorArtCandidates.length ? priorArtCandidates.map((item, index) => (
                    <article className="prior-row" key={`${item.publication_number || item.application_number || item.title}-${index}`}>
                      <div className="score-ring" style={{ "--score": item.similarity_score }}>
                        <strong>{item.similarity_score}%</strong>
                        <span>{item.risk_level}</span>
                      </div>
                      <div>
                        <strong>{item.title || "제목 확인 필요"}</strong>
                        <span>
                          {[item.publication_number, item.application_number, item.registration_number].filter(Boolean).join(" · ") || "번호 확인 필요"}
                        </span>
                        <p>{item.abstract || item.note}</p>
                        {item.abstract && item.note ? <small>{item.note}</small> : null}
                        <small>일치 핵심어: {item.matched_terms?.length ? item.matched_terms.join(", ") : "없음"}</small>
                      </div>
                    </article>
                  )) : <p className="muted">KIPRIS 후보가 없습니다. API 설정 또는 검색어를 확인해 주세요.</p>}
                </div>
              ) : null}

              {activeTab === "references" ? (
                <div className="list-stack">
                  <h2>읽은 파일</h2>
                  <p className="section-note">이번 사건에 누적된 입력 자료입니다. 글자 수와 청크 수는 Agent가 어느 정도의 내용을 읽었는지 보여줍니다.</p>
                  {materials.length ? materials.map((item, index) => (
                    <article className={`material-row ${item.status}`} key={`${item.name}-${index}`}>
                      <strong>{item.name}</strong>
                      <span>{item.kind} · {item.char_count.toLocaleString()}자 · 분석 조각 {item.chunk_count}개</span>
                      {item.note ? <p>{item.note}</p> : null}
                    </article>
                  )) : <p className="muted">없음</p>}

                  <h2>초안 근거로 참고한 문장</h2>
                  <p className="section-note">
                    이 문장은 초안의 형식, 표현, 사용자 자료와의 연결을 확인하기 위한 근거입니다.
                    사용자 자료에 없는 발명 내용이나 실험 결과를 대신 채우는 용도로 쓰지 않습니다.
                  </p>
                  {references.length ? references.map((item, index) => (
                    <article className="reference-row" key={`${item.source}-${index}`}>
                      <strong>{item.title}</strong>
                      <span>{item.source}</span>
                      <p>{item.excerpt}</p>
                    </article>
                  )) : <p className="muted">없음</p>}
                </div>
              ) : null}

            </>
          )}
        </section>
      </main>
    </div>
  );
}
