"""SP-882 the AI 問答's tokens and USD for the NAS's shared「💸 API 使用量」Grafana (backend/ai/spend.py).

驗收: every model call's tokens and USD reach InfluxDB `feeder` / `api_call` with project
trailruncoach — the app appends a line-protocol line to <data folder>/logs/ops/api_calls.lp, the
Telegraf of deploy/ops-monitoring tails it (the app holds no InfluxDB token, SP-388). A failed
write never fails the answer. No real model call (conftest._no_live_ai), no InfluxDB."""
from __future__ import annotations

import re
from pathlib import Path

from backend.ai import qa as Q
from backend.ai import spend as S
from backend.tests.test_ai_qa_sp785 import FAKE_KEY, _key, _model, owner  # noqa: F401 — fixtures

LINE = re.compile(
    r'^api_call,provider=anthropic,model=(?P<model>[^,]+),project=trailruncoach,purpose=(?P<purpose>\w+),'
    r'status=(?P<status>success|error) input_tokens=(?P<i>\d+)i,output_tokens=(?P<o>\d+)i,'
    r'cost_usd=(?P<cost>\d+\.\d{6}),latency_ms=(?P<ms>\d+)i,error_message="(?P<err>[^"]*)" (?P<ts>\d{18,})$')
REAL_PATH = S.path


def _ops(monkeypatch, tmp_path) -> Path:
    where = tmp_path / "logs" / "ops"
    from backend import ops_daily
    monkeypatch.setattr(ops_daily, "folder", lambda: where)
    return where / S.FILE_NAME


def _lines(p: Path) -> list:
    return [LINE.match(x) for x in p.read_text("utf-8").splitlines()]


def test_a_model_call_leaves_its_tokens_and_usd_for_grafana(owner, monkeypatch, tmp_path):
    p = _ops(monkeypatch, tmp_path)
    _model(monkeypatch)                                   # usage: 900 in / 120 out
    _key(owner)
    q = {"question": "我現在的訓練狀態如何？"}
    assert owner.post("/api/v1/ai/ask", json=q).status_code == 200
    [m] = _lines(p)
    assert m, p.read_text("utf-8")
    assert (m["model"], m["purpose"], m["status"], m["i"], m["o"], m["err"]) == \
        ("claude-sonnet-5-5", "ask", "success", "900", "120", "")
    assert float(m["cost"]) == round((900 * 3 + 120 * 15) / 1e6, 6)     # 0.0045 USD
    raw = p.read_text("utf-8")
    assert FAKE_KEY not in raw and "訓練狀態" not in raw and "72%" not in raw   # no key, question, answer
    # a cached answer is not a call: no second line
    assert owner.post("/api/v1/ai/ask", json=q).json()["cached"] is True
    assert len(_lines(p)) == 1


def test_a_failed_call_is_an_error_line_with_its_code(owner, monkeypatch, tmp_path):
    p = _ops(monkeypatch, tmp_path)
    m = _model(monkeypatch, status=401)
    _key(owner)
    r = owner.post("/api/v1/ai/ask", json={"question": "最近哪裡不足？"})
    assert r.json()["detail"]["code"] == "AI_KEY_INVALID" and m.calls
    [x] = _lines(p)
    assert (x["status"], x["i"], x["o"], x["cost"], x["err"]) == ("error", "0", "0", "0.000000", "AI_KEY_INVALID")


def test_a_write_that_fails_never_touches_the_answer(owner, monkeypatch, tmp_path):
    blocker = tmp_path / "logs"
    blocker.write_text("a file where the folder should be", "utf-8")   # mkdir fails
    _ops(monkeypatch, tmp_path)
    _model(monkeypatch)
    _key(owner)
    r = owner.post("/api/v1/ai/ask", json={"question": "我現在的訓練狀態如何？"})
    assert r.status_code == 200 and r.json()["parts"]

    def boom():
        raise RuntimeError("no folder")
    monkeypatch.setattr(S, "path", boom)
    Q.clear_cache()
    assert owner.post("/api/v1/ai/ask", json={"question": "最近哪裡不足？"}).status_code == 200
    # the log file off: nothing is written, nothing breaks
    from backend import ops_daily
    monkeypatch.setattr(S, "path", REAL_PATH)
    monkeypatch.setattr(ops_daily, "folder", lambda: None)
    assert S.path() is None
    S.record("claude-sonnet-5-5", "success", 1, 1, 1)


def test_the_line_is_the_shared_schema(monkeypatch, tmp_path):
    ln = S.line("claude-sonnet-5-5", "success", 1_000_000, 1_000_000, 812.7, at_ns=1_792_900_000_000_000_000)
    assert ln == ('api_call,provider=anthropic,model=claude-sonnet-5-5,project=trailruncoach,purpose=ask,'
                  'status=success input_tokens=1000000i,output_tokens=1000000i,cost_usd=18.000000,'
                  'latency_ms=812i,error_message="" 1792900000000000000')
    # a model not in the price table: cost 0 but still a float literal (InfluxDB keeps one field type)
    odd = S.line("some model,x=1", "weird", 5, 5, 1, error='bad "quote"\nnext')
    assert "model=some_model_x_1," in odd and "status=error " in odd and "cost_usd=0.000000," in odd
    assert 'error_message="bad \\"quote\\" next"' in odd


def test_the_file_is_cut_over_past_its_size(monkeypatch, tmp_path):
    p = _ops(monkeypatch, tmp_path)
    monkeypatch.setattr(S, "MAX_BYTES", 300)
    for _ in range(3):
        S.record("claude-sonnet-5-5", "success", 10, 10, 5)
    assert p.with_name(S.FILE_NAME + ".1").exists() and len(_lines(p)) == 1
    assert all(_lines(p.with_name(S.FILE_NAME + ".1")))


def test_telegraf_tails_the_file_into_feeder():
    d = Path(__file__).resolve().parents[2] / "deploy" / "ops-monitoring"
    conf = (d / "telegraf.conf").read_text("utf-8")
    assert '"/trc-ops/api_calls.lp"' in conf and 'data_format = "influx"' in conf and "[[inputs.tail]]" in conf
    outs = conf.split("[[outputs.influxdb_v2]]")[1:]
    assert len(outs) == 2
    assert 'namedrop = ["api_call"]' in outs[0] and "${INFLUX_BUCKET}" in outs[0]
    second = outs[1].split("\n[[")[0]
    assert 'bucket = "feeder"' in second and 'namepass = ["api_call"]' in second and 'tagexclude = ["host"]' in second
    readme = (d / "README.md").read_text("utf-8")
    assert "api_calls.lp" in readme and "feeder" in readme
