import { useEffect, useState } from "react";
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

  async function analyze() {
    setBusy(true);
    setError("");
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

  return (
    <div className="page">
      <header>
        <h1>NLP-Driven Compiler Assistant</h1>
        <p>Paste C code. Get the problems explained, scored and improved.</p>
      </header>

      <div className="columns">
        <section className="panel">
          <h2>Your code</h2>
          <textarea
            value={code}
            spellCheck="false"
            onChange={(e) => setCode(e.target.value)}
          />

          <h2>Options</h2>
          <fieldset>
            <legend>Optimize for</legend>
            <div className="chips">
              {(options?.objectives ?? []).map((name) => (
                <label
                  key={name}
                  className={objectives.includes(name) ? "chip on" : "chip"}
                >
                  <input
                    type="checkbox"
                    checked={objectives.includes(name)}
                    onChange={() => toggleObjective(name)}
                  />
                  {name}
                </label>
              ))}
            </div>
          </fieldset>

          <label className="toggle">
            <input type="checkbox" checked={run} onChange={(e) => setRun(e.target.checked)} />
            <span>
              Run the program <em>finds crashes, timeouts, divide by zero</em>
            </span>
          </label>

          <label className="toggle">
            <input type="checkbox" checked={ai} onChange={(e) => setAi(e.target.checked)} />
            <span>
              AI code analysis <em>needs an API key; rules run either way</em>
            </span>
          </label>

          <button onClick={analyze} disabled={busy || !code.trim()}>
            {busy ? "Analyzing..." : "Analyze"}
          </button>

          {error && <p className="error">{error}</p>}
        </section>

        <section className="panel results">
          {!report && !error && <Empty />}
          {report && <Results report={report} />}
        </section>
      </div>
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
