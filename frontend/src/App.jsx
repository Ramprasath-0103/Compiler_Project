import { useEffect, useRef, useState } from "react";
import { SAMPLE } from "./sample.js";

const SEVERITY_ORDER = { fatal: 0, error: 1, warning: 2, note: 3 };

export default function App() {
  const [code, setCode] = useState(SAMPLE);
  const [options, setOptions] = useState(null);
  const [objectives, setObjectives] = useState([]);
  const [run, setRun] = useState(false);
  const [ai, setAi] = useState(false);
  const [report, setReport] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [request, setRequest] = useState("");
  const [intent, setIntent] = useState(null);
  const [generated, setGenerated] = useState(null);
  const [pipeline, setPipeline] = useState(null);
  const [improvement, setImprovement] = useState(null);
  const [tab, setTab] = useState("report");
  const [busyWith, setBusyWith] = useState("");
  const editor = useRef(null);

  // The controls are built from what the server says it supports, so the UI
  // cannot offer an objective the analyzer does not know about.
  useEffect(() => {
    fetch("/api/options")
      .then((r) => r.json())
      .then((data) => {
        setOptions(data);
        setObjectives(data.objectives);
      })
      .catch(() => setError("Cannot reach the analyzer. Is `nlpcc serve` running?"));
  }, []);

  function toggleObjective(name) {
    setObjectives((current) =>
      current.includes(name)
        ? current.filter((o) => o !== name)
        : [...current, name]
    );
  }

  function reset() {
    setError("");
    setIntent(null);
    setGenerated(null);
  }

  // Clicking a finding puts the cursor on the line it is about. A textarea
  // has no line API, so the offsets are counted from the text itself.
  function jumpToLine(line) {
    const area = editor.current;
    if (!area || !line) return;
    const lines = code.split("\n");
    if (line > lines.length) return;
    const start = lines.slice(0, line - 1).reduce((n, l) => n + l.length + 1, 0);
    area.focus();
    area.setSelectionRange(start, start + lines[line - 1].length);
    // Put the line roughly in the middle rather than at the very bottom.
    const lineHeight = area.scrollHeight / Math.max(lines.length, 1);
    area.scrollTop = Math.max(0, (line - 1) * lineHeight - area.clientHeight / 2);
  }

  async function post(route, body, label) {
    setBusy(true);
    setBusyWith(label);
    setError("");
    try {
      const response = await fetch(route, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await response.json();
      if (!response.ok) {
        setError(data.error || "That did not work.");
        return null;
      }
      return data;
    } catch {
      setError("Cannot reach the analyzer. Is the server running?");
      return null;
    } finally {
      setBusy(false);
      setBusyWith("");
    }
  }

  async function inspectPipeline() {
    const data = await post("/api/inspect", { code }, "pipeline");
    if (data) {
      setPipeline(data);
      setTab("pipeline");
    }
  }

  async function improveCode() {
    const data = await post("/api/improve", { code, objectives }, "improve");
    if (data) {
      setImprovement(data);
      setTab("improved");
    }
  }

  // Plain English in, the right stage of the pipeline out. The intent is
  // worked out by keyword rules on the server, so this needs no model.
  async function ask() {
    setBusy(true);
    reset();
    try {
      const response = await fetch("/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: request, code, ai }),
      });
      const data = await response.json();
      if (!response.ok) {
        setError(data.error || "That did not work.");
        return;
      }
      setIntent(data.intent);
      setTab("report");
      if (data.generated) {
        // Deliberately not copied into the editor: showing the same program
        // twice, side by side, reads as two different results.
        setGenerated(data.generated);
        setReport(null);
      } else if (data.report) {
        setReport(data.report);
      } else if (data.error) {
        setError(data.error);
      }
    } catch {
      setError("Cannot reach the analyzer. Is `nlpcc serve` running?");
    } finally {
      setBusy(false);
    }
  }

  async function analyze() {
    setBusy(true);
    setBusyWith("analyze");
    reset();
    setGenerated(null);
    setTab("report");
    try {
      const response = await fetch("/api/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code, objectives, run, ai }),
      });
      const data = await response.json();
      if (!response.ok) {
        setError(data.error || "Analysis failed.");
        setReport(null);
      } else {
        setReport(data);
      }
    } catch {
      setError("Cannot reach the analyzer. Is `nlpcc serve` running?");
      setReport(null);
    } finally {
      setBusy(false);
      setBusyWith("");
    }
  }

  function onAskKey(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (request.trim() && !busy) ask();
    }
  }

  return (
    <div className="page">
      <header className="masthead">
        <h1>NLP-Driven Compiler Assistant</h1>
        <p>Paste C code. Get the problems explained, scored and improved.</p>
      </header>

      <div className="columns">
        <section className="panel">
          <h2>Ask in plain English</h2>
          <div className="askrow">
            <input
              type="text"
              value={request}
              placeholder="why does this not compile?"
              onChange={(e) => setRequest(e.target.value)}
              onKeyDown={onAskKey}
            />
            <button
              className="secondary"
              onClick={ask}
              disabled={busy || !request.trim()}
            >
              {busy ? "Working..." : "Ask"}
            </button>
          </div>
          <div className="examples">
            {[
              "why does this not compile?",
              "make it faster",
              "how good is my code?",
              "write a program that reverses a string",
            ].map((example) => (
              <button
                key={example}
                className="example"
                onClick={() => setRequest(example)}
              >
                {example}
              </button>
            ))}
          </div>

          <h2>Your code</h2>
          <textarea
            ref={editor}
            value={code}
            spellCheck="false"
            onChange={(e) => setCode(e.target.value)}
          />
          <p className="hint">
            {code.split("\n").length} lines · click any finding to jump to its line
          </p>

          <h2>Options</h2>
          <MultiSelect
            label="Optimize for"
            available={options?.objectives ?? []}
            selected={objectives}
            onToggle={toggleObjective}
            onAll={() => setObjectives(options?.objectives ?? [])}
            onNone={() => setObjectives([])}
          />

          <label className="toggle">
            <input type="checkbox" checked={run} onChange={(e) => setRun(e.target.checked)} />
            <span>
              Run the program <em>finds crashes, timeouts, divide by zero</em>
            </span>
          </label>

          <label className="toggle">
            <input type="checkbox" checked={ai} onChange={(e) => setAi(e.target.checked)} />
            <span>
              AI code analysis
              <em>
                {options?.model?.available
                  ? options.model.describe
                  : "no model found — rules run either way"}
              </em>
            </span>
          </label>

          <button onClick={analyze} disabled={busy || !code.trim()}>
            {busy && busyWith === "analyze" ? "Analyzing..." : "Analyze"}
          </button>

          <div className="button-row">
            <button
              className="ghost"
              onClick={improveCode}
              disabled={busy || !code.trim()}
            >
              {busy && busyWith === "improve" ? "Improving..." : "Improve it"}
            </button>
            <button
              className="ghost"
              onClick={inspectPipeline}
              disabled={busy || !code.trim()}
            >
              {busy && busyWith === "pipeline" ? "Reading..." : "Show the phases"}
            </button>
          </div>

          {busy && (
            <p className="muted">
              Working{busyWith === "improve" || busyWith === "generate"
                ? " — a model call can take up to a minute"
                : ""}.
            </p>
          )}
          {error && <p className="error">{error}</p>}
        </section>

        <section className="panel results">
          <nav className="tabs">
            {[
              ["report", "Report", report || generated || intent],
              ["pipeline", "Compiler phases", pipeline],
              ["improved", "Improved version", improvement],
            ].map(([key, title, enabled]) => (
              <button
                key={key}
                className={tab === key ? "tab on" : "tab"}
                disabled={!enabled}
                onClick={() => setTab(key)}
              >
                {title}
              </button>
            ))}
          </nav>

          {tab === "report" && (
            <>
              {intent && <IntentBadge intent={intent} />}
              {generated && (
                <Generated
                  generated={generated}
                  onUse={(c) => {
                    setCode(c);
                    setGenerated(null);
                  }}
                />
              )}
              {!report && !generated && !error && !intent && <Empty />}
              {report && <Results report={report} onJump={jumpToLine} />}
            </>
          )}

          {tab === "pipeline" &&
            (pipeline ? <Pipeline data={pipeline} onJump={jumpToLine} /> : <Empty />)}

          {tab === "improved" &&
            (improvement ? (
              <Improved
                improvement={improvement}
                onUse={(c) => {
                  setCode(c);
                  setTab("report");
                }}
              />
            ) : (
              <Empty />
            ))}
        </section>
      </div>
    </div>
  );
}

