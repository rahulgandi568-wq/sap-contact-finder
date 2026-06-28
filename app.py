#!/usr/bin/env python3
"""
SAP C2C Contact Finder — local web app.
Paste a resume -> detects your SAP module -> pulls matching C2C postings from
corptocorp.org and extracts the recruiter's name, email and phone.

Run:
    pip install -r requirements.txt
    python app.py
Then open http://localhost:8000
"""
import re, html, os, base64, time, ssl, smtplib, concurrent.futures as cf
from email.message import EmailMessage
from flask import Flask, request, jsonify, render_template_string
import requests
from bs4 import BeautifulSoup

app = Flask(__name__)
HEADERS = {"User-Agent": "Mozilla/5.0 (SAP-contact-finder; personal use)"}

# ---------------- email sender config (set these as env vars on the server) ----
GMAIL_USER         = os.environ.get("GMAIL_USER", "")          # the Gmail you send FROM
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")  # 16-char Google App Password
# TEST_MODE on by default -> every email goes to TEST_EMAIL, never the real recruiter.
TEST_MODE  = os.environ.get("TEST_MODE", "1").lower() not in ("0", "false", "no", "off")
TEST_EMAIL = os.environ.get("TEST_EMAIL", "sg.sumagandham@gmail.com")

@app.after_request
def _cors(resp):
    resp.headers["Access-Control-Allow-Origin"]  = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    return resp

def fill_tpl(s, j):
    return (s or "") \
        .replace("{recruiter}", j.get("recruiter") or j.get("name") or "there") \
        .replace("{role}",      j.get("role") or j.get("title") or "your SAP requirement") \
        .replace("{company}",   j.get("company") or "your client") \
        .replace("{module}",    j.get("module") or "SAP")

def send_one(to_addr, subject, body, resume_b64, resume_name):
    msg = EmailMessage()
    msg["From"] = GMAIL_USER
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)
    if resume_b64:
        try:
            raw = base64.b64decode(resume_b64.split(",")[-1])
            sub = "pdf" if (resume_name or "").lower().endswith(".pdf") else "octet-stream"
            msg.add_attachment(raw, maintype="application", subtype=sub,
                               filename=resume_name or "resume.pdf")
        except Exception:
            pass
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx, timeout=30) as s:
        s.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        s.send_message(msg)

# ---------------- module detection ----------------
MODULES = [
 ("EWM","SAP EWM","logistics",["extended warehouse management","ewm"]),
 ("WM","SAP WM","logistics",["warehouse management"]),
 ("MM","SAP MM","logistics",["materials management","sap mm"]),
 ("SD","SAP SD","logistics",["sales and distribution","order to cash"]),
 ("TM","SAP TM","logistics",["transportation management"]),
 ("PP","SAP PP/QM","logistics",["production planning","quality management"]),
 ("FICO","SAP FICO","finance",["financial accounting","controlling","fico","fi/co"]),
 ("RAR","SAP RAR","finance",["revenue accounting","rar"]),
 ("ABAP","SAP ABAP","tech",["abap development","abap on hana","abap"]),
 ("BASIS","SAP Basis","tech",["basis administration","hana administration","sap basis"]),
 ("FIORI","SAP Fiori","tech",["sapui5","fiori"]),
 ("BWBTP","SAP BW/BTP","tech",["bw/4hana","business technology platform","sap bw"]),
 ("PIPO","SAP PI/PO","tech",["process integration","integration architect","pi/po","cpi"]),
 ("SF","SuccessFactors","hcm",["successfactors","employee central"]),
 ("HCM","SAP HCM","hcm",["human capital management","payroll"]),
 ("ARIBA","SAP Ariba","procurement",["ariba"]),
 ("GRC","SAP GRC / Security",["security"],["access control","segregation of duties","sap security","sap grc","grc","authorization","role design"]),
 ("S4","S/4HANA","cross",["s/4hana","s4hana","s/4 hana"]),
]
QUERY = {"EWM":"sap ewm","WM":"sap wm","MM":"sap mm","SD":"sap sd","TM":"sap tm",
 "PP":"sap pp","FICO":"sap fico","RAR":"sap rar","ABAP":"sap abap","BASIS":"sap basis",
 "FIORI":"sap fiori","BWBTP":"sap bw","PIPO":"sap pi po integration","SF":"successfactors",
 "HCM":"sap hcm","ARIBA":"sap ariba","GRC":"sap security grc","S4":"s/4hana sap"}

