import type { SignOnQuestion } from "../api";

export type Answers = Record<string, string | boolean | undefined>;

export function toAnswerList(answers: Answers): { question_id: string; value: string | boolean }[] {
  return Object.entries(answers)
    .filter(([, value]) => value !== undefined && value !== "")
    .map(([question_id, value]) => ({ question_id, value: value as string | boolean }));
}

/** The questions a site asks at sign-in. Large touch targets: this is used at a gate, on a phone. */
export function SignOnQuestions({ questions, answers, onChange }: { questions: SignOnQuestion[]; answers: Answers; onChange: (a: Answers) => void }) {
  if (questions.length === 0) return null;
  return (
    <div className="stack">
      {questions.map((q) => (
        <div key={q.id} className="stack" style={{ gap: 6 }}>
          <span id={`q-${q.id}`} style={{ fontWeight: 600 }}>
            {q.label}
            {q.required ? "" : <span className="muted small"> (optional)</span>}
          </span>
          {q.type === "yesno" ? (
            <div role="group" aria-labelledby={`q-${q.id}`} style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, maxWidth: 320 }}>
              {[{ label: "Yes", value: true }, { label: "No", value: false }].map((option) => (
                <button key={option.label} type="button" className={`btn${answers[q.id] === option.value ? " primary" : ""}`}
                  aria-pressed={answers[q.id] === option.value} onClick={() => onChange({ ...answers, [q.id]: option.value })}>
                  {option.label}
                </button>
              ))}
            </div>
          ) : q.type === "choice" ? (
            <select aria-labelledby={`q-${q.id}`} className="plain-input" value={(answers[q.id] as string) ?? ""}
              onChange={(e) => onChange({ ...answers, [q.id]: e.target.value || undefined })}>
              <option value="">Choose…</option>
              {(q.choices ?? []).map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          ) : (
            <input aria-labelledby={`q-${q.id}`} className="plain-input" maxLength={500} value={(answers[q.id] as string) ?? ""}
              onChange={(e) => onChange({ ...answers, [q.id]: e.target.value })} />
          )}
        </div>
      ))}
    </div>
  );
}