function MultiSelect({ label, available, selected, onToggle, onAll, onNone }) {
  const [open, setOpen] = useState(false);
  const box = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    function onClickAway(event) {
      if (box.current && !box.current.contains(event.target)) setOpen(false);
    }
    function onEscape(event) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onClickAway);
    document.addEventListener("keydown", onEscape);
    return () => {
      document.removeEventListener("mousedown", onClickAway);
      document.removeEventListener("keydown", onEscape);
    };
  }, [open]);

  const summary =
    selected.length === 0
      ? "nothing selected"
      : selected.length === available.length
      ? "everything"
      : selected.join(", ");

  return (
    <div className="field" ref={box}>
      <span className="field-label">{label}</span>
      <button
        type="button"
        className="select"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <span className={selected.length ? "" : "placeholder"}>{summary}</span>
        <span className="caret">{open ? "\u25B2" : "\u25BC"}</span>
      </button>

      {open && (
        <div className="menu">
          {available.map((name) => (
            <label key={name} className="menu-row">
              <input
                type="checkbox"
                checked={selected.includes(name)}
                onChange={() => onToggle(name)}
              />
              <span>{name}</span>
            </label>
          ))}
          <div className="menu-foot">
            <button type="button" onClick={onAll}>Select all</button>
            <button type="button" onClick={onNone}>Clear</button>
          </div>
        </div>
      )}
    </div>
  );
}

