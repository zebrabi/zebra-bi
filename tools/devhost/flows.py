#!/usr/bin/env python3
"""Drive the report builder's flows in the dev host with agent-browser; print one line per step. Dev only.

  ZEBRA_BI_REPORTS=<copy of reports> CLAUDE_PLUGIN_DATA=<scratch> python3 tools/devhost/devhost.py scripts/report_server.py 8768 &
  ZEBRA_BI_REPORTS=<same copy> CLAUDE_PLUGIN_DATA=<same scratch> python3 -I tools/devhost/flows.py <out dir> [all|main|sign-in]

The copy needs "october-close-test" and "p-l-close-october-2026" with their cached charts; the flows change it. The
scratch folder holds a copy of the renderer (renderer/visual.html and visual.json) and no auth.json: the sign-in
flows fake Zebra BI's sign-in through the dev host's /fake, so nothing reaches Zebra BI. A step with an expectation
prints ok or FAIL; the run exits 1 when any step failed.
"""
import json, os, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ENV = dict(os.environ, AGENT_BROWSER_SESSION="zbr-flows")
OUT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")
H = ("const f=document.getElementById('a'), d=f.contentDocument, w=f.contentWindow;"
     "const $=s=>d.querySelector(s), all=s=>[...d.querySelectorAll(s)], sleep=ms=>new Promise(r=>setTimeout(r,ms));"
     "const titles=()=>all('#list .item .t').map(x=>x.firstChild.textContent).join(' | ');"
     "const view=()=>!$('#editor').hidden?'editor':(!$('#home').hidden?'home':'none');"
     "const item=p=>all('#list .item').find(i=>i.querySelector('.t').firstChild.textContent.startsWith(p));"
     "const menu=async(it,label)=>{it.querySelector('.dots').click(); await sleep(200); all('#menu button').find(b=>b.textContent===label).click();};")

# The sign-in steps' helpers: the dev host's fake, the banner, ghosts and drawn charts, the report's More menu.
SI = ("const fake=b=>fetch('/fake',{method:'POST',body:JSON.stringify(b)}).then(r=>r.json());"
      "const banner=()=>$('#signin').textContent, btns=()=>all('#signin button').map(b=>b.textContent).join(',');"
      "const drawn=()=>all('#rows .chart').filter(b=>b.querySelector('iframe')).length, ghosts=()=>all('#rows .ghost').length;"
      "const once=()=>'banners='+all('.signin').length+' chart errors='+all('#rows .chart .err').length;"
      "const more=async label=>{$('#report-menu').click(); await sleep(200); all('#menu button').find(b=>b.textContent===label).click();};"
      "const toastText=()=>($('.toast')||{}).textContent||'';")


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
    R.update_text(rep, charts[0]["id"], "COGS came in 987.6k under plan.")  # numbers no chart holds
    R.update_text(rep, charts[-1]["id"], "Revenue fell 876.5k short of plan.")
    rep["show_notes"] = True  # hidden notes are not checked, and the source report may have them hidden
    R.save(path, rep)


FAILED = []
PRE = [""]  # set by a group of steps, before each of them


def step(name, body, *want):
    """Run one step in the page; with want, every string must be in what it returns."""
    out = ab("eval", "(async()=>{%s %s})()" % (H + SI + PRE[0], body))[0]
    mark = "" if not want else "ok   " if all(w in out for w in want) else "FAIL "
    if mark == "FAIL ":
        FAILED.append(name)
    print("%-14s %s%s" % (name, mark, out))


