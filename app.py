import io
import re
import zipfile
from datetime import date, timedelta

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup
import streamlit as st

# Google API
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

st.set_page_config(page_title="GSC Content Optimizer", page_icon="📈", layout="wide")

# ---------------------------
# Theme
# ---------------------------
st.markdown("""
<style>
.main .block-container {max-width:1450px;padding-top:1.2rem;}
.hero{padding:28px 32px;border-radius:18px;background:linear-gradient(135deg,#111827,#26354f);color:#fff;margin-bottom:20px}
.hero h1{font-size:42px;margin:0 0 8px}
.hero p{font-size:17px;margin:0;opacity:.9}
.badge{display:inline-block;padding:4px 9px;border-radius:999px;font-size:12px;font-weight:700}
.high{background:#fee2e2;color:#991b1b}.medium{background:#fef3c7;color:#92400e}.low{background:#d1fae5;color:#065f46}
.section{margin-top:18px}
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="hero">
<h1>📈 GSC Content Optimizer</h1>
<p>Find the keywords and pages most worth optimizing, explain why, and turn Search Console data into an actionable content plan.</p>
</div>
""", unsafe_allow_html=True)

# ---------------------------
# Constants / utility
# ---------------------------
GSC_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"

def norm_col(x):
    return re.sub(r"[^a-z0-9]", "", str(x).strip().lower())

def find_col(df, candidates):
    mapping = {norm_col(c): c for c in df.columns}
    for candidate in candidates:
        if norm_col(candidate) in mapping:
            return mapping[norm_col(candidate)]
    for c in df.columns:
        nc = norm_col(c)
        for candidate in candidates:
            if norm_col(candidate) in nc or nc in norm_col(candidate):
                return c
    return None

def parse_percent(s):
    if pd.api.types.is_numeric_dtype(s):
        v = pd.to_numeric(s, errors="coerce")
        return v/100 if v.dropna().median() > 1 else v
    return pd.to_numeric(s.astype(str).str.replace("%","",regex=False), errors="coerce")/100

def standardize(df):
    x=df.copy(); r={}
    for candidates,target in [
        (["query","queries","top queries","search query"],"Query"),
        (["page","pages","top pages","url"],"Page"),
        (["date"],"Date"),
        (["clicks"],"Clicks"),
        (["impressions"],"Impressions"),
        (["ctr"],"CTR"),
        (["position","avg position","average position"],"Position"),
    ]:
        c=find_col(x,candidates)
        if c:r[c]=target
    x=x.rename(columns=r)
    if "Date" in x:x["Date"]=pd.to_datetime(x["Date"],errors="coerce")
    for c in ["Clicks","Impressions","Position"]:
        if c in x:x[c]=pd.to_numeric(x[c],errors="coerce")
    if "CTR" in x:x["CTR"]=parse_percent(x["CTR"])
    return x

def classify_dataset(df):
    cols={norm_col(c) for c in df.columns}
    q=any(c=="query" or "topqueries" in c or "searchquery" in c for c in cols)
    p=any(c=="page" or "toppages" in c or c=="url" for c in cols)
    d="date" in cols
    m=any("clicks" in c for c in cols) and any("impressions" in c for c in cols)
    if q and p:return "query_page"
    if q:return "query"
    if p:return "page"
    if d and m:return "daily"
    return "other"

def read_gsc_zip(upload):
    datasets={}
    with zipfile.ZipFile(upload) as z:
        for name in z.namelist():
            if name.endswith("/") or not name.lower().endswith((".csv",".xlsx",".xls")): continue
            raw=z.read(name)
            try:
                df=pd.read_csv(io.BytesIO(raw)) if name.lower().endswith(".csv") else pd.read_excel(io.BytesIO(raw))
            except Exception:
                try: df=pd.read_csv(io.BytesIO(raw),encoding="latin-1")
                except Exception: continue
            if not df.empty:
                s=standardize(df)
                datasets[name]=(classify_dataset(s),s)
    return datasets