function IntentBadge({ intent }) {
  const weak = intent.confidence < 0.5;
  return (
    <div className={weak ? "intent weak" : "intent"}>
      <div>
        <strong>Understood as:</strong> {intent.summary}
        {intent.objectives.length > 0 && (
          <> — for {intent.objectives.join(", ")}</>
        )}
      </div>
      <div className="muted">
        {Math.round(intent.confidence * 100)}% confident
        {intent.because.length > 0 && <> · matched {intent.because.join(", ")}</>}
      </div>
      {weak && (
        <div className="muted">
          That was a guess — rephrase if it is not what you wanted.
        </div>
      )}
    </div>
  );
}

function Generated({ generated, onUse }) {
  return (
    <div className="block">
      <h3>
        {generated.accepted ? "Generated and verified" : "Could not generate"}
      </h3>
      {generated.accepted ? (
        <p className="muted">
          It compiles cleanly after {generated.attempts} attempt
          {generated.attempts === 1 ? "" : "s"}
          {generated.provider ? ` · ${generated.provider}` : ""}.
        </p>
      ) : (
        <p className="note">{generated.failure_reason}</p>
      )}
      {generated.code && (
        <>
          <pre className="generated-code">{generated.code}</pre>
          <button className="secondary" onClick={() => onUse(generated.code)}>
            Put it in the editor
          </button>
        </>
      )}
      {generated.notes && <p className="muted">{generated.notes}</p>}
    </div>
  );
}

