import React, { useEffect, useRef } from "react";
import { t } from "../i18n";

const DATA = ["anamnesis", "triage", "medication", "booking", "letter"];

export default function ConsentDialog({ message, onDecision, epaUnavailable = false, mockEpa = false }) {
  const grantRef = useRef(null);
  const declineRef = useRef(null);

  useEffect(() => {
    (epaUnavailable ? declineRef : grantRef).current?.focus();
  }, [epaUnavailable]);

  function onKeyDown(e) {
    if (e.key === "Escape") onDecision(false);
  }

  return (
    <div className="overlay" onKeyDown={onKeyDown}>
      <div
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="consent-title"
        aria-describedby="consent-purpose"
      >
        <h2 id="consent-title">{t("consent.title")}</h2>
        {!epaUnavailable && !mockEpa && message && <p>{message}</p>}
        {epaUnavailable && <p role="status">{t("consent.referenceUnavailable")}</p>}
        {mockEpa && <p role="status">{t("consent.mockNotice")}</p>}
        <p id="consent-purpose">{t("consent.purpose")}</p>
        <p className="strong">{t("consent.dataHeading")}</p>
        <ul>
          {DATA.map((k) => (
            <li key={k}>{t(mockEpa && ["medication", "booking", "letter"].includes(k)
              ? `consent.data.mock${k[0].toUpperCase()}${k.slice(1)}`
              : `consent.data.${k}`)}</li>
          ))}
        </ul>
        <p>{t(mockEpa ? "consent.mockDuration" : "consent.duration")}</p>
        <p className="muted">{t("consent.revokeInfo")}</p>
        <div className="actions">
          <button ref={grantRef} className="btn btn-primary" disabled={epaUnavailable} onClick={() => onDecision(true)}>
            {t(mockEpa ? "consent.mockGrant" : "consent.grant")}
          </button>
          <button ref={declineRef} className="btn" onClick={() => onDecision(false)}>
            {t("consent.decline")}
          </button>
        </div>
      </div>
    </div>
  );
}
