import sys

import pytest

import benchmark_llm


@pytest.mark.parametrize(
    ("kind", "text", "expected"),
    [
        ("followup", '{"complete":true}', True),
        ("followup", '```json\n{"complete":true}\n```', True),
        ("followup", '{"complete":false,"question":"Seit wann?"}', True),
        ("followup", '{"complete":false,"question":""}', False),
        ("followup", '{"complete":"false","question":"Seit wann?"}', False),
        ("assessment", '{"summary":"Husten","triage_score":"Grün"}', True),
        ("assessment", '{"summary":"Husten","triage_score":"green"}', False),
        ("assessment", '{"summary":null,"triage_score":"Grün"}', False),
        ("assessment", "not json", False),
    ],
)
def test_schema_validation(kind, text, expected):
    assert benchmark_llm.valid(kind, text) is expected


def test_main_reports_each_case_and_failure(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["benchmark_llm.py", "--provider", "foundry_local",
                                    "--base-url", "http://localhost:5273", "--model", "test",
                                    "--repeats", "1"])
    monkeypatch.setattr(benchmark_llm, "generate", lambda *args, **kwargs: '{"complete":true}')
    assert benchmark_llm.main() == 1
    output = capsys.readouterr().out
    assert "case=followup" in output and "schema_valid=True" in output
    assert "case=assessment" in output and "schema_valid=False" in output
