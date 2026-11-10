export const config = {
  apiUrl: process.env.REACT_APP_API_URL || "http://localhost:8000",
  issuer: process.env.REACT_APP_OIDC_ISSUER || "http://localhost:8081",
  clientId: process.env.REACT_APP_OIDC_CLIENT_ID || "patient-intake-frontend",
  referenceLogin: process.env.REACT_APP_REFERENCE_LOGIN === "true",
};

export const redirectUri = () => `${window.location.origin}/`;