function Measurements({ m }) {
  const rows = [
    ["Build", `${m.build_ms} ms`],
    ["Wall time", `${m.wall_ms} ms`],
    ["CPU time", `${m.cpu_ms} ms`],
    ["Peak memory", `${(m.peak_memory_kb / 1024).toFixed(1)} MB`],
    ["Output", `${m.output_bytes} bytes`],
    ["Exit status", m.timed_out ? "timed out" : String(m.exit_code)],
  ];
  return (
    <div className="block">
      <h3>Measurements</h3>
      <div className="metrics">
        {rows.map(([label, value]) => (
          <div key={label} className="metric">
            <span className="metric-value">{value}</span>
            <span className="metric-label">{label}</span>
          </div>
        ))}
      </div>
      {m.sanitized && (
        <p className="muted">
          Measured with runtime checks on (-fsanitize=undefined), which
          instruments every operation — an uninstrumented build is faster.
        </p>
      )}
    </div>
  );
}

function Pipeline({ data, onJump }) {
  return (
    <>
      <p className="muted">
        What our own front end did to <code>{data.source}</code>, phase by
        phase. These are the real tokens, the real parse tree and the real
        symbol table the analysis ran on.
      </p>

      <div className="phaseflow">
        {data.phases.map((phase, i) => (
          <div key={phase.name} className="phasestep">
            <div className={phase.ok ? "phasechip ok" : "phasechip bad"}>
              {phase.title}
            </div>
            {i < data.phases.length - 1 && <span className="arrow">→</span>}
          </div>
        ))}
      </div>

      {data.phases.map((phase) => (
        <div key={phase.name} className="block">
          <h3>
            {phase.title}
            <span className="grade">{phase.summary}</span>
          </h3>

          {phase.diagnostics.length > 0 && (
            <div className="phasediags">
              {phase.diagnostics.map((d, i) => (
                <button
                  key={i}
                  className="phasediag"
                  onClick={() => d.line && onJump && onJump(d.line)}
                >
                  <span className={`tag ${d.severity}`}>{d.severity}</span>
                  {d.line ? `line ${d.line}: ` : ""}
                  {d.title}
                </button>
              ))}
            </div>
          )}

          {phase.name === "lexical" && <Tokens detail={phase.detail} onJump={onJump} />}
          {phase.name === "syntax" && <Tree node={phase.detail.tree} onJump={onJump} />}
          {phase.name === "semantic" && <Symbols rows={phase.detail.symbols} onJump={onJump} />}
        </div>
      ))}
    </>
  );
}

function Tokens({ detail, onJump }) {
  const [all, setAll] = useState(false);
  const shown = all ? detail.tokens : detail.tokens.slice(0, 60);
  return (
    <>
      <div className="chips">
        {detail.counts.map((c) => (
          <span key={c.kind} className="chip static">
            {c.kind.toLowerCase()} <b>{c.count}</b>
          </span>
        ))}
      </div>
      <div className="tokens">
        {shown.map((t, i) => (
          <button
            key={i}
            className={`token k-${t.kind.toLowerCase()}`}
            title={`${t.kind.toLowerCase()} · line ${t.line}`}
            onClick={() => onJump && onJump(t.line)}
          >
            {t.text}
          </button>
        ))}
      </div>
      {detail.tokens.length > 60 && (
        <button className="ghost" onClick={() => setAll((v) => !v)}>
          {all ? "Show fewer" : `Show all ${detail.total} tokens`}
        </button>
      )}
    </>
  );
}

function Tree({ node, onJump, depth = 0 }) {
  const [open, setOpen] = useState(depth < 3);
  if (!node || !node.kind) return <p className="muted">No tree was built.</p>;
  const children = node.children || [];

  return (
    <div className="treenode" style={{ marginLeft: depth ? 16 : 0 }}>
      <div className="treerow">
        {children.length > 0 ? (
          <button className="twisty" onClick={() => setOpen((v) => !v)}>
            {open ? "▾" : "▸"}
          </button>
        ) : (
          <span className="twisty leaf">·</span>
        )}
        <button
          className="treelabel"
          onClick={() => node.line && onJump && onJump(node.line)}
        >
          <span className="treekind">{node.kind}</span>
          {node.label && <span className="treename">{node.label}</span>}
          {node.line > 0 && <span className="treeline">line {node.line}</span>}
        </button>
      </div>
      {open &&
        children.map((child, i) => (
          <Tree key={i} node={child} onJump={onJump} depth={depth + 1} />
        ))}
      {node.truncated && <div className="muted">… trimmed</div>}
    </div>
  );
}

