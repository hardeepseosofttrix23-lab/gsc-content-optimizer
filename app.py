
import io
import re
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

st.set_page_config(page_title="GSC Content Optimizer", page_icon="📈", layout="wide")

# -----------------------------
# Styling
# -----------------------------
st.markdown("""
<style>
.block-container {max-width: 1450px; padding-top: 1.5rem;}
.hero {
    background: linear-gradient(135deg,#101827,#23395d);
    color:white; padding:30px 34px; border-radius:20px; margin-bottom:22px;
}
.hero h1 {font-size:42px; margin:0 0 8px 0;}
.hero p {font-size:17px; margin:0; color:#dbe5f4;}
.card {
    border:1px solid #e5e7eb; border-radius:14px; padding:18px;
    background:#fff; margin-bottom:12px;
}
.badge {display:inline-block; padding:4px 9px; border-radius:999px; font-size:12px; font-weight:700;}
.high {background:#fee2e2; color:#991b1b;}
.medium {background:#fef3c7; color:#92400e;}
.low {background:#dcfce7; color:#166534;}
.small {color:#667085; font-size:13px;}
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="hero">
<h1>📈 GSC Content Optimizer</h1>
<p>Upload Google Search Console data and get a prioritized page + keyword optimization plan — including what to change, why it matters, and how to implement it.</p>
</div>
""", unsafe_allow_html=True)

# -----------------------------
# Helpers
# -----------------------------
def norm_col(c):
    return re.sub(r'[^a-z0-9]+', '', str(c).strip().lower())

def find_col(df, names):
    mp = {norm_col(c): c for c in df.columns}
    for n in names:
        if norm_col(n) in mp:
            return mp[norm_col(n)]
    return None

def clean_num(series):
    return pd.to_numeric(
        series.astype(str).str.replace(',', '', regex=False).str.replace('%','', regex=False),
        errors='coerce'
    )

def normalize_url(u):
    if pd.isna(u):
        return ""
    u = str(u).strip()
    if not u:
        return ""
    if not re.match(r'^https?://', u, re.I):
        u = "https://" + u
    return u.rstrip('/')

def tokens(text):
    text = re.sub(r'https?://\S+', ' ', str(text).lower())
    return set(re.findall(r'[a-z0-9]{3,}', text))

STOP = set("""
the and for with from that this your you are our their into about what how why
near best top service services company official home page website in on of to a
an is it by at or as be can get more we us my me all
""".split())

LOCAL_TERMS = set("""
near me nearby local city county state area downtown location locations
miami broward palm beach fort lauderdale west palm beach doral tampa orlando
austin dallas houston chicago new york los angeles san francisco
""".split())

def keyword_tokens(q):
    return {x for x in tokens(q) if x not in STOP}

def is_local_query(q, locations):
    ql = str(q).lower()
    if any(x in ql for x in ["near me","nearby","local"]):
        return True
    locs = [x.strip().lower() for x in re.split(r'[,;\n]+', locations or "") if x.strip()]
    return any(x and x in ql for x in locs)

def intent_of(q):
    ql = str(q).lower()
    if any(x in ql for x in ["price","pricing","cost","quote","buy","hire","book","schedule","appointment"]):
        return "Commercial / Transactional"
    if any(x in ql for x in ["near me","nearby","in ","best ","top ","company","service","services"]):
        return "Local / Commercial"
    if any(x in ql for x in ["how ","what ","why ","guide","tips","vs ","difference","meaning"]):
        return "Informational"
    return "Commercial / Mixed"

def expected_ctr(position):
    # Conservative heuristic, intentionally not presented as a Google benchmark.
    if position <= 1: return .28
    if position <= 2: return .18
    if position <= 3: return .12
    if position <= 5: return .08
    if position <= 10: return .04
    if position <= 20: return .018
    return .008

