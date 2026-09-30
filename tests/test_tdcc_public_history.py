import math
from pathlib import Path

import pytest

from scripts.backfill_tdcc_public_history import LEVELS,parse_response,verified_receipt,digest
from scripts.backfill_tdcc_public_history import exact_no_data,retry_after_seconds,RequestPacer,paced_request


def source_html(*,day="114年10月09日",ticker="2330",adjustment=0):
    # Explicit source contract: each holding range has one person at its lower
    # bound. The real source publishes percentages truncated to two decimals.
    shares=[int(x.split("-")[0].replace("以上","")) for x in LEVELS]
    total=sum(shares)+adjustment
    rows=[]
    for level,(label,held) in enumerate(zip(LEVELS,shares),1):
        pct=math.floor(held/total*10000)/100
        rows.append(f"<tr><td>{level}</td><td>{label}</td><td>1</td><td>{held}</td><td>{pct}</td></tr>")
    if adjustment:
        rows.append(f"<tr><td>16</td><td>差異數調整（說明4）</td><td></td><td>{adjustment}</td><td>-0.01</td></tr>")
    return (f"<p>證券代號：{ticker}</p><span>資料日期：{day}</span>"
        "<table><tr><th>序</th><th>持股/單位數分級</th><th>人數</th><th>股數/單位數</th><th>占集保庫存數比例 (%)</th></tr>"
        +"".join(rows)+f"<tr><td>17</td><td>合　計</td><td>15</td><td>{total}</td><td>100.00</td></tr></table>")


@pytest.mark.parametrize("adjustment",[0,-499])
def test_complete_levels_and_negative_adjustment_reconcile(adjustment):
    rows,meta=parse_response(source_html(adjustment=adjustment),"20251009","2330")
    assert len(rows)==15 and meta["adjustment_shares"]==adjustment
    assert sum(r["股數"] for r in rows)+adjustment==meta["total_shares"]


@pytest.mark.parametrize("html,error",[
    (source_html(day="114年10月17日"),"date mismatch"),
    (source_html(ticker="2603"),"ticker"),
    (source_html().replace("<td>1</td><td>1-999</td>","<td>2</td><td>1-999</td>"),"level number/label"),
    (source_html().replace("<td>15</td><td>1000001以上</td>","<td>14</td><td>800001-1000000</td>"),"Missing or duplicate"),
    (source_html().replace("<td>1</td><td>1-999</td><td>1</td><td>1</td>","<td>1</td><td>1-999</td><td>1</td><td>nan</td>"),"Nonfinite"),
    (source_html().replace("<td>合　計</td><td>15</td>","<td>合　計</td><td>16</td>"),"totals"),
    ("<p>查無資料</p>","ticker"),
])
def test_partial_wrong_identity_and_unknown_are_rejected(html,error):
    with pytest.raises(ValueError,match=error):
        parse_response(html,"20251009","2330")


def test_resume_requires_source_and_table_hashes(tmp_path):
    raw=tmp_path/"source.html"
    table=tmp_path/"table.csv"
    raw.write_text(source_html(),encoding="utf-8")
    table.write_text("frozen table",encoding="utf-8")
    item={"raw_path":str(raw),"table_path":str(table),"raw_sha256":digest(raw),"table_sha256":digest(table)}
    verified_receipt(item,"20251009","2330")
    table.write_text("modified table",encoding="utf-8")
    with pytest.raises(ValueError,match="hash mismatch"):
        verified_receipt(item,"20251009","2330")


def test_no_data_requires_exact_echoed_query():
    html='<form id="form1"><input name="stockNo" value="009813"><select name="scaDate"><option selected value="20251009">20251009</option></select></form><table><td colspan="5">查無此資料</td></table>'
    assert exact_no_data(html,"20251009","009813")
    assert not exact_no_data(html,"20251023","009813")
    assert not exact_no_data(html,"20251009","2330")
    assert not exact_no_data('<table><td colspan="5">查無此資料</td></table>',"20251009","009813")


