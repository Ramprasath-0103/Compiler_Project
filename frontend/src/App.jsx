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
    reset();
    setGenerated(null);
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
            value={code}
            spellCheck="false"
            onChange={(e) => setCode(e.target.value)}
          />

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
            {busy ? "Analyzing..." : "Analyze"}
          </button>

          {busy && (
            <p className="muted">
              Working. Generating code on a local model can take a minute.
            </p>
          )}
          {error && <p className="error">{error}</p>}
        </section>

        <section className="panel results">
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
          {report && <Results report={report} />}
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

function Results({ report }) {
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
      <Findings findings={findings} />
      <Recommendations
        recommendations={report.recommendations}
        objectives={report.objectives}
      />
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

function Findings({ findings }) {
  return (
    <div className="block">
      <h3>Problems ({findings.length})</h3>
      {findings.length === 0 && <p className="muted">None. The program is clean.</p>}
      {findings.map((d, i) => (
        <article key={i} className={`finding ${d.severity}`}>
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