def bcount(text, term):
    return len(re.findall(r"(?:^|[^a-z0-9])"+re.escape(term)+r"(?![a-z0-9])", text))

EXCL = ["s/4hana","s4hana","s/4 hana","sapui5","fiori","abap","netweaver","successfactors","ariba","hybris"]
def detect_module(resume):
    t = " " + resume.lower() + " "
    has_sap = bcount(t,"sap") > 0
    has_excl = any(bcount(t,x) for x in EXCL)
    if not (has_sap or has_excl):
        return None  # not an SAP resume — generic phrases alone don't qualify
    hits = []
    for code,label,group,terms in MODULES:
        sc = sum(bcount(t,term)*(2 if len(term.split())>1 else 1) for term in terms)
        if sc: hits.append((sc, code, label, group))
    if not hits:
        return {"code":"S4","label":"SAP"}
    hits.sort(key=lambda h: h[0], reverse=True)
    non_cross = [h for h in hits if h[3] != "cross"]
    primary = non_cross[0] if non_cross else hits[0]
    return {"code": primary[1], "label": primary[2]}

# ---------------- corptocorp scraper ----------------
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
PHONE_RE = re.compile(r"(?:\+?1[\s.\-]?)?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}")
GENERIC = {"info","hr","careers","jobs","support","noreply","no-reply","admin",
           "recruiting","talent","contact","resumes","unsubscribe"}
NAME_RE = re.compile(r"(?:regards|thanks|this is|reaching out|i am|i'm)[,\s&]*\n*\**\s*"
                     r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})", re.I)

def clean_phone(raw):
    d = re.sub(r"\D","",raw)
    if len(d)==11 and d.startswith("1"): d=d[1:]
    return f"({d[:3]}) {d[3:6]}-{d[6:]}" if len(d)==10 else ""

def extract_contact(text):
    emails = [e.rstrip(".,;:") for e in EMAIL_RE.findall(text)]
    emails = [e for e in emails if "corptocorp" not in e and "zoniac" not in e
              and "sierraconsult" not in e and "example" not in e]
    personal = next((e for e in emails if e.split("@")[0].lower() not in GENERIC), "")
    email = personal or (emails[0] if emails else "")
    phone = ""
    for p in PHONE_RE.findall(text):
        c = clean_phone(p)
        if c: phone=c; break
    name = ""
    m = NAME_RE.search(text)
    if m: name = m.group(1).strip()
    company = ""
    if email and "@" in email:
        dom = email.split("@")[-1].split(".")[0]
        if dom not in {"gmail","yahoo","outlook","hotmail"}:
            company = dom.capitalize()
    return {"name":name, "email":email, "phone":phone, "company":company}

POSTING_SKIP = ("/category/","/tag/","/author/","/page/","/states-jobs","/about",
                "/contact","/privacy","/write-for-us","/post-","/hotlist","/c2c-jobs",
                "/usa-jobs","/recruitment","/talent-acquisition","/boolean")

def search_postings(query, limit=10):
    url = "https://corptocorp.org/?s=" + requests.utils.quote(query)
    r = requests.get(url, headers=HEADERS, timeout=20)
    soup = BeautifulSoup(r.text, "html.parser")
    seen, out = set(), []
    for a in soup.select("h2 a, h3 a"):
        href = a.get("href","")
        title = a.get_text(strip=True)
        if not href.startswith("https://corptocorp.org/"): continue
        if any(s in href for s in POSTING_SKIP): continue
        if href.rstrip("/") == "https://corptocorp.org": continue
        if href in seen or not title: continue
        seen.add(href); out.append((title, href))
        if len(out) >= limit: break
    return out