def opportunity_score(row):
    imp = max(float(row.get("Impressions",0) or 0), 0)
    pos = float(row.get("Position", 100) or 100)
    ctr = float(row.get("CTR",0) or 0)
    if ctr > 1: ctr /= 100
    pos_score = max(0, min(1, (25-pos)/21))
    ctr_gap = max(0, min(1, (expected_ctr(pos)-ctr) / max(expected_ctr(pos), .001)))
    imp_score = min(1, (imp / 5000) ** .5)
    return round(100*(.48*imp_score + .32*pos_score + .20*ctr_gap),1)

def priority_label(score):
    if score >= 65: return "High"
    if score >= 40: return "Medium"
    return "Low"

def load_table(name, data):
    try:
        if name.lower().endswith(".csv"):
            return pd.read_csv(io.BytesIO(data))
        if name.lower().endswith((".xlsx",".xls")):
            return pd.read_excel(io.BytesIO(data))
    except Exception:
        return None
    return None

def read_upload(uploaded):
    files = {}
    if uploaded.name.lower().endswith(".zip"):
        with zipfile.ZipFile(uploaded) as z:
            for n in z.namelist():
                if n.endswith("/") or "__MACOSX" in n:
                    continue
                low = n.lower()
                if low.endswith((".csv",".xlsx",".xls")):
                    try:
                        files[n.split("/")[-1]] = z.read(n)
                    except Exception:
                        pass
    else:
        files[uploaded.name] = uploaded.getvalue()
    return files

def standardize(df, source_name=""):
    if df is None or df.empty:
        return None
    d = df.copy()
    q = find_col(d, ["query","queries","top queries","search query"])
    p = find_col(d, ["page","pages","top pages","url","landing page"])
    date = find_col(d, ["date","day"])
    clicks = find_col(d, ["clicks"])
    imp = find_col(d, ["impressions"])
    ctr = find_col(d, ["ctr"])
    pos = find_col(d, ["position","average position","avg position"])
    ren = {}
    for old,new in [(q,"Query"),(p,"Page"),(date,"Date"),(clicks,"Clicks"),(imp,"Impressions"),(ctr,"CTR"),(pos,"Position")]:
        if old: ren[old]=new
    d = d.rename(columns=ren)
    for c in ["Clicks","Impressions","CTR","Position"]:
        if c in d: d[c] = clean_num(d[c])
    if "CTR" in d and d["CTR"].dropna().max() <= 1:
        pass
    elif "CTR" in d:
        d["CTR"] = d["CTR"]/100
    if "Date" in d:
        d["Date"] = pd.to_datetime(d["Date"], errors="coerce")
    if "Page" in d:
        d["Page"] = d["Page"].map(normalize_url)
    d["_source"] = source_name
    return d

def aggregate(df):
    if df is None or df.empty: return df
    group = []
    for c in ["Query","Page"]:
        if c in df.columns: group.append(c)
    if not group: return df
    out = df.copy()
    if "Clicks" not in out: out["Clicks"]=0
    if "Impressions" not in out: out["Impressions"]=0
    if "Position" not in out: out["Position"]=None
    g = out.groupby(group, dropna=False, as_index=False).agg(
        Clicks=("Clicks","sum"),
        Impressions=("Impressions","sum"),
        Position=("Position","mean")
    )
    g["CTR"] = g["Clicks"]/g["Impressions"].replace(0,pd.NA)
    return g

@st.cache_data(show_spinner=False)
def crawl_url(url, timeout=12):
    url = normalize_url(url)
    result = {"url":url, "ok":False, "status":None, "title":"", "description":"",
              "h1":[],"h2":[],"text":"","canonical":"","robots":"","error":""}
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent":"GSC-Content-Optimizer/1.0"}, allow_redirects=True)
        result["status"] = r.status_code
        if r.status_code >= 400:
            result["error"] = f"HTTP {r.status_code}"
            return result
        soup = BeautifulSoup(r.text, "html.parser")
        result["ok"] = True
        result["url"] = r.url.rstrip("/")
        result["title"] = soup.title.get_text(" ", strip=True) if soup.title else ""
        md = soup.find("meta", attrs={"name":re.compile("^description$",re.I)})
        result["description"] = md.get("content","").strip() if md else ""
        result["h1"] = [x.get_text(" ",strip=True) for x in soup.find_all("h1")][:5]
        result["h2"] = [x.get_text(" ",strip=True) for x in soup.find_all("h2")][:20]
        can = soup.find("link", rel=lambda v: v and "canonical" in v)
        result["canonical"] = normalize_url(can.get("href","")) if can else ""
        rob = soup.find("meta", attrs={"name":re.compile("^robots$",re.I)})
        result["robots"] = rob.get("content","").strip().lower() if rob else ""
        for x in soup(["script","style","noscript","svg"]):
            x.decompose()
        result["text"] = re.sub(r'\s+',' ',soup.get_text(" ", strip=True))[:30000]
    except Exception as e:
        result["error"] = str(e)[:180]
    return result

