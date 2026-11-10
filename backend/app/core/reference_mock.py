"""Explicit local-only mock workflow; this is not ePA authorization."""

from config import settings


def reference_mock_epa_available() -> bool:
    return (
        settings.reference_mock_epa_enabled
        and settings.reference_bff_enabled
        and settings.reference_rp_enabled
        and settings.reference_frontend_origin == "http://127.0.0.1:3000"
        and settings.epa_base_url == "http://mock-ti-gateway:8082/fhir"
        and settings.fhir_base_url == "http://mock-ti-gateway:8082/fhir"
        and settings.kim_smtp_host == "mock-kim-cm"
        and settings.kim_smtp_port == 2525
    )