def main_flows():
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
    step("open", "item('P&L close').querySelector('.main').click(); await sleep(1500);"
         "for (let i=0;i<27 && all('#rows iframe').some(f=>f.offsetHeight<=100);i++) await sleep(500);"  # until every chart drew
         "return view()+' / '+$('#title').textContent+' / frames='+all('#rows iframe').length+' drawn='+all('#rows iframe').filter(f=>f.offsetHeight>100).length")
    ab("screenshot", os.path.join(OUT, "flows-editor.png"))
    step("editdup", "all('#editor-actions .dots')[0].click(); await sleep(200); all('#menu button').find(b=>b.textContent==='Duplicate').click();"
         "await sleep(4000); return view()+' / '+$('#title').textContent")
    step("pptx", "$('#report-menu').click(); await sleep(200); all('#menu button').find(b=>b.textContent==='Save as PowerPoint').click();"
         "for (let i=0;i<60 && !(($('.toast')||{}).textContent||'').startsWith('PowerPoint saved');i++) await sleep(1000);"
         "return ($('.toast')||{}).textContent")
    step("draft", "$('#back').click(); await sleep(1500); item('Draft test').querySelector('.main').click(); await sleep(6000);"
         "return [$('#key').textContent, all('#kpis .kpi').length, ($('.chart-msg')||{}).textContent,"
         " all('.flag span').map(f=>f.textContent).join(' | ')].join(' / ')")
    step("why", "$('.why-btn').click(); await sleep(200); return ($('.why-text')||{}).textContent")
    step("keep", "all('.flag button')[0].click(); await sleep(2500); $('#reload').click(); await sleep(2500);"
         "return 'flags left: ' + all('.flag span').map(f=>f.textContent).join(' | ')")
    step("pptxflag", "$('#report-menu').click(); await sleep(200); all('#menu button').find(b=>b.textContent==='Save as PowerPoint').click();"
         "await sleep(2500); return ($('.toast')||{}).textContent")
    step("pdfflag", "$('#report-menu').click(); await sleep(200); all('#menu button').find(b=>b.textContent==='Save as PDF').click();"
         "await sleep(2500); return ($('.toast')||{}).textContent")
    step("layout", "const before=all('#rows .row').length; all('#layouts button').find(b=>b.dataset.layout==='variance-walk').click();"
         "await sleep(3500); return before+' -> '+all('#rows .row').length+' rows, pressed: '+"
         "all('#layouts button').filter(b=>b.getAttribute('aria-checked')==='true').map(b=>b.textContent).join(',')")
    ab("screenshot", os.path.join(OUT, "flows-draft.png"))
    step("fastlayout", "const m=$('.chart-msg'); m.focus(); m.textContent='Typed just now'; m.dispatchEvent(new w.Event('input',{bubbles:true}));"
         "all('#layouts button').find(b=>b.dataset.layout==='board-pack').click(); await sleep(4000); $('#reload').click(); await sleep(3000);"
         "return 'message after layout: ' + all('.chart-msg').map(x=>x.textContent).find(t=>t.indexOf('Typed')>=0)")
    step("conflict", "const m=all('.chart-msg')[0]; m.focus(); m.textContent='Mine, typed while the chat edits'; m.dispatchEvent(new w.Event('input',{bubbles:true}));"
         "await fetch('/rpc',{method:'POST',body:JSON.stringify({method:'tools/call',params:{name:'add_text',arguments:{report:'draft-test',text:'Added from the chat meanwhile.'}}})});"
         "m.blur(); await sleep(5000);"
         "return 'mine kept: '+all('.chart-msg').some(x=>x.textContent.indexOf('Mine, typed')>=0)+' / chat text: '+all('.text').some(x=>x.textContent.indexOf('Added from the chat')>=0)"
         "+' / toast: '+(($('.toast')||{}).textContent||'')")
    step("boardpack", "return 'pages: '+all('.page-mark').map(x=>x.textContent).join(',')+' / notes: '+$('#notes-toggle').textContent"
         "+' / '+$('#layout-hint').textContent.slice(0,40)")
    step("onepager", "all('#layouts button').find(b=>b.dataset.layout==='one-pager').click(); await sleep(3500);"
         "return 'charts a row: '+all('#rows .row').map(r=>r.querySelectorAll('.chart').length).filter(n=>n).join(',')"
         "+' / notes: '+$('#notes-toggle').textContent+' / page marks: '+all('.page-mark').length")
    ab("screenshot", os.path.join(OUT, "flows-one-pager.png"))
    ab("set", "viewport", "600", "900")
    ab("wait", "1500")
    step("narrow600", "const b=$('#bar'); return b.className+' / one line: '+(b.getBoundingClientRect().height<=60)"
         "+' / layouts as a menu: '+(getComputedStyle($('#layout-pick')).display!=='none')")
    ab("set", "viewport", "420", "900")
    ab("wait", "1500")
    step("narrow420", "$('#report-menu').click(); await sleep(300); const t=all('#menu button').map(b=>b.textContent).slice(0,3).join(',');"
         "d.body.click(); return $('#bar').className+' / one line: '+($('#bar').getBoundingClientRect().height<=60)+' / menu starts: '+t")
    ab("set", "viewport", "1300", "900")
    ab("wait", "1500")
    step("badsave", "const l=$('#kpis .l'); l.focus(); l.textContent='x'.repeat(130); l.dispatchEvent(new w.Event('input',{bubbles:true}));"
         "await sleep(2500); return 'toast: ' + ($('.toast')||{}).textContent")


def seed_sign_in():
    """A copy of the P&L report whose charts the dev host hides, so the builder shows them as ghosts."""
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import report as R
    for r in R.reports():  # one "Sign-in test" in the list, so the steps open this run's copy
        if r["title"] == "Sign-in test":
            R.trash(r["report"])
    return R.duplicate("p-l-close-october-2026", "Sign-in test")