def relevance(query, page):
    qt = keyword_tokens(query)
    if not qt: return 0
    text = " ".join([page.get("title",""), " ".join(page.get("h1",[])), " ".join(page.get("h2",[])), page.get("text","")[:12000]]).lower()
    pt = tokens(text)
    if not pt: return 0
    overlap = len(qt & pt)/len(qt)
    title_h1 = tokens(page.get("title","")+" "+" ".join(page.get("h1",[])))
    head_overlap = len(qt & title_h1)/len(qt)
    return round(100*(.7*overlap+.3*head_overlap),1)

def suggestion(row, page, local_locations):
    q = str(row.get("Query","")).strip()
    qt = keyword_tokens(q)
    intent = intent_of(q)
    local = is_local_query(q, local_locations)
    title = page.get("title","")
    h1s = page.get("h1",[])
    h2s = page.get("h2",[])
    body = page.get("text","")
    body_tokens = tokens(body)
    missing = [x for x in qt if x not in body_tokens]
    title_missing = [x for x in qt if x not in tokens(title)]
    actions = []
    if title_missing:
        actions.append(f"Review the title tag so the primary topic ({q}) is represented naturally; do not force exact-match repetition.")
    if not h1s or not any(qt & tokens(h) for h in h1s):
        actions.append("Strengthen the H1 to clearly communicate the page's primary service/topic and search intent.")
    if missing:
        actions.append("Add useful coverage for the missing topic terms where they genuinely fit: " + ", ".join(sorted(missing)[:6]) + ".")
    if local:
        actions.append("Add genuinely useful local relevance: service + target area, service-area details, local proof/examples, and a clear location/service CTA. Avoid keyword stuffing.")
    if intent.startswith("Informational"):
        actions.append("Add a concise answer-first section and supporting FAQs that directly satisfy the informational intent.")
    else:
        actions.append("Strengthen commercial proof: services offered, process, differentiators, trust signals, FAQs, and a clear conversion CTA.")
    if len(h2s) < 3:
        actions.append("Expand the page structure with descriptive H2 sections covering the main subtopics users need before converting.")
    if page.get("canonical") and normalize_url(page.get("canonical")) != normalize_url(page.get("url")):
        actions.append("Canonical review required: the page declares a different canonical URL. Confirm this page is intended to rank before changing content.")
    if "noindex" in page.get("robots",""):
        actions.append("Indexing review required: robots meta contains noindex, so content optimization alone may not improve organic visibility.")
    return actions

# -----------------------------
# Sidebar
# -----------------------------
with st.sidebar:
    st.header("⚙️ Analysis Settings")
    upload = st.file_uploader("Upload GSC export", type=["zip","csv","xlsx","xls"],
                               help="ZIP is recommended. CSV/XLSX are also supported.")
    website = st.text_input("Website / GSC property", placeholder="https://example.com")
    locations = st.text_input("Target locations", placeholder="Miami, Broward County, Palm Beach")
    min_imp = st.number_input("Minimum impressions", min_value=0, value=100, step=50)
    max_opps = st.slider("Priority opportunities", 5, 100, 25)
    crawl_pages = st.slider("Pages to inspect automatically", 5, 75, 30)
    st.caption("No paid AI API is required. The optimizer uses GSC data + automatic page inspection + deterministic SEO rules.")