def ctr_benchmark(pos):
    # Conservative heuristic benchmark used only to identify opportunities.
    if pos <= 1.5:return .30
    if pos <= 3:return .12
    if pos <= 5:return .07
    if pos <= 10:return .035
    if pos <= 20:return .015
    if pos <= 30:return .008
    return .003

def intent_for_query(q, locations):
    q=str(q).lower().strip()
    local_terms=["near me","nearby","in my area","local","service area"]
    commercial=["hire","book","buy","service","services","company","agency","provider","cost","price","pricing","quote","appointment"]
    info=["how","what","why","when","guide","tips","ideas","checklist","vs ","versus","difference","meaning"]
    if any(x in q for x in local_terms) or any(loc and loc.lower() in q for loc in locations):
        return "Local Commercial"
    if any(x in q for x in commercial):return "Commercial"
    if any(x in q for x in info):return "Informational"
    return "Mixed / Unknown"

def clean_domain(url):
    if not url:return ""
    try:return re.sub(r"^www\.","",urlparse(url).netloc.lower())
    except:return ""

def brand_query(q, domain):
    host=clean_domain(domain)
    if not host:return False
    brand=host.split(".")[0]
    toks=set(re.findall(r"[a-z0-9]+",str(q).lower()))
    return brand in toks

def query_page_aggregate(df):
    if df.empty or not {"Query","Page","Clicks","Impressions","CTR","Position"}.issubset(df.columns):
        return pd.DataFrame()
    x=df.copy()
    x=x[x["Query"].notna() & x["Page"].notna()]
    x["Clicks"]=x["Clicks"].fillna(0);x["Impressions"]=x["Impressions"].fillna(0)
    g=x.groupby(["Query","Page"],as_index=False).agg(Clicks=("Clicks","sum"),Impressions=("Impressions","sum"))
    x["wpos"]=x["Position"].fillna(100)*x["Impressions"]
    wp=x.groupby(["Query","Page"],as_index=False)["wpos"].sum()
    g=g.merge(wp,on=["Query","Page"])
    g["Position"]=g["wpos"]/g["Impressions"].replace(0,np.nan)
    g["CTR"]=g["Clicks"]/g["Impressions"].replace(0,np.nan)
    return g.drop(columns=["wpos"])

def fetch_gsc(service, site, start, end, dims, row_limit=25000):
    rows=[]; start_row=0
    while True:
        body={"startDate":str(start),"endDate":str(end),"dimensions":dims,"rowLimit":row_limit,"startRow":start_row,"dataState":"final"}
        resp=service.searchanalytics().query(siteUrl=site,body=body).execute()
        batch=resp.get("rows",[])
        for r in batch:
            rec=dict(zip(dims,r.get("keys",[])))
            rec.update({k:r.get(k,0) for k in ["clicks","impressions","ctr","position"]})
            rows.append(rec)
        if len(batch)<row_limit:break
        start_row += row_limit
        if start_row >= 100000: break
    if not rows:return pd.DataFrame()
    x=pd.DataFrame(rows)
    rename={"query":"Query","page":"Page","date":"Date","clicks":"Clicks","impressions":"Impressions","ctr":"CTR","position":"Position"}
    x=x.rename(columns=rename)
    if "Date" in x:x["Date"]=pd.to_datetime(x["Date"],errors="coerce")
    return x

def score_row(r):
    imp=float(r["Impressions"]); pos=float(r["Position"]); ctr=float(r["CTR"])
    volume=min(1,np.log10(max(imp,1))/5)
    rank=1 if 4<=pos<=15 else .7 if 15<pos<=20 else .35 if 20<pos<=40 else .15 if pos<=3 else .05
    ctr_gap=max(0,ctr_benchmark(pos)-ctr)/max(ctr_benchmark(pos),.001)
    intent={"Local Commercial":1.0,"Commercial":.95,"Mixed / Unknown":.55,"Informational":.35}.get(r["Intent"],.35)
    trend=r.get("Trend",0)
    trend_factor=.5 if trend < -0.15 else .75 if trend < 0 else 1 if trend < .15 else 1.1
    return round(100*(.30*volume+.30*rank+.20*min(1,ctr_gap)+.15*intent+.05*min(1,trend_factor)),1)

