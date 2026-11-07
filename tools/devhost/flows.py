#!/usr/bin/env python3
"""Drive the report builder's flows in the dev host with agent-browser; print one line per step. Dev only.

  ZEBRA_BI_REPORTS=<copy of reports> python3 tools/devhost/devhost.py scripts/report_server.py 8768   # in the background
  ZEBRA_BI_REPORTS=<same copy> python3 -I tools/devhost/flows.py <out dir>

The copy needs "october-close-test" and "p-l-close-october-2026" with their cached charts; the flows change it.
"""
import os, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ENV = dict(os.environ, AGENT_BROWSER_SESSION="zbr-flows")
OUT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")
H = ("const f=document.getElementById('a'), d=f.contentDocument, w=f.contentWindow;"
     "const $=s=>d.querySelector(s), all=s=>[...d.querySelectorAll(s)], sleep=ms=>new Promise(r=>setTimeout(r,ms));"
     "const titles=()=>all('#list .item .t').map(x=>x.firstChild.textContent).join(' | ');"
     "const view=()=>!$('#editor').hidden?'editor':(!$('#home').hidden?'home':'none');"
     "const item=p=>all('#list .item').find(i=>i.querySelector('.t').firstChild.textContent.startsWith(p));"
     "const menu=async(it,label)=>{it.querySelector('.dots').click(); await sleep(200); all('#menu button').find(b=>b.textContent===label).click();};")


def ab(*args):
    r = subprocess.run(["agent-browser", *args], env=ENV, capture_output=True, text=True)
    return (r.stdout.strip() or r.stderr.strip().replace("\n", " ")[:300]).splitlines()[-1:] or [""]


def seed():
    """A draft report: key message, two KPI tiles, a message and reason on the first chart, two untraced numbers."""
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import report as R
    slug = R.duplicate("p-l-close-october-2026", "Draft test")
    path = R.path_for(slug)
    rep = R.load(path)
    R.set_key_message(rep, "EBITDA beat plan by **113.1k** (+45.9%).")
    R.set_kpis(rep, [{"value": "359.7k", "label": "EBITDA, ΔPL +113.1k"}, {"value": "1.0M", "label": "Revenue, ΔPL −39.2k"}])
    charts = [c for r in rep["rows"] for c in r["cells"] if c["type"] == "chart"]
    R.update_text(rep, charts[0]["id"], "Costs carried EBITDA past plan", field="message")
    R.update_text(rep, charts[0]["id"], "A P&L reads top-down with ΔPL beside each line.", field="why")
    R.update_text(rep, charts[0]["id"], "COGS came in 99.0k under plan.")
    R.update_text(rep, charts[-1]["id"], "Revenue fell 41.0k short of plan.")
    R.save(path, rep)


def step(name, body):
    out = ab("eval", "(async()=>{%s %s})()" % (H, body))[0]
    print("%-12s %s" % (name, out))


seed()
ab("set", "viewport", "1300", "900")
ab("open", "http://127.0.0.1:8768/")
ab("wait", "9000")
step("start", "return view()+' / '+$('#title').textContent")
step("home", "$('#back').click(); await sleep(1500); return view()+' / '+titles()")
step("new", "$('#new').click(); $('#newtitle').value='Flow test'; $('#newcreate').click(); await sleep(2200);"
     "return view()+' / '+$('#title').textContent+' / empty='+!!$('#rows .empty')")
step("rename", "$('#back').click(); await sleep(1500); const it=item('Flow test'); await menu(it,'Rename'); await sleep(200);"
     "const inp=it.querySelector('input'); inp.value='Flow test renamed'; inp.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Enter'}));"
     "await sleep(1800); return titles()")
step("duplicate", "await menu(item('October close'),'Duplicate'); await sleep(2200); return titles()+' || toast: '+($('.toast')||{}).textContent")
ab("screenshot", os.path.join(OUT, "flows-home.png"))
step("delete", "await menu(item('Flow test renamed'),'Delete'); await sleep(2000);"
     "return view()+' / '+titles()+' || toast: '+($('.toast')||{}).textContent+' || deleted: '+$('#deleted-sum').textContent")