if not upload:
    st.info("Upload a GSC ZIP/CSV/XLSX to begin.")
    st.markdown("""
### What this version is designed to do
1. Detect the GSC date coverage.
2. Let you choose a custom period inside the uploaded data when date-level query data exists.
3. Identify high-value keywords instead of reporting every keyword.
4. Match keywords to the best pages automatically.
5. Inspect those pages without requiring you to manually read them.
6. Explain **why / what / how** to optimize.
7. Flag potential keyword cannibalization and canonical/indexing concerns.
8. Separate local-service opportunities from generic keywords.
""")
    st.stop()

# -----------------------------
# Load
# -----------------------------
raw_files = read_upload(upload)
tables = {}
for name, data in raw_files.items():
    t = standardize(load_table(name, data), name)
    if t is not None and not t.empty:
        tables[name] = t

if not tables:
    st.error("No readable CSV/XLSX files were found.")
    st.stop()

combined = None
for t in tables.values():
    if "Query" in t.columns and "Page" in t.columns and "Impressions" in t.columns:
        combined = t if combined is None else pd.concat([combined,t], ignore_index=True)

query_tables = [t for t in tables.values() if "Query" in t.columns and "Impressions" in t.columns]
page_tables = [t for t in tables.values() if "Page" in t.columns and "Impressions" in t.columns]
date_tables = [t for t in tables.values() if "Date" in t.columns]

if combined is not None:
    data = combined.copy()
    data_mode = "Query + Page data available"
elif query_tables:
    data = pd.concat(query_tables, ignore_index=True)
    data_mode = "Query data available; page mapping will be inferred"
elif page_tables:
    data = pd.concat(page_tables, ignore_index=True)
    data_mode = "Page data available; query analysis unavailable"
else:
    # Fall back to any table with core metrics
    candidates = [t for t in tables.values() if "Impressions" in t.columns]
    data = pd.concat(candidates, ignore_index=True) if candidates else pd.DataFrame()
    data_mode = "Aggregate data only"

if data.empty:
    st.error("The uploaded files do not contain recognizable GSC performance columns.")
    st.stop()

data = aggregate(data)

# -----------------------------
# Date coverage / selector
# -----------------------------
all_dates = []
for t in date_tables:
    if "Date" in t:
        all_dates += list(t["Date"].dropna())
min_date = min(all_dates).date() if all_dates else None
max_date = max(all_dates).date() if all_dates else None

st.subheader("📅 Analysis Period")
if min_date and max_date:
    st.success(f"GSC date coverage detected: **{min_date} → {max_date}** ({(max_date-min_date).days+1} days)")
    c1,c2 = st.columns([1,2])
    with c1:
        start = st.date_input("From", value=max(min_date, max_date-timedelta(days=6)), min_value=min_date, max_value=max_date)
    with c2:
        end = st.date_input("To", value=max_date, min_value=min_date, max_value=max_date)
    if start > end:
        st.error("Start date cannot be after end date.")
        st.stop()
    selected_days = (end-start).days+1
    st.caption(f"Selected period: {start} → {end} ({selected_days} days)")
    # Only filter query/page metrics if the actual metric rows have dates.
    if "Date" in data.columns:
        data = data[(data["Date"].dt.date >= start) & (data["Date"].dt.date <= end)].copy()
        data = aggregate(data)
    else:
        st.warning("The uploaded Query/Page tables are aggregated and do not contain a Date column. The selected dates can be used for daily trend context, but keyword-level metrics cannot be recalculated for that exact period. For true 1/2/5/7-day keyword analysis, upload a GSC export containing Date + Query + Page + Clicks + Impressions + CTR + Position.")
else:
    st.info("No date column was found in the uploaded Query/Page data. The tool will analyze the full exported period.")

# -----------------------------
# Overview
# -----------------------------
if "Clicks" not in data: data["Clicks"]=0
if "Impressions" not in data: data["Impressions"]=0
if "Position" not in data: data["Position"]=pd.NA
data["CTR"] = data["Clicks"]/data["Impressions"].replace(0,pd.NA)