def choose_action(r):
    p=r["Position"]; ctr=r["CTR"]; imp=r["Impressions"]
    if p<=3 and ctr>=ctr_benchmark(p)*.8:
        return "PROTECT"
    if 4<=p<=10 and ctr<ctr_benchmark(p)*.65:
        return "CTR + CONTENT"
    if 4<=p<=10:
        return "CONTENT REFRESH"
    if 10<p<=20:
        return "PUSH TO PAGE 1"
    if 20<p<=40 and imp>=200:
        return "RELEVANCE / CONTENT GAP"
    if r["Intent"] in ["Commercial","Local Commercial"] and imp>=100:
        return "REVIEW TARGET PAGE"
    return "MONITOR"

def recommendation_text(r):
    q=r["Query"]; p=r["Page"]; pos=r["Position"]; intent=r["Intent"]; ctr=r["CTR"]
    actions={
        "PROTECT":"Keep this page focused on its current intent. Avoid a major rewrite unless there is a verified content problem.",
        "CTR + CONTENT":"Review the title and meta description for stronger query alignment, then strengthen the opening copy and supporting sections around the query.",
        "CONTENT REFRESH":"Refresh the existing page rather than creating another URL. Improve topical depth, headings, internal links and intent alignment.",
        "PUSH TO PAGE 1":"Expand the page around the search intent, add missing supporting subtopics/entities, strengthen internal links and improve the main content's usefulness.",
        "RELEVANCE / CONTENT GAP":"First confirm this is the correct page for the query. If it is, expand the page substantially; if not, map the query to the correct existing service/location page.",
        "REVIEW TARGET PAGE":"This is a commercially useful query but the ranking is weak. Check whether the current URL is the best service/location landing page before creating anything new.",
        "MONITOR":"Low priority under the current filters. Do not spend optimization time here before higher-value opportunities."
    }
    return actions[r["Action"]]

def build_plan(qp, locations, domain, min_imp, limit):
    if qp.empty:return pd.DataFrame()
    x=qp.copy()
    x=x[x["Impressions"]>=min_imp].copy()
    x["Intent"]=x["Query"].apply(lambda q:intent_for_query(q,locations))
    x=x[~x["Query"].apply(lambda q:brand_query(q,domain))].copy()
    x["Action"]=x.apply(choose_action,axis=1)
    x["Opportunity Score"]=x.apply(score_row,axis=1)
    # dominant page per query: prevents a long list of duplicate query/page rows
    x["Page Share"]=x.groupby("Query")["Impressions"].transform(lambda s:s/max(s.sum(),1))
    x["Page Signal"]=np.where(x["Page Share"]>=.6,"Primary page",np.where(x["Page Share"]>=.15,"Secondary page","Weak secondary"))
    x=x.sort_values(["Opportunity Score","Impressions"],ascending=[False,False])
    # Keep the best page for each query for the primary action plan
    x=x.drop_duplicates("Query",keep="first")
    x["Priority"]=pd.cut(x["Opportunity Score"],[-1,45,65,101],labels=["Low","Medium","High"]).astype(str)
    x["Why"]=x.apply(lambda r:f"{r['Impressions']:,} impressions, position {r['Position']:.1f}, CTR {r['CTR']:.2%}, {r['Intent'].lower()} intent.",axis=1)
    x["What to do"]=x.apply(recommendation_text,axis=1)
    return x.head(limit)