REFUSED = {
    "trial_ended": {"kind": "trial_ended", "date": "22 Nov 2026", "message": "Your Zebra BI trial ended on 22 Nov 2026. "
                    "Your report and its texts stay; the charts need a Zebra BI licence."},
    "no_licence": {"kind": "no_licence", "date": None, "message": "Zebra BI found no licence for your company."},
    "no_verified_email": {"kind": "no_verified_email", "date": None, "message": "Microsoft did not share a verified email "
                          "address for this account, so Zebra BI cannot tell which organisation it belongs to."},
    "other": {"kind": "other", "date": None, "message": "Your organisation's Zebra BI subscription is suspended."},
}


def refuse(kind):
    """Signed in, every chart hidden again, and Zebra BI refusing the first one it is asked to draw."""
    return ("await fake({auth:{signed_in:true,state:'signed_in',refusal:null},refuse:%s,hide:SLUG,delay:300}); links.length=0;"
            "$('#reload').click(); await sleep(4500);" % json.dumps(REFUSED[kind]))


def sign_in_flows():
    slug = seed_sign_in()
    PRE[0] = "const SLUG=%s;" % json.dumps(slug)
    ab("set", "viewport", "1300", "900")
    ab("open", "http://127.0.0.1:8768/")
    ab("wait", "6000")
    step("si-ghosts", "await fake({auth:{signed_in:false,state:'idle'},refuse:null,hide:SLUG,delay:700});"
         "if (view()!=='home') { $('#back').click(); await sleep(1500); } item('Sign-in test').querySelector('.main').click(); await sleep(3500);"
         "return banner()+' / ghosts='+ghosts()+' drawn='+drawn()+' / shapes: '+all('#rows .ghost svg').map(s=>s.querySelectorAll('rect').length+' '+"
         "(s.querySelector('rect').getAttribute('width')<s.querySelector('rect').getAttribute('height')?'columns':'bars')).join(', ')"
         "+' / label: '+$('#rows .ghost .gl').textContent+' / '+once()",
         "Your report is ready. Sign in to Zebra BI to draw its 5 charts.", "ghosts=5 drawn=0", "10 columns",
         "label: Zebra BI chart, drawn after sign-in", "banners=1 chart errors=0")
    ab("screenshot", os.path.join(OUT, "signin-1-ghosts-1300.png"))
    for width in ("760", "420"):  # two cells a row at about 340 px each, then the inline width of a phone
        ab("set", "viewport", width, "900")
        ab("wait", "1500")
        step("si-ghosts-" + width, "return 'cells: '+all('#rows .ghost').map(g=>g.clientWidth).join(',')+' / inside: '+"
             "all('#rows .ghost').every(g=>g.querySelector('svg').getBoundingClientRect().right<=g.getBoundingClientRect().right+0.5)"
             "+' / bar: '+$('#bar').className", "inside: true")
        ab("screenshot", os.path.join(OUT, "signin-1-ghosts-%s.png" % width))
    ab("set", "viewport", "1300", "900")
    ab("wait", "1500")
    step("si-pdf", "await more('Save as PDF'); await sleep(2500); return toastText()+' / focus: '+d.activeElement.textContent",
         "No PDF was saved: 5 charts are not drawn yet. Sign in to Zebra BI with the button above the report first.",
         "focus: Sign in to Zebra BI")
    step("si-pptx", "await more('Save as PowerPoint'); await sleep(2500); return toastText()",
         "No PowerPoint was saved: 5 charts are not drawn yet.")
    step("si-menu-out", "$('#report-menu').click(); await sleep(200); const t=all('#menu button').map(b=>b.textContent).join(','); d.body.click();"
         "return 'not signed in, Sign out offered: '+(t.indexOf('Sign out')>=0)", "Sign out offered: false")
    step("si-start", "links.length=0; all('#signin button').find(b=>b.textContent==='Sign in to Zebra BI').click(); await sleep(1500);"
         "return banner()+' / buttons: '+btns()+' / opened: '+links.join(',')",
         "Finish signing in in your browser. Your charts appear here as soon as you are back.",
         "buttons: Open the sign-in page again", "opened: https://sign-in.example.invalid/")
    ab("screenshot", os.path.join(OUT, "signin-2-waiting.png"))
    step("si-again", "all('#signin button').find(b=>b.textContent==='Open the sign-in page again').click(); await sleep(500);"
         "return 'opened '+links.length+' times'", "opened 2 times")
    step("si-gave-up", "await fake({auth:{signed_in:false,state:'idle'}}); await sleep(5000);"  # sign-in.json: waiting past 5 min is idle
         "return banner()+' / buttons: '+btns()", "draw its 5 charts", "buttons: Sign in to Zebra BI")
    step("si-aha", "all('#signin button')[0].click(); await sleep(1500); w.__noReload=1; w.__order=[];"
         "new w.MutationObserver(()=>all('#rows .chart').forEach(b=>{ if (b.querySelector('iframe') && w.__order.indexOf(b.dataset.cell)<0)"
         " w.__order.push(b.dataset.cell); })).observe($('#rows'),{childList:true,subtree:true});"
         "await fake({auth:{signed_in:true,state:'signed_in'},delay:1500}); const seen=[]; let last=-1;"
         "for (let i=0;i<40 && drawn()<2;i++) { if (drawn()!==last) { last=drawn(); seen.push(last); } await sleep(250); }"
         "return 'drawn '+drawn()+' of 5 / banner: '+banner()+' / ghost label: '+(($('#rows .ghost .gl')||{}).textContent||'')",
         "banner: Signed in to Zebra BI", "ghost label: Zebra BI chart, drawing now")
    ab("screenshot", os.path.join(OUT, "signin-3-drawing.png"))
    step("si-aha-done", "for (let i=0;i<60 && drawn()<5;i++) await sleep(250); await sleep(300);"
         "const top=all('#rows .chart').map(b=>b.dataset.cell).join(',');"  # all on screen at this size: top to bottom
         "return 'drawn '+drawn()+' ghosts '+ghosts()+' / order '+w.__order.join(',')+' / top to bottom: '+(w.__order.join(',')===top)"
         "+' / no reload: '+(w.__noReload===1)", "drawn 5 ghosts 0", "top to bottom: true", "no reload: true")
    ab("screenshot", os.path.join(OUT, "signin-4-drawn.png"))
    step("si-quiet", "await sleep(9000); return 'banner after: \"'+banner()+'\"'", 'banner after: \\"\\"')
    step("si-signout", "await more('Sign out of Zebra BI'); await sleep(1500);"
         "return toastText()+' / drawn='+drawn()+' / banner: \"'+banner()+'\"'",
         "Signed out of Zebra BI", "drawn=5", 'banner: \\"\\"')
    step("si-one-left", "await fake({hide:SLUG,only:['marketing-costs-by-month']}); $('#reload').click(); await sleep(3000);"
         "return 'drawn='+drawn()+' ghosts='+ghosts()+' / '+banner()",
         "drawn=4 ghosts=1", "Sign in to Zebra BI to draw its chart.")
    step("si-trial", refuse("trial_ended") + "return banner()+' / buttons: '+btns()+' / ghosts='+ghosts()+' / '+once()",
         "Your Zebra BI trial ended on 22 Nov 2026. Your report and its texts stay; the charts need a Zebra BI licence.",
         "buttons: Contact sales", "ghosts=5", "banners=1 chart errors=0")
    ab("screenshot", os.path.join(OUT, "signin-5-trial-ended.png"))
    step("si-sales", "all('#signin button')[0].click(); await sleep(500); return 'opened: '+links.join(',')",
         "opened: https://zebrabi.com/contact-sales/")
    step("si-pdf-refused", "await more('Save as PDF'); await sleep(2500); return toastText()",
         "No PDF was saved: 5 charts are not drawn yet. The note above the report says why.")
    step("si-no-licence", refuse("no_licence") + "const a=banner()+' / buttons: '+btns();"
         "all('#signin button')[0].click(); await sleep(1500); return a+' / then: '+banner()",
         "Zebra BI found no licence for your company.", "buttons: Sign in again", "then: Finish signing in")
    step("si-no-email", refuse("no_verified_email") + "const a=banner()+' / buttons: '+btns()+' / '+once();"
         "all('#signin button')[0].click(); await sleep(500); return a+' / opened: '+links.join(',')",
         "Microsoft did not share a verified email address", "buttons: support@zebrabi.com", "opened: mailto:support@zebrabi.com",
         "banners=1 chart errors=0")
    step("si-other", refuse("other") + "return banner()+' / buttons: '+btns()",
         "Your organisation's Zebra BI subscription is suspended.", "buttons: Contact sales")
    step("si-expired", "await fake({auth:{signed_in:false,state:'idle',refusal:{kind:'expired',date:null,"
         "message:'Your Zebra BI sign-in has expired. Sign in again to draw the charts.'}}}); await sleep(5000);"
         "return banner()+' / buttons: '+btns()+' / ghosts='+ghosts()",
         "Your Zebra BI sign-in has expired. Sign in again to draw the charts.", "buttons: Sign in again", "ghosts=5")
    step("si-keyboard", "const b=all('#signin button')[0]; return 'a real button: '+(b.tagName==='BUTTON')+' / live region: '"
         "+$('#signin').getAttribute('role')", "a real button: true / live region: status")
    ab("eval", "fetch('/fake',{method:'POST',body:JSON.stringify({auth:null,refuse:null,delay:0})}).then(r=>r.text())")


if __name__ == "__main__":
    which = sys.argv[2] if len(sys.argv) > 2 else "all"
    if which in ("all", "main"):
        main_flows()
    if which in ("all", "sign-in"):
        sign_in_flows()
    ab("close")
    if FAILED:
        print("failed: " + ", ".join(FAILED))
        sys.exit(1)