c1,c2,c3,c4 = st.columns(4)
c1.metric("Clicks", f"{int(data['Clicks'].sum()):,}")
c2.metric("Impressions", f"{int(data['Impressions'].sum()):,}")
c3.metric("CTR", f"{(data['Clicks'].sum()/max(data['Impressions'].sum(),1))*100:.2f}%")
pos_vals = data["Position"].dropna()
c4.metric("Weighted avg. position", f"{(sum(data['Position'].fillna(0)*data['Impressions'])/max(data['Impressions'].sum(),1)):.1f}" if not pos_vals.empty else "N/A")

st.info(f"**Data mode:** {data_mode}. The tool intentionally prioritizes opportunities instead of dumping every keyword.")

# -----------------------------
# Build query opportunities
# -----------------------------
if "Query" not in data.columns:
    st.error("This export does not contain query-level data. Upload a GSC Query export (or a combined Query + Page export) for keyword optimization.")
    st.stop()

qdf = data.copy()
qdf["Query"] = qdf["Query"].astype(str).str.strip()
qdf = qdf[qdf["Query"].ne("") & qdf["Impressions"].ge(min_imp)].copy()

qdf = qdf.groupby("Query", as_index=False).agg(
    Clicks=("Clicks","sum"), Impressions=("Impressions","sum"), Position=("Position","mean")
)
qdf["CTR"] = qdf["Clicks"]/qdf["Impressions"].replace(0,pd.NA)
qdf["Opportunity Score"] = qdf.apply(opportunity_score, axis=1)
qdf["Priority"] = qdf["Opportunity Score"].map(priority_label)
qdf["Intent"] = qdf["Query"].map(intent_of)
qdf["Local"] = qdf["Query"].map(lambda x:is_local_query(x, locations))
qdf = qdf.sort_values(["Opportunity Score","Impressions"], ascending=False)

# Keep a balanced set: don't let 20 near-identical queries dominate.
selected = []
seen_stems = []
for _,r in qdf.iterrows():
    qt = keyword_tokens(r["Query"])
    if not qt: continue
    # skip near-duplicates if a stronger query already selected
    duplicate = any(len(qt & s)/max(len(qt|s),1) >= .82 for s in seen_stems)
    if duplicate: continue
    selected.append(r)
    seen_stems.append(qt)
    if len(selected) >= max_opps: break
opp = pd.DataFrame(selected)

st.subheader("🎯 Priority Content Optimization Plan")
if opp.empty:
    st.warning("No query met the minimum impression threshold. Lower the minimum impressions or use a larger GSC period.")
    st.stop()

# -----------------------------
# Page candidates
# -----------------------------
page_df = None
if "Page" in data.columns:
    page_df = data[data["Page"].astype(str).str.startswith("http", na=False)].copy()
    page_df = page_df.groupby("Page", as_index=False).agg(
        Page_Clicks=("Clicks","sum"), Page_Impressions=("Impressions","sum"), Page_Position=("Position","mean")
    )
else:
    # try page tables from ZIP
    if page_tables:
        pt = pd.concat(page_tables, ignore_index=True)
        pt = aggregate(pt)
        if "Page" in pt:
            page_df = pt.rename(columns={"Clicks":"Page_Clicks","Impressions":"Page_Impressions","Position":"Page_Position"})[["Page","Page_Clicks","Page_Impressions","Page_Position"]]

if page_df is None or page_df.empty:
    st.warning("No page URLs were found in the usable GSC data. Add page-level GSC export data for page-specific recommendations.")
    page_df = pd.DataFrame(columns=["Page","Page_Clicks","Page_Impressions","Page_Position"])

# If combined query+page data exists, use the actual winning page for each query.
actual_map = {}
if combined is not None and "Query" in combined.columns and "Page" in combined.columns:
    tmp = combined.copy()
    tmp = tmp[tmp["Impressions"].fillna(0) >= 1]
    for q,g in tmp.groupby("Query"):
        g = g.copy()
        g["score"] = g["Clicks"].fillna(0)*2 + g["Impressions"].fillna(0)/(g["Position"].fillna(100)+1)
        actual_map[str(q)] = g.sort_values("score", ascending=False).iloc[0]["Page"]