def crawl_page(url, timeout=12):
    try:
        r=requests.get(url,timeout=timeout,headers={"User-Agent":"Mozilla/5.0 (compatible; GSCContentOptimizer/1.0)"})
        if r.status_code>=400:return {"URL":url,"Status":r.status_code,"Error":f"HTTP {r.status_code}"}
        soup=BeautifulSoup(r.text,"html.parser")
        title=(soup.title.get_text(" ",strip=True) if soup.title else "")
        h1=[h.get_text(" ",strip=True) for h in soup.find_all("h1")]
        hs=[h.get_text(" ",strip=True) for h in soup.find_all(["h2","h3"])]
        canon=""
        link=soup.find("link",rel=lambda v:v and "canonical" in v)
        if link:canon=link.get("href","")
        text=" ".join(soup.stripped_strings)
        return {"URL":url,"Status":r.status_code,"Title":title,"H1":" | ".join(h1),"H2/H3":" | ".join(hs[:30]),"Canonical":canon,"Words":len(text.split()),"Error":""}
    except Exception as e:
        return {"URL":url,"Status":"Error","Error":str(e)}

# ---------------------------
# Sidebar settings
# ---------------------------
with st.sidebar:
    st.header("⚙️ Analysis Settings")
    source=st.radio("Data source",["Connect GSC","Upload GSC ZIP"],index=0)
    st.divider()
    st.subheader("Business context")
    domain=st.text_input("Website / GSC property",placeholder="https://example.com")
    location_text=st.text_input("Target locations (comma-separated)",placeholder="Miami, Broward County, Palm Beach")
    locations=[x.strip() for x in location_text.split(",") if x.strip()]
    st.divider()
    min_imp=st.number_input("Minimum impressions",10,100000,100,step=10)
    limit=st.slider("Priority opportunities",10,50,25)
    st.divider()
    st.caption("The engine prioritizes opportunities; it does not claim that a change guarantees rankings.")

# ---------------------------
# Acquire data
# ---------------------------
qp=pd.DataFrame(); daily=pd.DataFrame(); page_df=pd.DataFrame(); query_df=pd.DataFrame()
available_sites=[]

if source=="Connect GSC":
    if not hasattr(st,"login") or "auth" not in st.secrets:
        st.error("Google login is not configured yet. Add the Streamlit [auth] secrets shown in the setup instructions below.")
        st.stop()
    if not st.user.is_logged_in:
        st.subheader("🔐 Connect Google Search Console")
        st.write("Sign in with the Google account that has access to the GSC property you want to analyze.")
        st.button("Connect with Google",on_click=st.login)
        st.info("The Google account used here can be different from the account that owns or deploys this Streamlit app.")
        st.stop()

    st.sidebar.success(f"Connected: {getattr(st.user,'email','Google account')}")
    if st.sidebar.button("Log out"):
        st.logout()

    try:
        access_token=st.user.tokens["access"]
        creds=Credentials(token=access_token,scopes=[GSC_SCOPE])
        service=build("searchconsole","v1",credentials=creds,cache_discovery=False)
        sites=service.sites().list().execute().get("siteEntry",[])
        available_sites=[s.get("siteUrl") for s in sites if s.get("permissionLevel") in ["siteOwner","siteFullUser","siteRestrictedUser"]]
    except Exception as e:
        st.error("Could not access Search Console with the authorized account.")
        st.code(str(e))
        st.stop()

    if not available_sites:
        st.warning("No Search Console properties are available to this Google account.")
        st.stop()

    selected_site=st.selectbox("GSC property",available_sites)
    domain=selected_site
    today=date.today()
    # Search Console data can lag; use a conservative end date.
    api_end=today-timedelta(days=3)
    api_start=api_end-timedelta(days=89)

    range_options={"Last 1 day":1,"Last 2 days":2,"Last 5 days":5,"Last 7 days":7,"Last 14 days":14,"Last 30 days":30,"Last 60 days":60,"Last 90 days":90}
    period=st.selectbox("Analysis period",list(range_options)+["Custom range"],index=5)
    if period=="Custom range":
        c1,c2=st.columns(2)
        start=c1.date_input("Start",api_start,min_value=date(2018,1,1),max_value=api_end)
        end=c2.date_input("End",api_end,min_value=start,max_value=api_end)
    else:
        end=api_end
        start=end-timedelta(days=range_options[period]-1)
    st.caption(f"Selected: {start} → {end} ({(end-start).days+1} days)")

    with st.spinner("Pulling GSC query + page data..."):
        raw=fetch_gsc(service,selected_site,start,end,["query","page"],25000)
        daily=fetch_gsc(service,selected_site,start,end,["date"],25000)

    if not raw.empty:
        qp=query_page_aggregate(raw)
    if not daily.empty and "Date" in daily:
        daily["Date"]=pd.to_datetime(daily["Date"])