def fetch_posting(title, url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        soup = BeautifulSoup(r.text, "html.parser")
        # main article text
        art = soup.find("article") or soup.find("main") or soup
        text = art.get_text("\n", strip=True)
        contact = extract_contact(text)
        # posted date
        mdate = soup.find("meta", {"property":"og:updated_time"}) or \
                soup.find("meta", {"property":"article:published_time"})
        posted = mdate["content"][:10] if mdate and mdate.get("content") else ""
        # location guess
        loc = ""
        ml = re.search(r"Location[:\s]+([A-Za-z .,/&()-]{3,40})", text)
        if ml: loc = ml.group(1).strip().rstrip(".")
        return {"title":title, "url":url, "posted":posted, "location":loc, **contact}
    except Exception:
        return None

def find_jobs_with_contacts(resume):
    mod = detect_module(resume)
    if not mod:
        return {"isSAP":False, "jobs":[]}
    posts = search_postings(QUERY.get(mod["code"],"sap"), limit=10)
    jobs = []
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        for res in ex.map(lambda p: fetch_posting(*p), posts):
            if res and (res["email"] or res["phone"]):
                jobs.append(res)
    return {"isSAP":True, "module":mod["label"], "jobs":jobs}

# ---------------- web UI ----------------
PAGE = """<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SAP Contract Finder</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@600;700&family=Source+Sans+3:ital,wght@0,400;0,600;1,400&display=swap" rel="stylesheet">
<style>
:root{--rust:#8B3A1F;--bistre:#2E2620;--walnut:#6B5F50;--stone:#8B8273;--stone20:rgba(139,130,115,.22);--stone10:rgba(139,130,115,.10);--rust08:rgba(139,58,31,.08);
 --head:'Inter','Segoe UI',system-ui,sans-serif;--body:'Source Sans 3','Segoe UI',system-ui,sans-serif}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:var(--body);color:var(--bistre);background:#fff;line-height:1.55;padding:22px;max-width:1100px;margin:0 auto}
h1,h2,h3,h4{font-family:var(--head);line-height:1.2}
.top{display:flex;align-items:center;gap:11px;margin-bottom:6px}
.logo{width:32px;height:32px;border-radius:8px;background:var(--rust);display:grid;place-items:center;color:#fff;font-family:var(--head);font-weight:700}
.top h1{font-size:19px;color:var(--rust)}
.sub{color:var(--walnut);font-size:14px;margin:0 0 16px 43px}
textarea{width:100%;min-height:140px;border:1px solid var(--stone);border-radius:10px;padding:12px;font:inherit;font-size:13.5px;resize:vertical}
.row{display:flex;gap:10px;align-items:center;margin-top:12px;flex-wrap:wrap}
.btn{font-family:var(--head);font-weight:600;background:var(--rust);color:#fff;border:none;border-radius:9px;padding:11px 20px;cursor:pointer;font-size:14px;text-decoration:none;display:inline-block}
.btn:hover{background:#73301a}.btn.ghost{background:transparent;color:var(--rust);border:1px solid var(--stone)}
.note{font-size:12px;color:var(--walnut);margin-top:8px}
.status{font-size:13px;color:var(--rust);font-weight:600;margin-top:12px;min-height:18px}
.focus{font-size:16px;margin:18px 0 4px}.focus b{color:var(--rust)}
.split{display:grid;grid-template-columns:1fr 1fr;gap:18px;align-items:start;margin-top:8px}
@media(max-width:820px){.split{grid-template-columns:1fr}}
.list{display:flex;flex-direction:column;gap:10px;max-height:70vh;overflow:auto}
.card{border:1px solid var(--stone20);border-radius:11px;padding:13px 15px;cursor:pointer;background:#fff}
.card:hover{border-color:var(--stone)}.card.active{border-color:var(--rust);box-shadow:0 0 0 1px var(--rust)}
.card h3{font-size:15px;margin-bottom:4px}
.meta{font-size:12.5px;color:var(--walnut);margin-top:2px}
.who{font-size:13px;color:var(--bistre);margin-top:6px}
.badges{display:flex;gap:6px;margin-top:8px;flex-wrap:wrap}
.b{font-size:10.5px;border-radius:5px;padding:2px 7px;border:1px solid var(--stone20);color:var(--walnut)}
.b.c2c{color:var(--rust);border-color:var(--rust)}.b.has{background:var(--stone10);color:var(--bistre)}
.detail{border:1px solid var(--stone20);border-radius:12px;position:sticky;top:10px;max-height:72vh;overflow:auto}
.detail .empty{padding:50px 24px;text-align:center;color:var(--walnut)}
.dhead{padding:18px 20px 14px;border-bottom:1px solid var(--stone20)}
.dhead h2{font-size:18px;margin-bottom:5px}.dhead .co{font-size:13.5px;color:var(--walnut)}
.applyrow{margin-top:12px}
.rec{margin:14px 20px 18px;border:1px solid var(--stone20);border-radius:10px;overflow:hidden}
.rec .rh{background:var(--rust);color:#fff;font-family:var(--head);font-weight:600;font-size:12.5px;padding:8px 14px}
.rec .rb{padding:13px 14px}.rec .name{font-family:var(--head);font-weight:700;font-size:15.5px}.rec .firm{font-size:12.5px;color:var(--walnut);margin-bottom:10px}
.contact{display:flex;align-items:center;gap:9px;margin:6px 0;font-size:13.5px}.contact .lbl{color:var(--walnut);width:48px;font-size:12px}.contact a{color:var(--rust)}
.copy{margin-left:auto;font-family:var(--head);font-size:10.5px;font-weight:600;border:1px solid var(--stone);background:#fff;color:var(--bistre);border-radius:6px;padding:3px 8px;cursor:pointer}
.dbody{padding:16px 20px}.dbody p{font-size:13.5px}
.empty{color:var(--walnut);font-style:italic;padding:14px 0}
</style></head><body>
<div class="top"><div class="logo">S</div><h1>SAP Contract Finder</h1></div>
<div class="sub">Paste your resume - it detects your SAP module, finds matching C2C jobs, and gives you the recruiter's name, email &amp; phone.</div>
<textarea id="resume" placeholder="Paste your full resume text here..."></textarea>
<div class="row">
  <button class="btn" onclick="run()">Find matched jobs + recruiter contacts</button>
  <button class="btn ghost" onclick="sample()">Try a sample EWM resume</button>
</div>
<div class="note">Live recruiter contacts from corptocorp.org C2C postings. Verify before reaching out.</div>
<div class="status" id="status"></div>
<div id="app"></div>

<script>
const SAMPLE = "Senior SAP EWM Consultant, 12+ years SAP supply chain. Extended Warehouse Management (EWM) design and config, RF framework, wave and task management on S/4HANA. Integration with MM, SD, TM and LE. Multiple greenfield go-lives. Open to C2C / Corp to Corp.";
let JOBS = [];
function esc(s){return (s||'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]));}
function sample(){document.getElementById('resume').value=SAMPLE;run();}
function fmtDate(iso){if(!iso)return '';try{const d=new Date(iso);if(isNaN(d))return '';const days=Math.max(0,Math.round((Date.now()-d.getTime())/86400000));const lbl=days===0?'Today':days===1?'Yesterday':days+' days ago';return lbl+' · '+d.toLocaleDateString('en-US',{month:'short',day:'numeric'});}catch(e){return '';}}
async function run(){
  const resume=document.getElementById('resume').value.trim();
  const status=document.getElementById('status'), app=document.getElementById('app');
  app.innerHTML='';
  if(!resume){status.textContent='Paste a resume first.';return;}
  status.textContent='⏳ Detecting your SAP module and pulling live C2C postings...';
  try{
    const r=await fetch('/api/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({resume})});
    const d=await r.json();
    status.textContent='';
    if(!d.isSAP){app.innerHTML='<div class=\"empty\">This doesn\\'t look like an SAP resume - no SAP module detected.</div>';return;}
    JOBS=(d.jobs||[]).map((j,i)=>({id:i,...j}));
    let h='<div class=\"focus\">Best-fit focus: <b>'+esc(d.module)+'</b> · '+JOBS.length+' matched jobs with recruiter contacts</div>';
    if(!JOBS.length){h+='<div class=\"empty\">No postings with a direct contact right now. The hotlists refresh through the day - try again later.</div>';app.innerHTML=h;return;}
    h+='<div class=\"split\"><div class=\"list\" id=\"list\"></div><div class=\"detail\" id=\"detail\"><div class=\"empty\">👈 Select a job to see the recruiter and apply link.</div></div></div>';
    app.innerHTML=h;
    renderList();
  }catch(e){status.textContent='Something went wrong: '+e;}
}
function renderList(){
  document.getElementById('list').innerHTML=JOBS.map(j=>`
   <div class="card" data-id="${j.id}">
     <h3>${esc(j.title)}</h3>
     <div class="meta">${esc(j.location||'US / Remote')} ${j.posted?'· '+fmtDate(j.posted):''}</div>
     ${j.name?`<div class="who">👤 <b>${esc(j.name)}</b>${j.company?' · '+esc(j.company):''}</div>`:(j.company?`<div class="who">${esc(j.company)}</div>`:'')}
     <div class="badges"><span class="b c2c">C2C</span><span class="b">corptocorp</span>${j.email?'<span class="b has">✉ email</span>':''}${j.phone?'<span class="b has">☎ phone</span>':''}</div>
   </div>`).join('');
  document.querySelectorAll('.card').forEach(c=>c.onclick=()=>select(+c.dataset.id,c));
}
function select(id,el){
  document.querySelectorAll('.card').forEach(c=>c.classList.remove('active'));if(el)el.classList.add('active');
  const j=JOBS.find(x=>x.id===id);
  const rows=(j.email||j.phone)?`
     ${j.email?`<div class="contact"><span class="lbl">Email</span><a href="mailto:${esc(j.email)}">${esc(j.email)}</a><button class="copy" data-c="${esc(j.email)}">Copy</button></div>`:''}
     ${j.phone?`<div class="contact"><span class="lbl">Phone</span><a href="tel:${(j.phone||'').replace(/[^0-9+]/g,'')}">${esc(j.phone)}</a><button class="copy" data-c="${esc(j.phone)}">Copy</button></div>`:''}`:'<p style="font-size:13px;color:var(--walnut)">No direct contact in this posting - open it to apply.</p>';
  document.getElementById('detail').innerHTML=`
   <div class="dhead"><h2>${esc(j.title)}</h2>
     <div class="co">${esc(j.location||'US / Remote')} ${j.posted?'· Posted '+fmtDate(j.posted):''}</div>
     <div class="applyrow"><a class="btn" target="_blank" rel="noopener" href="${esc(j.url)}">Open posting / apply</a></div></div>
   <div class="rec"><div class="rh">Recruiter who posted this role</div><div class="rb">
     ${j.name?`<div class="name">${esc(j.name)}</div>`:''}<div class="firm">${esc(j.company||'')}${j.company?' · ':''}via corptocorp</div>
     ${rows}</div></div>
   <div class="dbody"><p style="color:var(--walnut)">Full job description is on the posting - click <b>Open posting / apply</b> above.</p></div>`;
  document.querySelectorAll('.copy').forEach(b=>b.onclick=e=>{e.stopPropagation();navigator.clipboard.writeText(b.dataset.c);b.textContent='Copied';setTimeout(()=>b.textContent='Copy',1200);});
}
</script></body></html>"""

@app.route("/")
def index():
    return render_template_string(PAGE)

@app.route("/api/search", methods=["POST"])
def api_search():
    resume = (request.get_json(force=True) or {}).get("resume","")
    return jsonify(find_jobs_with_contacts(resume))

@app.route("/api/config", methods=["GET"])
def api_config():
    # Lets the front-end show whether sending is wired up (never returns the secret).
    return jsonify({"ready": bool(GMAIL_USER and GMAIL_APP_PASSWORD),
                    "test_mode": TEST_MODE, "test_email": TEST_EMAIL,
                    "from": GMAIL_USER if GMAIL_USER else ""})

@app.route("/api/send", methods=["POST", "OPTIONS"])
def api_send():
    if request.method == "OPTIONS":
        return ("", 204)
    if not (GMAIL_USER and GMAIL_APP_PASSWORD):
        return jsonify({"ok": False,
            "error": "Sender not configured. Set GMAIL_USER and GMAIL_APP_PASSWORD env vars on the server."}), 400
    data        = request.get_json(force=True) or {}
    jobs        = data.get("jobs") or []
    threshold   = float(data.get("threshold", 75))
    subject_tpl = data.get("subject", "SAP {module} - {role} - available C2C")
    body_tpl    = data.get("body", "")
    resume_b64  = data.get("resume_b64", "")
    resume_name = data.get("resume_name", "resume.pdf")
    delay       = min(float(data.get("delay", 5)), 10)

    targets = [j for j in jobs if j.get("email") and float(j.get("match", 0)) >= threshold]
    results = []
    for i, j in enumerate(targets):
        real_to = j["email"]
        to_addr = TEST_EMAIL if TEST_MODE else real_to
        subject = fill_tpl(subject_tpl, j)
        body    = fill_tpl(body_tpl, j)
        if TEST_MODE:
            body = f"[TEST MODE - intended recipient: {real_to}]\n\n" + body
        try:
            send_one(to_addr, subject, body, resume_b64, resume_name)
            results.append({"to": real_to, "sent_to": to_addr, "ok": True})
        except Exception as e:
            results.append({"to": real_to, "ok": False, "error": str(e)})
        if i < len(targets) - 1 and delay > 0:
            time.sleep(delay)
    return jsonify({"ok": True, "test_mode": TEST_MODE,
                    "sent": sum(1 for r in results if r["ok"]),
                    "total": len(targets), "results": results})

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 8000))
    print(f"\n  SAP C2C Contact Finder running at:  http://localhost:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=False)