# Crawl candidate pages
candidate_urls = list(page_df.sort_values("Page_Impressions", ascending=False)["Page"].head(crawl_pages))
pages = {}
if website:
    # only same-domain URLs
    base_host = urlparse(normalize_url(website)).netloc.lower().replace("www.","")
    candidate_urls = [u for u in candidate_urls if urlparse(u).netloc.lower().replace("www.","") == base_host]
with st.spinner(f"Inspecting up to {len(candidate_urls)} pages automatically..."):
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(crawl_url,u):u for u in candidate_urls}
        for fut in as_completed(futs):
            r = fut.result()
            pages[r["url"]] = r

def best_page_for_query(q):
    # Actual query/page evidence wins.
    if str(q) in actual_map and actual_map[str(q)]:
        u = normalize_url(actual_map[str(q)])
        if u in pages: return u, 100.0, "GSC query+page evidence"
        if page_df["Page"].astype(str).eq(u).any(): return u, 100.0, "GSC query+page evidence"
    scores=[]
    for u,p in pages.items():
        scores.append((relevance(q,p),u))
    if scores:
        scores.sort(reverse=True)
        return scores[0][1], scores[0][0], "automatic page-content matching"
    return "",0,"no page crawl available"

rows=[]
for _,r in opp.iterrows():
    u,rel,method = best_page_for_query(r["Query"])
    p = pages.get(u,{})
    acts = suggestion(r,p,locations)
    row = r.to_dict()
    row.update({"Suggested Page":u,"Page Relevance":rel,"Mapping Method":method,
                "Recommendation":" ".join(acts[:3]),"Actions":acts,
                "Page Title":p.get("title",""),"Canonical":p.get("canonical",""),
                "Robots":p.get("robots","")})
    rows.append(row)
plan=pd.DataFrame(rows)

# -----------------------------
# Cannibalization
# -----------------------------
st.subheader("⚠️ Cannibalization & Technical Checks")
if combined is not None and "Page" in combined.columns:
    cg = combined[combined["Query"].notna() & combined["Page"].notna()].copy()
    counts = cg.groupby("Query")["Page"].nunique().sort_values(ascending=False)
    cann = counts[counts>1]
    if len(cann):
        st.warning(f"{len(cann)} queries have impressions across multiple pages in the uploaded Query + Page data. These are potential cannibalization cases, not automatic errors.")
        cann_tbl=[]
        for q,n in cann.head(20).items():
            urls = cg.loc[cg["Query"].eq(q),"Page"].dropna().unique().tolist()
            cann_tbl.append({"Query":q,"Pages":n,"Observed URLs":" | ".join(urls[:5]),"Action":"Choose one primary intent/page; consolidate or differentiate supporting pages before creating more content."})
        st.dataframe(pd.DataFrame(cann_tbl), use_container_width=True, hide_index=True)
    else:
        st.success("No multi-page query pattern was detected in the uploaded Query + Page data.")
else:
    st.info("Cannibalization can only be confirmed from Query + Page-level data. With separate Query.csv and Pages.csv, the tool avoids falsely declaring cannibalization.")

tech=[]
for _,r in plan.iterrows():
    if r["Suggested Page"]:
        if "noindex" in str(r["Robots"]).lower():
            tech.append({"Page":r["Suggested Page"],"Issue":"noindex","Action":"Review indexability before investing in content optimization."})
        if r["Canonical"] and normalize_url(r["Canonical"]) != normalize_url(r["Suggested Page"]):
            tech.append({"Page":r["Suggested Page"],"Issue":"Non-self canonical","Action":f"Canonical points to {r['Canonical']}; confirm the intended ranking URL."})
if tech:
    st.dataframe(pd.DataFrame(tech), use_container_width=True, hide_index=True)
else:
    st.success("No canonical/noindex warning was found on the automatically inspected priority pages.")

