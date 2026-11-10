import React from "react";
import { t } from "../i18n";

function formatTime(iso) {
  const d = new Date(iso);
  if (!iso || Number.isNaN(d.getTime())) return t("result.noTime");
  return d.toLocaleString("de-DE", { dateStyle: "full", timeStyle: "short" });
}

export default function ResultCard({ result, onRestart }) {
  const score = result.triage_score;
  return (
    <section className="card result" aria-labelledby="result-title">
      <h2 id="result-title">{t("result.title")}</h2>
      <dl>
        <dt>{t("result.triage")}</dt>
        <dd>
          <span className={`badge badge-${score}`}>
            {score} – {t(`triage.${score}`)}
          </span>
        </dd>
        <dt>{t("result.appointment")}</dt>
        <dd>{formatTime(result.slot_start)}</dd>
        {result.appointment_id && (
          <>
            <dt>{t("result.appointmentId")}</dt>
            <dd>{result.appointment_id}</dd>
          </>
        )}
      </dl>
      <h3>{t("result.anamnesis")}</h3>
      <p className="anamnesis-summary">{result.anamnesis_summary?.trim() || t("result.noAnamnesis")}</p>
      {result.medication_plan?.length > 0 && (
        <>
          <h3>{t("result.medication")}</h3>
          <ul>
            {result.medication_plan.map((m) => (
              <li key={m}>{m}</li>
            ))}
          </ul>
        </>
      )}
      {result.kim_sent ? <p>{t("result.kim")}</p> : <p role="alert">{t("result.kimFailed")}</p>}
      <button className="btn" onClick={onRestart}>
        {t("chat.restart")}
      </button>
    </section>
  );
}