else:
    upload=st.file_uploader("Upload GSC ZIP",type=["zip"])
    if upload:
        datasets=read_gsc_zip(upload)
        st.success(f"ZIP processed. Detected {len(datasets)} readable file(s).")
        with st.expander("Detected GSC files"):
            st.write({n:k for n,(k,_) in datasets.items()})
        for _,(kind,df) in datasets.items():
            if kind=="query_page" and qp.empty: qp=df
            elif kind=="query" and query_df.empty: query_df=df
            elif kind=="page" and page_df.empty: page_df=df
            elif kind=="daily" and daily.empty: daily=df
        if not qp.empty:qp=query_page_aggregate(qp)
        if daily.empty and query_df.empty:
            st.warning("No usable Query or Daily dataset was found.")
        if not qp.empty:
            st.info("Query + Page data detected. Exact date filtering is available only when Date is also present in the same dataset; otherwise use GSC API mode.")

# ---------------------------
# Overview
# ---------------------------
st.subheader("📊 Performance Overview")
if not daily.empty and {"Clicks","Impressions"}.issubset(daily.columns):
    clicks=int(daily["Clicks"].sum()); imps=int(daily["Impressions"].sum()); ctr=clicks/imps if imps else 0
    pos=np.nan
    if "Position" in daily and daily["Position"].notna().any():
        valid=daily["Position"].notna()
        pos=np.average(daily.loc[valid,"Position"],weights=daily.loc[valid,"Impressions"])
    a,b,c,d=st.columns(4)
    a.metric("Clicks",f"{clicks:,}");b.metric("Impressions",f"{imps:,}");c.metric("CTR",f"{ctr:.2%}");d.metric("Avg. position","N/A" if np.isnan(pos) else f"{pos:.1f}")
else:
    st.info("Daily chart data is not available in this dataset.")

# ---------------------------
# Main optimization plan
# ---------------------------
st.subheader("🎯 Priority Content Optimization Plan")

if qp.empty and not query_df.empty:
    # Query-only fallback
    q=query_df.copy()
    if {"Query","Clicks","Impressions","CTR","Position"}.issubset(q.columns):
        q["Page"]="Page mapping unavailable"
        qp=q[["Query","Page","Clicks","Impressions","CTR","Position"]]
        plan=build_plan(qp,locations,domain,min_imp,limit)
        if not plan.empty:
            st.warning("This ZIP has keyword data but not reliable query→page mapping. Use Connect GSC for page-specific recommendations.")
        else: st.success("No priority opportunities found under current filters.")
    else:
        plan=pd.DataFrame()
elif not qp.empty:
    plan=build_plan(qp,locations,domain,min_imp,limit)
else:
    plan=pd.DataFrame()

if not plan.empty:
    view=plan[["Priority","Query","Intent","Page","Impressions","Clicks","CTR","Position","Opportunity Score","Action","Page Signal"]].copy()
    view["CTR"]=view["CTR"].map(lambda x:f"{x:.2%}")
    view["Position"]=view["Position"].map(lambda x:f"{x:.1f}")
    st.dataframe(view,use_container_width=True,hide_index=True)
    st.download_button("⬇️ Download optimization plan CSV",plan.to_csv(index=False).encode("utf-8"),"gsc-optimization-plan.csv","text/csv")

    st.markdown("### 🔎 Detailed recommendations")
    for i,r in plan.iterrows():
        css="high" if r["Priority"]=="High" else "medium" if r["Priority"]=="Medium" else "low"
        st.markdown(f"<span class='badge {css}'>{r['Priority']} PRIORITY</span> **{r['Query']}**",unsafe_allow_html=True)
        c1,c2,c3,c4=st.columns(4)
        c1.metric("Impressions",f"{int(r['Impressions']):,}")
        c2.metric("Clicks",f"{int(r['Clicks']):,}")
        c3.metric("CTR",f"{r['CTR']:.2%}")
        c4.metric("Position",f"{r['Position']:.1f}")
        st.write(f"**Recommended page:** `{r['Page']}`")
        st.write(f"**Intent:** {r['Intent']}")
        st.write(f"**Why selected:** {r['Why']}")
        st.write(f"**Action:** {r['Action']}")
        st.write(f"**What to change:** {r['What to do']}")
        if r["Action"]=="PROTECT":
            st.write("**Do not:** create another page or make a large rewrite without evidence of a problem.")
        else:
            st.write("**Do not:** create a new URL for this query until the current ranking URL has been checked for intent, relevance and cannibalization.")
        st.divider()