# -----------------------------
# Main table
# -----------------------------
st.subheader("🔎 Priority Opportunities")
display = plan[["Priority","Query","Intent","Local","Clicks","Impressions","CTR","Position","Opportunity Score","Suggested Page","Page Relevance","Mapping Method"]].copy()
display["CTR"] = display["CTR"].map(lambda x:f"{x*100:.2f}%" if pd.notna(x) else "0.00%")
display["Position"] = display["Position"].map(lambda x:f"{x:.1f}" if pd.notna(x) else "N/A")
st.dataframe(display, use_container_width=True, hide_index=True)

# -----------------------------
# Detailed cards
# -----------------------------
st.subheader("🛠️ Detailed Optimization Recommendations")
for i,r in plan.iterrows():
    badge = "high" if r["Priority"]=="High" else "medium" if r["Priority"]=="Medium" else "low"
    with st.expander(f"{r['Priority']} • {r['Query']} • {int(r['Impressions']):,} impressions • Position {r['Position']:.1f}"):
        c1,c2,c3,c4=st.columns(4)
        c1.metric("Clicks",f"{int(r['Clicks']):,}")
        c2.metric("Impressions",f"{int(r['Impressions']):,}")
        c3.metric("CTR",f"{r['CTR']*100:.2f}%")
        c4.metric("Opportunity",f"{r['Opportunity Score']:.1f}/100")
        st.markdown(f"**Priority:** <span class='badge {badge}'>{r['Priority']}</span>", unsafe_allow_html=True)
        st.markdown(f"**Intent:** {r['Intent']}  \n**Local opportunity:** {'Yes' if r['Local'] else 'No'}")
        st.markdown(f"**Recommended page:** `{r['Suggested Page'] or 'No suitable page found'}`")
        if r["Page Title"]: st.markdown(f"**Current title:** {r['Page Title']}")
        if r["Canonical"]: st.markdown(f"**Canonical:** {r['Canonical']}")
        st.markdown("### Why this keyword is prioritized")
        reasons=[]
        if r["Impressions"] >= min_imp: reasons.append(f"It has meaningful visibility ({int(r['Impressions']):,} impressions).")
        if 4 <= r["Position"] <= 20: reasons.append(f"Its average position ({r['Position']:.1f}) is within a range where stronger relevance/CTR can be worth testing.")
        if r["CTR"] < expected_ctr(r["Position"]): reasons.append("CTR is below the optimizer's conservative expected-CTR heuristic for its current position.")
        if r["Local"]: reasons.append("It shows local/commercial intent, so a relevant service/location page can be more valuable than a generic blog page.")
        for x in reasons: st.write("•",x)
        st.markdown("### What to change")
        for a in r["Actions"]: st.write("•",a)
        st.markdown("### How to implement")
        st.write("Keep the page focused on one primary search intent. Improve the title/H1/section structure and usefulness first; then strengthen internal links and conversion elements. Do not add keywords merely to increase keyword count.")
        st.markdown("### Avoid")
        st.write("• Do not create a new page if an existing page already serves the same intent.  • Do not force exact-match keywords repeatedly.  • Do not change a canonical simply to make a page rank without confirming the intended URL.")

# -----------------------------
# Export
# -----------------------------
st.subheader("📥 Export Optimization Plan")
export_cols = ["Priority","Query","Intent","Local","Clicks","Impressions","CTR","Position","Opportunity Score","Suggested Page","Page Relevance","Recommendation","Canonical","Robots"]
export_df = plan[export_cols].copy()
export_df["CTR"] = export_df["CTR"]*100
csv = export_df.to_csv(index=False).encode("utf-8")
st.download_button("Download prioritized optimization CSV", csv, "gsc-content-optimization-plan.csv", "text/csv")

# Optional full report workbook
try:
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        export_df.to_excel(writer, sheet_name="Optimization Plan", index=False)
        if combined is not None:
            combined.to_excel(writer, sheet_name="Query Page Data", index=False)
        if page_df is not None:
            page_df.to_excel(writer, sheet_name="Pages", index=False)
    st.download_button("Download Excel report", out.getvalue(), "gsc-content-optimization-plan.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
except Exception:
    pass

st.caption("Important: GSC data tells the tool what is already visible. Page inspection tells it what the current page contains. Recommendations are evidence-based heuristics, not a guarantee of ranking gains.")

