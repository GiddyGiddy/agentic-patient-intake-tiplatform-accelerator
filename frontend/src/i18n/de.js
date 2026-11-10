const de = {
  "app.title": "Patientenaufnahme",
  "app.skip": "Zum Inhalt springen",
  "app.demoNote":
    "Demo-System für die Entwicklung. Keine echten Gesundheitsdaten eingeben.",

  "auth.heading": "Willkommen",
  "auth.intro":
    "Melden Sie sich mit Ihrer Gesundheits-ID an, um die Patientenaufnahme zu starten.",
  "auth.login": "Mit Gesundheits-ID anmelden",
  "auth.processing": "Anmeldung wird abgeschlossen …",
  "auth.failed": "Die Anmeldung ist fehlgeschlagen. Bitte versuchen Sie es erneut.",
  "auth.expired": "Ihre Sitzung ist abgelaufen. Bitte melden Sie sich erneut an.",
  "auth.loggedInAs": "Angemeldet als {sub}",
  "auth.logout": "Abmelden",

  "chat.log": "Gesprächsverlauf",
  "chat.intro":
    "Beschreiben Sie Ihre Beschwerden. Der Assistent stellt Ihnen bei Bedarf Rückfragen.",
  "chat.inputLabel": "Ihre Nachricht",
  "chat.send": "Senden",
  "chat.sending": "Assistent antwortet …",
  "chat.you": "Sie",
  "chat.agent": "Assistent",
  "chat.system": "Hinweis",
  "chat.restart": "Neues Gespräch",
  "chat.retry": "Erneut versuchen",
  "chat.error.network":
    "Der Server ist nicht erreichbar. Bitte prüfen Sie Ihre Verbindung.",
  "chat.error.server": "Es ist ein Fehler aufgetreten (Code {status}).",

  "emergency.title": "Mögliche Notfallsituation",
  "emergency.text":
    "Rufen Sie sofort den Notruf 112 an. Warten Sie nicht auf einen Termin.",
  "emergency.call": "112 anrufen",

  "consent.title": "Zugriff auf Ihre Daten freigeben",
  "consent.purpose":
    "Zweck: Terminvergabe in der Praxis und Information Ihrer behandelnden Ärztin oder Ihres Arztes.",
  "consent.dataHeading": "Folgende Daten werden verwendet:",
  "consent.data.anamnesis": "Zusammenfassung Ihrer Angaben (Anamnese)",
  "consent.data.triage": "Automatische Dringlichkeitseinschätzung (nicht ärztlich geprüft)",
  "consent.data.medication": "Medikationsplan aus der elektronischen Patientenakte (nur lesend)",
  "consent.data.booking": "Terminbuchung im Praxis- bzw. Krankenhausinformationssystem",
  "consent.data.letter": "Arztbrief per KIM an Ihre behandelnde Ärztin oder Ihren Arzt",
  "consent.duration":
    "Dauer: Die Freigabe gilt nur für diesen Vorgang und endet mit dessen Abschluss oder mit Ihrem Widerruf.",
  "consent.revokeInfo":
    "Sie können die Freigabe jederzeit widerrufen. Bereits übermittelte Daten verbleiben bei der Praxis.",
  "consent.grant": "Mit Gesundheits-ID freigeben",
  "consent.decline": "Nicht freigeben",
  "consent.declined.title": "Keine Freigabe erteilt",
  "consent.declined.text":
    "Ohne Freigabe können wir keinen Termin buchen und keinen Arztbrief versenden. Es wurden keine Daten übermittelt.",
  "consent.declined.reconsider": "Freigabe erneut prüfen",
  "consent.status.granted": "Datenfreigabe erteilt",
  "consent.revoke": "Freigabe widerrufen",
  "consent.revoked":
    "Die Freigabe wurde widerrufen. Es werden keine weiteren Daten verwendet.",

  "result.title": "Ihre Aufnahme ist abgeschlossen",
  "result.triage": "Dringlichkeit",
  "result.appointment": "Termin",
  "result.appointmentId": "Termin-ID",
  "result.anamnesis": "Zusammenfassung Ihrer Angaben (Anamnese)",
  "result.noAnamnesis": "Keine Zusammenfassung verfügbar.",
  "result.kim": "Arztbrief wurde per KIM an Ihre behandelnde Ärztin oder Ihren Arzt gesendet.",
  "result.noTime": "Zeit wird von der Praxis bestätigt",
  "result.medication": "Ihr Medikationsplan aus der ePA",
  "result.kimFailed": "Der Arztbrief konnte noch nicht per KIM versendet werden. Die Praxis wird informiert.",

  "triage.Rot": "Sofort",
  "triage.Orange": "Sehr dringend",
  "triage.Gelb": "Dringend",
  "triage.Grün": "Normal",
  "triage.Blau": "Nicht dringend",
};

export default de;
