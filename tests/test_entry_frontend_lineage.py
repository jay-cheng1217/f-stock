"""Execute the actual frontend module in Node, with controlled HTTP and DOM IO."""
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest


NODE_HARNESS = r"""
const fs = require('fs'), vm = require('vm');
const {script, response} = JSON.parse(fs.readFileSync(0, 'utf8'));
const nodes = {
  'canonical-entry-body': {innerHTML: 'PREVIOUS_STALE_ROWS'},
  'canonical-entry-sub': {textContent: 'PREVIOUS_DATE'},
  'industry-news-body': {innerHTML: ''},
};
const calls = [];
const fetch = async (url, options) => {
  calls.push({url, options});
  if (url.startsWith('/static/industry_news.html')) return {ok:true, text:async()=>''};
  if (url === '/api/entry/canonical') {
    if (response.networkError) throw new Error(response.networkError);
    return {ok:response.status>=200 && response.status<300, status:response.status,
      json:async()=>{if(response.invalidJSON) throw new Error('invalid JSON'); return response.body;}};
  }
  // A stale static plan would look usable; the consumer must never request it.
  if (url.startsWith('/static/entry_canonical.json')) return {
    ok:true, json:async()=>({rows:[{stock:'9999 STALE_STATIC', kind:'go'}]})};
  throw new Error('Unexpected request: ' + url);
};
(async()=>{
  vm.runInNewContext(script, {fetch, Date, console, document:{getElementById:id=>nodes[id]}});
  for(let i=0;i<5;i++) await new Promise(resolve=>setImmediate(resolve));
  process.stdout.write(JSON.stringify({calls, nodes}));
})().catch(error=>{console.error(error); process.exit(1);});
"""


def run_frontend(response):
    node = shutil.which("node")
    assert node, "Node.js is required to execute the actual frontend contract fixture"
    html = (Path(__file__).resolve().parents[1] / "frontend/index.html").read_text(encoding="utf-8")
    anchor = html.index("// Canonical 進場名單")
    start = html.index("(function () {", anchor)
    end = html.index("})();", start) + len("})();")
    completed = subprocess.run([node, "-e", NODE_HARNESS],
        input=json.dumps({"script": html[start:end], "response": response}, ensure_ascii=False),
        encoding="utf-8", capture_output=True, check=True)
    result = json.loads(completed.stdout)
    assert all(not call["url"].startswith("/static/entry_canonical.json") for call in result["calls"])
    assert next(call for call in result["calls"] if call["url"] == "/api/entry/canonical")["options"]["cache"] == "no-store"
    assert "PREVIOUS_STALE_ROWS" not in result["nodes"]["canonical-entry-body"]["innerHTML"]
    return result["nodes"]


@pytest.mark.parametrize("response,expected", [
    ({"status": 503, "body": {"status": "data_error", "error": "來源已更新，舊名單拒絕"}}, "來源已更新，舊名單拒絕"),
    ({"status": 200, "body": {"status": "data_error", "error": "<stale>"}}, "&lt;stale&gt;"),
    ({"status": 200, "body": {"rows": "invalid"}}, "名單回應格式不完整"),
    ({"status": 503, "invalidJSON": True}, "invalid JSON"),
    ({"networkError": "network unavailable"}, "network unavailable"),
])
def test_api_error_never_restores_stale_static_rows(response, expected):
    nodes = run_frontend(response)
    assert expected in nodes["canonical-entry-body"]["innerHTML"]
    assert "名單暫不可用" in nodes["canonical-entry-body"]["innerHTML"]
    assert nodes["canonical-entry-sub"]["textContent"] == "名單資料尚未通過驗證"


def test_valid_empty_list_remains_empty_and_keeps_current_date():
    nodes = run_frontend({"status": 200, "body": {"rows": [], "trade_date": "2026-09-07"}})
    assert "尚無名單" in nodes["canonical-entry-body"]["innerHTML"]
    assert "名單暫不可用" not in nodes["canonical-entry-body"]["innerHTML"]
    assert nodes["canonical-entry-sub"]["textContent"].startswith("2026-09-07")


def test_successful_18_candidates_keep_order_fields_and_rere_lane():
    rows = [{"stock": f"{2300+i} 候選{i}", "priority": i+1, "lane": "rere" if i<6 else "main",
             "kind": "small", "status": "rere lane" if i<6 else "觀察",
             "zone": f"{100+i}-{105+i}", "stop": str(95+i), "no_chase": str(108+i),
             "ret20d": f"+{i}.25%", "sector": "半導體", "reason": f"原始依據{i}"} for i in range(18)]
    nodes = run_frontend({"status": 200, "body": {"rows": rows, "trade_date": "2026-09-07",
                         "source_file": "entry_list_20260907.json", "subtitle": "9/4 收盤資料"}})
    body = nodes["canonical-entry-body"]["innerHTML"]
    assert re.findall(r'data-ticker="(\d+)"', body) == [str(2300+i) for i in range(18)]
    assert body.count('class="lane-badge lane-rere"') == 6
    for row in rows:
        for field in ("stock", "zone", "stop", "no_chase", "ret20d", "reason"):
            assert row[field] in body
    assert nodes["canonical-entry-sub"]["textContent"] == "2026-09-07 · entry_list_20260907.json · 9/4 收盤資料"