step("undo", "all('.toast button').find(b=>b.textContent==='Undo').click(); await sleep(2500); return view()+' / '+$('#title').textContent")
step("delete2", "all('#editor-actions .dots')[0].click(); await sleep(200); all('#menu button').find(b=>b.textContent==='Delete').click();"
     "await sleep(2200); $('#deleted').open=true; await sleep(200); return view()+' / deleted: '+all('#deleted-list .item .t').map(x=>x.textContent).join(' | ')")
step("restore", "all('#deleted-list .item button').find(b=>b.textContent==='Restore').click(); await sleep(2000);"
     "return titles()+' / deleted hidden='+$('#deleted').hidden")
step("a11y", "const m=item('P&L close').querySelector('.main'); m.focus(); await sleep(9000);"
     "return 'role='+m.getAttribute('role')+' label='+m.getAttribute('aria-label')+' focus kept='+(d.activeElement===m && m.isConnected)")
step("forever", "await menu(item('Flow test renamed'),'Delete'); await sleep(2000); $('#deleted').open=true; await sleep(200);"
     "all('#deleted-list .item button').find(b=>b.textContent==='Delete forever').click(); await sleep(200);"
     "const c=$('#deleted-list .confirm'); const txt=c.textContent; c.querySelector('button.danger').click(); await sleep(2000);"
     "return 'confirm: '+txt+' || deleted hidden='+$('#deleted').hidden+' / '+titles()")
step("open", "item('P&L close').querySelector('.main').click(); await sleep(6000);"
     "return view()+' / '+$('#title').textContent+' / frames='+all('#rows iframe').length+' drawn='+all('#rows iframe').filter(f=>f.offsetHeight>100).length")
ab("screenshot", os.path.join(OUT, "flows-editor.png"))
step("editdup", "all('#editor-actions .dots')[0].click(); await sleep(200); all('#menu button').find(b=>b.textContent==='Duplicate').click();"
     "await sleep(4000); return view()+' / '+$('#title').textContent")
step("draft", "$('#back').click(); await sleep(1500); item('Draft test').querySelector('.main').click(); await sleep(6000);"
     "return [$('#key').textContent, all('#kpis .kpi').length, ($('.chart-msg')||{}).textContent,"
     " all('.flag span').map(f=>f.textContent).join(' | ')].join(' / ')")
step("why", "$('.why-btn').click(); await sleep(200); return ($('.why-text')||{}).textContent")
step("keep", "all('.flag button')[0].click(); await sleep(2500); $('#reload').click(); await sleep(2500);"
     "return 'flags left: ' + all('.flag span').map(f=>f.textContent).join(' | ')")
step("pdfflag", "$('#report-menu').click(); await sleep(200); all('#menu button').find(b=>b.textContent==='Save as PDF').click();"
     "await sleep(2500); return ($('.toast')||{}).textContent")
step("layout", "const before=all('#rows .row').length; all('#layouts button').find(b=>b.dataset.layout==='variance-walk').click();"
     "await sleep(3500); return before+' -> '+all('#rows .row').length+' rows, pressed: '+"
     "all('#layouts button').filter(b=>b.getAttribute('aria-pressed')==='true').map(b=>b.textContent).join(',')")
ab("screenshot", os.path.join(OUT, "flows-draft.png"))
step("fastlayout", "const m=$('.chart-msg'); m.focus(); m.textContent='Typed just now'; m.dispatchEvent(new w.Event('input',{bubbles:true}));"
     "all('#layouts button').find(b=>b.dataset.layout==='board-pack').click(); await sleep(4000); $('#reload').click(); await sleep(3000);"
     "return 'message after layout: ' + all('.chart-msg').map(x=>x.textContent).find(t=>t.indexOf('Typed')>=0)")
step("badsave", "const l=$('#kpis .l'); l.focus(); l.textContent='x'.repeat(130); l.dispatchEvent(new w.Event('input',{bubbles:true}));"
     "await sleep(2500); return 'toast: ' + ($('.toast')||{}).textContent")
