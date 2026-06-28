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
import re, html, concurrent.futures as cf
from flask import Flask, request, jsonify, render_template_string
import requests
from bs4 import BeautifulSoup

app = Flask(__name__)
HEADERS = {"User-Agent": "Mozilla/5.0 (SAP-contact-finder; personal use)"}

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
PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SAP C2C Contact Finder</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@600;700&family=Source+Sans+3:wght@400;600&display=swap" rel="stylesheet">
<style>
:root{--rust:#8B3A1F;--bistre:#2E2620;--walnut:#6B5F50;--stone:#8B8273;--stone20:rgba(139,130,115,.22);--rust08:rgba(139,58,31,.08)}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Source Sans 3',system-ui,sans-serif;color:var(--bistre);background:#fff;line-height:1.55;padding:24px;max-width:900px;margin:0 auto}
h1,h2,h3{font-family:'Inter',system-ui,sans-serif}
h1{color:var(--rust);font-size:22px;margin-bottom:4px}
.sub{color:var(--walnut);font-size:14px;margin-bottom:18px}
textarea{width:100%;min-height:150px;border:1px solid var(--stone);border-radius:10px;padding:12px;font:inherit;font-size:13.5px}
.btn{font-family:'Inter';font-weight:600;background:var(--rust);color:#fff;border:none;border-radius:9px;padding:11px 20px;cursor:pointer;font-size:14px;margin-top:12px}
.btn:hover{background:#73301a}
.focus{margin:18px 0 8px;font-size:15px}.focus b{color:var(--rust)}
.card{border:1px solid var(--stone20);border-radius:11px;padding:15px 17px;margin-bottom:12px}
.card h3{font-size:16px;margin-bottom:4px}
.meta{font-size:13px;color:var(--walnut);margin-bottom:10px}
.rec{background:var(--rust08);border-left:3px solid var(--rust);border-radius:6px;padding:10px 12px}
.rec .nm{font-family:'Inter';font-weight:700;font-size:14.5px}
.row{margin:4px 0;font-size:14px}.row a{color:var(--rust)}.lbl{color:var(--walnut);display:inline-block;width:54px;font-size:12.5px}
.empty{color:var(--walnut);font-style:italic;padding:14px 0}
.loading{color:var(--rust);font-weight:600}
.note{font-size:12px;color:var(--walnut);margin-top:8px}
</style></head><body>
<h1>SAP C2C Contact Finder</h1>
<div class="sub">Paste your resume — detects your SAP module, finds matching C2C jobs, and pulls the recruiter's email & phone.</div>
<textarea id="resume" placeholder="Paste your full resume text here…"></textarea>
<button class="btn" onclick="run()">Find matched jobs + recruiter contacts →</button>
<div class="note">Free layer: live from corptocorp.org C2C postings. Contacts are extracted from each posting — verify before use.</div>
<div id="out" style="margin-top:20px"></div>
<script>
async function run(){
  const resume=document.getElementById('resume').value.trim();
  const out=document.getElementById('out');
  if(!resume){out.innerHTML='<div class="empty">Paste a resume first.</div>';return;}
  out.innerHTML='<div class="loading">⏳ Detecting module and scraping live C2C postings…</div>';
  try{
    const r=await fetch('/api/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({resume})});
    const d=await r.json();
    if(!d.isSAP){out.innerHTML='<div class="empty">This doesn\\'t look like an SAP resume — no SAP module detected.</div>';return;}
    let h='<div class="focus">Detected focus: <b>'+d.module+'</b> · '+d.jobs.length+' matched jobs with contacts</div>';
    if(!d.jobs.length){h+='<div class="empty">No postings with extractable contacts right now. Try again later — the hotlists refresh through the day.</div>';}
    for(const j of d.jobs){
      h+='<div class="card"><h3>'+esc(j.title)+'</h3>'+
         '<div class="meta">'+(j.location?esc(j.location)+' · ':'')+(j.posted?'Posted '+j.posted:'')+'</div>'+
         '<div class="rec">'+(j.name?'<div class="nm">'+esc(j.name)+(j.company?' · '+esc(j.company):'')+'</div>':(j.company?'<div class="nm">'+esc(j.company)+'</div>':''))+
         (j.email?'<div class="row"><span class="lbl">Email</span><a href="mailto:'+esc(j.email)+'">'+esc(j.email)+'</a></div>':'')+
         (j.phone?'<div class="row"><span class="lbl">Phone</span><a href="tel:'+j.phone.replace(/[^0-9]/g,'')+'">'+esc(j.phone)+'</a></div>':'')+
         '<div class="row"><span class="lbl">Source</span><a href="'+esc(j.url)+'" target="_blank">open posting</a></div>'+
         '</div></div>';
    }
    out.innerHTML=h;
  }catch(e){out.innerHTML='<div class="empty">Error: '+e+'</div>';}
}
function esc(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
</script></body></html>"""

@app.route("/")
def index():
    return render_template_string(PAGE)

@app.route("/api/search", methods=["POST"])
def api_search():
    resume = (request.get_json(force=True) or {}).get("resume","")
    return jsonify(find_jobs_with_contacts(resume))

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 8000))
    print(f"\n  SAP C2C Contact Finder running at:  http://localhost:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=False)