def test_retry_after_seconds_and_http_date():
    assert retry_after_seconds("12")==12
    assert retry_after_seconds("Thu, 01 Jan 1970 00:01:00 GMT",now=40)==20
    assert retry_after_seconds("invalid")==0


class FakeClock:
    def __init__(self):
        self.now=0.
        self.waits=[]
    def clock(self):
        return self.now
    def sleep(self,seconds):
        self.waits.append(seconds)
        self.now+=seconds


def test_pacer_never_exceeds_three_request_starts_per_second():
    fake=FakeClock()
    pacer=RequestPacer(.25,clock=fake.clock,sleeper=fake.sleep)
    starts=[]
    for _ in range(12):
        starts.append(pacer.before())
        pacer.complete()
    assert all(b-a>=.24999 for a,b in zip(starts,starts[1:]))
    assert all(starts[i+3]-starts[i]>=1 for i in range(len(starts)-3))


def test_transport_429_honors_retry_after_and_falls_back_to_one_second():
    from types import SimpleNamespace
    fake=FakeClock()
    pacer=RequestPacer(.25,clock=fake.clock,sleeper=fake.sleep)
    replies=iter([SimpleNamespace(status_code=429,headers={"Retry-After":"20"}),SimpleNamespace(status_code=200,headers={})])
    session=SimpleNamespace(request=lambda *a,**k:next(replies))
    events=[]
    result=paced_request(session,"GET",pacer,events,timeout=30)
    assert result.status_code==200 and len(events)==2
    assert 20 in fake.waits and pacer.delay==1
    assert events[0]["backoff_seconds"]==20


def test_network_errors_retry_bounded_with_exponential_backoff():
    import requests
    from types import SimpleNamespace
    fake=FakeClock()
    pacer=RequestPacer(.25,clock=fake.clock,sleeper=fake.sleep)
    def fail(*args,**kwargs):
        raise requests.Timeout("fixture network outage")
    events=[]
    with pytest.raises(requests.Timeout):
        paced_request(SimpleNamespace(request=fail),"GET",pacer,events,timeout=30)
    assert len(events)==3
    assert [x["backoff_seconds"] for x in events if "backoff_seconds" in x]==[2,4]


def test_normalized_total_keeps_semantic17_even_when_serial16():
    from scripts.normalize_tdcc_public_history import normalize_response
    html=source_html().replace("<td>17</td><td>合　計</td>","<td>16</td><td>合　計</td>")
    rows,meta=normalize_response(html,"20251009","2330")
    assert [r["持股分級"] for r in rows]==list(range(1,16))+[17]
    assert not meta["adjustment_row_present"]
    assert rows[-1]["人數"]==meta["total_holders"]==15
    assert meta["source_extra_rows"][0]["source_serial"]==16


def test_normalized_adjustment_preserves_blank_count_and_negative_shares():
    from scripts.normalize_tdcc_public_history import normalize_response
    rows,meta=normalize_response(source_html(adjustment=-499),"20251009","2330")
    assert [r["持股分級"] for r in rows]==list(range(1,18))
    assert rows[15]["人數"] is None and rows[15]["股數"]==-499
    assert rows[15]["占集保庫存數比例%"]==-.01
    assert rows[16]["人數"]==sum(r["人數"] for r in rows[:15])


def test_priority_changes_only_order_and_spans_all_weeks():
    from scripts.backfill_tdcc_public_history import prioritize_jobs
    jobs=[("20251023","Y001"),("20251023","2330"),("20251023","6111"),("20260226","000218"),("20260226","2330")]
    result=prioritize_jobs(jobs,{"2330"})
    assert result[:2]==[("20251023","2330"),("20260226","2330")]
    assert result[2]==("20251023","6111")
    assert sorted(result)==sorted(jobs) and len(result)==len(jobs)