function Symbols({ rows, onJump }) {
  if (!rows || rows.length === 0) return <p className="muted">No declarations.</p>;
  return (
    <table className="symbols">
      <thead>
        <tr>
          <th>Name</th>
          <th>Kind</th>
          <th>Type</th>
          <th>Line</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((s, i) => (
          <tr key={i} onClick={() => s.line && onJump && onJump(s.line)}>
            <td><code>{s.name}</code></td>
            <td>{s.kind}{s.parameters != null ? ` (${s.parameters} params)` : ""}</td>
            <td><code>{s.type || "—"}</code></td>
            <td>{s.line || "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Improved({ improvement, onUse }) {
  const before = improvement.before || {};
  const after = improvement.after || {};
  return (
    <>
      <div className={improvement.accepted ? "summary ok" : "summary bad"}>
        <strong>{improvement.accepted ? "Improved" : "Not accepted"}</strong>
        <span>{improvement.reason}</span>
      </div>

      {before.overall != null && after.overall != null && (
        <div className="block">
          <h3>
            Score {before.overall} <span className="arrow">→</span>{" "}
            <span className="big">{after.overall}</span>
            <span className="grade">{after.grade}</span>
          </h3>
          <div className="metrics">
            {Object.keys(after.dimensions || {}).map((name) => {
              const was = before.dimensions?.[name] ?? 0;
              const now = after.dimensions[name];
              const delta = now - was;
              return (
                <div key={name} className="metric">
                  <span className="metric-value">
                    {now}
                    {delta !== 0 && (
                      <small className={delta > 0 ? "up" : "down"}>
                        {delta > 0 ? ` +${delta}` : ` ${delta}`}
                      </small>
                    )}
                  </span>
                  <span className="metric-label">{name}</span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {improvement.diff_lines?.length > 0 && (
        <div className="block">
          <h3>What changed</h3>
          <pre className="diff">
            {improvement.diff_lines.map((row, i) => (
              <span key={i} className={`d-${row.kind}`}>
                {row.text}
                {"\n"}
              </span>
            ))}
          </pre>
        </div>
      )}

      {improvement.accepted && improvement.improved && (
        <button className="secondary" onClick={() => onUse(improvement.improved)}>
          Put it in the editor
        </button>
      )}
    </>
  );
}

function Empty() {
  return (
    <div className="empty">
      <p>Nothing analyzed yet.</p>
      <p className="muted">
        Press Analyze. The sample has a missing semicolon — fix it and analyze
        again to see the quality score and suggestions, which need a program
        that compiles.
      </p>
    </div>
  );
}

function Results({ report, onJump }) {
  const findings = [...report.diagnostics].sort(
    (a, b) =>
      (SEVERITY_ORDER[a.severity] ?? 9) - (SEVERITY_ORDER[b.severity] ?? 9) ||
      (a.position?.line ?? 0) - (b.position?.line ?? 0)
  );

  return (
    <>
      <Summary report={report} />
      {report.score ? (
        <Score score={report.score} />
      ) : (
        <p className="note">
          No quality score: the program has to compile before it can be
          measured. Fix the errors below and analyze again.
        </p>
      )}
      <Findings findings={findings} onJump={onJump} />
      <Recommendations
        recommendations={report.recommendations}
        objectives={report.objectives}
      />
      {report.measurements && <Measurements m={report.measurements} />}
      {report.program_output && (
        <div className="block">
          <h3>Program output</h3>
          <pre>{report.program_output}</pre>
        </div>
      )}
    </>
  );
}

function Summary({ report }) {
  const { errors, warnings } = report.summary;
  return (
    <div className={report.compiles ? "summary ok" : "summary bad"}>
      <strong>
        {report.compiles ? "Compiles" : "Does not compile"}
      </strong>
      <span>
        {errors} error{errors === 1 ? "" : "s"}, {warnings} warning
        {warnings === 1 ? "" : "s"}
      </span>
    </div>
  );
}

function Score({ score }) {
  return (
    <div className="block">
      <h3>
        Quality score <span className="big">{score.overall}</span>
        <span className="grade">{score.grade}</span>
      </h3>

      {score.capped_by_security && (
        <p className="note">
          Capped: a critical security issue limits the score no matter how good
          the other dimensions are.
        </p>
      )}
      {!score.capped_by_security && score.limited_by && (
        <p className="note">
          Held down by its weakest dimension ({score.limited_by}).
        </p>
      )}

      <table>
        <tbody>
          {Object.entries(score.dimensions).map(([name, d]) => (
            <tr key={name}>
              <td className="dim">{name}</td>
              <td className="barcell">
                <div className="bar">
                  <div className="fill" style={{ width: `${d.score}%` }} />
                </div>
              </td>
              <td className="num">{d.score}</td>
              <td className="weight">{Math.round(d.weight * 100)}%</td>
              <td className="why">
                {d.deductions.length === 0
                  ? "—"
                  : d.deductions
                      .map((x) => `${x.code} (−${x.penalty})`)
                      .join(", ")}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted">
        Every dimension starts at 100. Each deduction names the rule that made
        it.
      </p>
    </div>
  );
}

function Findings({ findings, onJump }) {
  return (
    <div className="block">
      <h3>Problems ({findings.length})</h3>
      {findings.length === 0 && <p className="muted">None. The program is clean.</p>}
      {findings.map((d, i) => (
        <article
          key={i}
          className={`finding ${d.severity}${d.position ? " clickable" : ""}`}
          onClick={() => d.position && onJump && onJump(d.position.line)}
          title={d.position ? "Go to this line" : undefined}
        >
          <header>
            <span className={`tag ${d.severity}`}>{d.severity}</span>
            <strong>{d.meta?.title || d.message}</strong>
            <span className="where">
              {d.position ? `line ${d.position.line}` : "whole file"} · {d.phase}
            </span>
          </header>

          {d.snippet && <pre className="snippet">{d.snippet}</pre>}

          {(d.meta?.related ?? []).map((rel, j) => (
            <p key={j} className="related">
              Also line {rel.line}: {rel.message}
            </p>
          ))}

          {d.explanation ? (
            <>
              <p>
                <b>What it means.</b> {d.explanation}
              </p>
              <p>
                <b>How to fix it.</b> {d.fix}
              </p>
            </>
          ) : (
            <p className="muted">The compiler said: {d.message}</p>
          )}

          <footer>
            <code>{d.code}</code>
            <span>found by {d.origin}</span>
            {d.meta?.generated && <span className="generated">AI generated</span>}
          </footer>
        </article>
      ))}
    </div>
  );
}

function Recommendations({ recommendations, objectives }) {
  return (
    <div className="block">
      <h3>Suggestions ({recommendations.length})</h3>
      <p className="muted">Selected for: {objectives.join(", ")}</p>
      {recommendations.length === 0 && (
        <p className="muted">Nothing to suggest for those objectives.</p>
      )}
      {recommendations.map((r, i) => (
        <article key={i} className="rec">
          <header>
            <strong>{r.title}</strong>
            <span className="where">
              {r.line ? `line ${r.line}` : "whole file"}
            </span>
          </header>
          <p className="meta">
            {r.impact} impact · {r.effort} effort · serves{" "}
            {r.objectives.join(", ")}
          </p>
          <p>{r.rationale}</p>
          <p>
            <b>Do this.</b> {r.action}
          </p>
        </article>
      ))}
    </div>
  );
}