else:
    st.info("No priority plan yet. Connect GSC or upload a ZIP containing usable Query data.")

# ---------------------------
# Page performance + cannibalization
# ---------------------------
st.subheader("📄 Page Opportunities & Cannibalization")

if not qp.empty:
    page=qp.groupby("Page",as_index=False).agg(Clicks=("Clicks","sum"),Impressions=("Impressions","sum"))
    page["CTR"]=page["Clicks"]/page["Impressions"].replace(0,np.nan)
    tmp=qp.copy()
    tmp["wpos"]=tmp["Position"]*tmp["Impressions"]
    wp=tmp.groupby("Page",as_index=False)["wpos"].sum()
    page=page.merge(wp,on="Page");page["Position"]=page["wpos"]/page["Impressions"].replace(0,np.nan)
    page=page.drop(columns="wpos").sort_values("Impressions",ascending=False)
    st.dataframe(page.head(limit).assign(CTR=lambda x:x["CTR"].map(lambda v:f"{v:.2%}"),Position=lambda x:x["Position"].map(lambda v:f"{v:.1f}")),use_container_width=True,hide_index=True)

    c=qp.copy()
    c["Share"]=c.groupby("Query")["Impressions"].transform(lambda s:s/max(s.sum(),1))
    signals=c[(c["Share"]>=.15)&(c["Share"]<.85)].sort_values("Impressions",ascending=False)
    st.markdown("#### Potential multi-URL query signals")
    if signals.empty:
        st.success("No strong multi-URL signals found in the current data.")
    else:
        st.warning("These are signals for review, not automatic cannibalization verdicts.")
        st.dataframe(signals[["Query","Page","Impressions","Clicks","Position","Share"]].head(100).assign(Share=lambda x:x["Share"].map(lambda v:f"{v:.1%}")),use_container_width=True,hide_index=True)
else:
    st.info("Query + Page data is required for reliable page mapping and cannibalization signals.")

# ---------------------------
# Optional live page audit
# ---------------------------
st.subheader("🧪 Optional Page Content Check")
st.caption("This checks the selected pages' HTML signals. It does not replace a full technical audit.")

if not plan.empty:
    pages=plan["Page"].dropna().astype(str).tolist()
    selected=st.multiselect("Select pages to inspect",pages,max_selections=10)
    if st.button("Check selected pages"):
        results=[]
        for u in selected:
            if not u.startswith("http"):
                st.warning(f"Skipped non-URL: {u}")
                continue
            results.append(crawl_page(u))
        if results:
            st.dataframe(pd.DataFrame(results),use_container_width=True,hide_index=True)
            st.info("Use these checks to validate whether title/H1/headings/canonical/word count support the GSC recommendation.")

# ---------------------------
# Data quality
# ---------------------------
st.subheader("🔍 Data Quality")
if source=="Connect GSC":
    st.success("HIGH confidence for the selected GSC API range when Query + Page and Date data are returned.")
    st.caption("Search Console API returns top rows and does not guarantee every possible query/page row. Treat the plan as a prioritized opportunity set, not a complete inventory.")
else:
    st.caption("ZIP confidence depends on which dimensions are present. Separate Queries.csv and Pages.csv cannot prove which page ranks for a particular query.")
