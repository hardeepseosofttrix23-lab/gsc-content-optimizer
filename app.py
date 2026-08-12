import io
import re
import zipfile
from datetime import timedelta

import numpy as np
import pandas as pd
import streamlit as st

st.set_page_config(page_title="GSC Content Optimizer", page_icon="📈", layout="wide")

st.markdown("""
<style>
.block-container{padding-top:1.5rem;padding-bottom:3rem}
.hero{padding:28px 34px;border-radius:18px;margin-bottom:24px;background:linear-gradient(135deg,#111827,#26364f);color:white}
.hero h1{font-size:2.4rem;margin:0 0 8px}.hero p{font-size:1.05rem;margin:0;opacity:.88}
.card{border:1px solid #e5e7eb;border-radius:14px;padding:18px;background:white;min-height:110px}
.metric-label{color:#6b7280;font-size:.86rem}.metric-value{font-size:1.65rem;font-weight:700;margin-top:4px}
</style>
""", unsafe_allow_html=True)

QUERY_COLS={"query","queries","search query","top queries"}
PAGE_COLS={"page","pages","url","top pages"}
LOCAL_WORDS={"near me","nearby","miami","orlando","tampa","fort lauderdale","broward","palm beach","west palm beach","new york","los angeles","chicago","houston","dallas","austin","phoenix","denver","san diego","san francisco","seattle","atlanta","boston","toronto","vancouver","calgary","london","manchester","birmingham","sydney","melbourne","brisbane"}
COMMERCIAL_WORDS={"buy","hire","book","service","services","company","agency","provider","cost","price","pricing","quote","quotation","best","professional","installation","repair","rental","rent","contractor","consultant"}
INFO_WORDS={"how","what","why","when","where","guide","tips","ideas","tutorial","meaning","definition","examples","vs","versus","checklist"}
BRAND_WORDS={"brand","branded","official","login","contact"}

def norm(x): return re.sub(r"\s+"," ",str(x).strip().lower())
def find_col(cols, candidates):
    m={norm(c):c for c in cols}
    for c in candidates:
        if c in m:return m[c]
    for n,o in m.items():
        if any(c in n or n in c for c in candidates):return o
    return None

def num(s):
    return pd.to_numeric(s.astype(str).str.replace(",","",regex=False).str.replace("%","",regex=False),errors="coerce")

def pct(s):
    if pd.api.types.is_numeric_dtype(s):
        x=pd.to_numeric(s,errors="coerce")
        if x.dropna().max()<=1.5:x=x*100
        return x
    return num(s)

def detect(df):
    cols=list(df.columns)
    q=find_col(cols,QUERY_COLS); p=find_col(cols,PAGE_COLS)
    d=find_col(cols,{"date"}); c=find_col(cols,{"clicks"})
    i=find_col(cols,{"impressions"}); t=find_col(cols,{"ctr","click through rate"})
    pos=find_col(cols,{"position","average position"})
    if q and c and i:return "query",{"query":q,"clicks":c,"impressions":i,"ctr":t,"position":pos,"date":d}
    if p and c and i:return "page",{"page":p,"clicks":c,"impressions":i,"ctr":t,"position":pos,"date":d}
    if d and c and i:return "daily",{"date":d,"clicks":c,"impressions":i,"ctr":t,"position":pos}
    return None,{}

def normalize(df,kind,m):
    o=pd.DataFrame()
    if kind=="query":o["query"]=df[m["query"]].astype(str).str.strip()
    if kind=="page":o["page"]=df[m["page"]].astype(str).str.strip()
    if kind=="daily":o["date"]=pd.to_datetime(df[m["date"]],errors="coerce").dt.date
    o["clicks"]=num(df[m["clicks"]]).fillna(0)
    o["impressions"]=num(df[m["impressions"]]).fillna(0)
    o["ctr"]=pct(df[m["ctr"]]).fillna(0) if m["ctr"] else (o["clicks"]/o["impressions"].replace(0,np.nan)*100).fillna(0)
    o["position"]=num(df[m["position"]]) if m["position"] else np.nan
    if m.get("date"):o["date"]=pd.to_datetime(df[m["date"]],errors="coerce").dt.date
    return o

def read_zip(file):
    found={}; skipped=[]
    with zipfile.ZipFile(file) as z:
        for name in z.namelist():
            if not name.lower().endswith(".csv"):continue
            try:
                df=pd.read_csv(io.BytesIO(z.read(name)))
                kind,m=detect(df)
                if kind:found.setdefault(kind,[]).append(normalize(df,kind,m))
            except Exception as e:skipped.append((name,str(e)))
    return {k:pd.concat(v,ignore_index=True) for k,v in found.items()},skipped

def aggregate(df,kind,start,end):
    if df.empty:return df
    if "date" in df.columns and df["date"].notna().any():
        df=df[df["date"].between(start,end)].copy()
    if df.empty:return df
    dim="query" if kind=="query" else "page"
    g=df.groupby(dim,dropna=False).agg(clicks=("clicks","sum"),impressions=("impressions","sum")).reset_index()
    g["ctr"]=np.where(g.impressions>0,g.clicks/g.impressions*100,0)
    if df["position"].notna().any():
        x=df.copy();x["w"]=x.impressions;x["wp"]=x.position.fillna(0)*x.w
        w=x.groupby(dim).agg(w=("w","sum"),wp=("wp","sum")).reset_index()
        w["position"]=np.where(w.w>0,w.wp/w.w,np.nan)
        g=g.merge(w[[dim,"position"]],on=dim,how="left")
    else:g["position"]=np.nan
    return g

def intent(q,locations):
    q=str(q).lower()
    loc=set(LOCAL_WORDS)
    for l in locations:
        loc.update(x.strip().lower() for x in re.split(r"[,/-]",l) if x.strip())
    local=any(x in q for x in loc)
    info=any(re.search(r"\b"+re.escape(x)+r"\b",q) for x in INFO_WORDS)
    comm=any(re.search(r"\b"+re.escape(x)+r"\b",q) for x in COMMERCIAL_WORDS)
    if local and (comm or not info):return "Local Commercial"
    if comm:return "Commercial"
    if info:return "Informational"
    return "Mixed / Other"

def tokens(x):return set(re.findall(r"[a-z0-9]+",str(x).lower()))
def slug_tokens(x):return tokens(re.sub(r"[-_/]+"," ",str(x)))
def brand(q,site):
    st=tokens(site)
    return bool(st and st&tokens(q)) or any(re.search(r"\b"+re.escape(x)+r"\b",str(q).lower()) for x in BRAND_WORDS)

def opportunities(qdf,pdf,locations,site,min_imp,n):
    q=qdf[qdf.impressions>=min_imp].copy()
    if q.empty:return q
    q["intent"]=q["query"].apply(lambda x:intent(x,locations))
    q["brand"]=q["query"].apply(lambda x:brand(x,site))
    q["suggested_page"]=np.nan
    if not pdf.empty:
        pages=pdf.sort_values("impressions",ascending=False).head(200)
        for ix,r in q.iterrows():
            qt=tokens(r["query"]);best=(0,None)
            for _,p in pages.iterrows():
                pt=slug_tokens(p["page"]);sim=len(qt&pt)/max(len(qt|pt),1)
                if sim>best[0]:best=(sim,p["page"])
            if best[0]>=.12:q.at[ix,"suggested_page"]=best[1]
    def score(r):
        s=0;pos=r.position
        if pd.notna(pos):
            if 3<=pos<=10:s+=35
            elif 10<pos<=20:s+=28
            elif 20<pos<=30:s+=15
            elif 1<=pos<3:s+=8
        s+=min(25,np.log10(max(r.impressions,10))*6)
        if pd.notna(pos) and pos<=10:
            expected=max(1,min(15,20/(pos+1)))
            if r.ctr<expected*.55:s+=20
            elif r.ctr<expected*.8:s+=10
        s+=15 if r.intent=="Local Commercial" else 12 if r.intent=="Commercial" else 4 if r.intent=="Informational" else 0
        if r.brand:s-=18
        return max(0,min(100,s))
    q["opportunity_score"]=q.apply(score,axis=1)
    def action(r):
        if r.brand:return "MONITOR / BRAND"
        if pd.notna(r.position) and r.position<3 and r.impressions>=min_imp*2:return "PROTECT"
        if pd.notna(r.position) and 3<=r.position<=20:
            return "OPTIMIZE — CTR + CONTENT" if r.ctr<2 and r.impressions>=min_imp*2 else "OPTIMIZE EXISTING PAGE"
        if pd.notna(r.position) and 20<r.position<=50:return "REVIEW — CONTENT DEPTH / INTENT"
        return "MONITOR"
    q["recommended_action"]=q.apply(action,axis=1)
    q["priority"]=pd.cut(q.opportunity_score,[-1,34,59,100],labels=["Low","Medium","High"]).astype(str)
    def why(r):
        b=[]
        if pd.notna(r.position) and 3<=r.position<=10:b.append("page-one ranking with room to reach the top 3")
        elif pd.notna(r.position) and 10<r.position<=20:b.append("near/page-two ranking opportunity")
        if r.impressions>0:b.append("meaningful search visibility")
        if r.intent in ("Local Commercial","Commercial"):b.append(r.intent.lower()+" intent")
        if pd.notna(r.position) and r.position<=10 and r.ctr<2:b.append("CTR appears weak for the visibility")
        return "; ".join(b) or "worth monitoring based on available GSC signals"
    q["reason"]=q.apply(why,axis=1)
    return q.sort_values(["opportunity_score","impressions"],ascending=False).head(n)

st.sidebar.title("⚙️ Analysis Settings")
uploaded=st.sidebar.file_uploader("Upload GSC ZIP",type=["zip"])
site=st.sidebar.text_input("Website / GSC property (optional)",placeholder="https://example.com")
loc_raw=st.sidebar.text_input("Target locations (optional)",placeholder="Miami, Broward County, Palm Beach")
locations=[x.strip() for x in loc_raw.split(",") if x.strip()]
min_imp=st.sidebar.number_input("Minimum impressions",min_value=0,value=100,step=25)
top_n=st.sidebar.slider("Priority opportunities",5,100,25)

st.markdown("""<div class="hero"><h1>📈 GSC Content Optimizer</h1><p>Find the pages and keywords most worth optimizing, explain why, and turn Search Console data into an actionable content plan.</p></div>""",unsafe_allow_html=True)

if not uploaded:
    st.info("Upload the GSC ZIP from the left sidebar to begin.")
    c=st.columns(4)
    for col,title,desc in zip(c,["🎯 Priority keywords","📄 Page decisions","📍 Local & commercial","⚠️ Cannibalization"],["Find realistic ranking opportunities.","Optimize, protect, review or monitor.","Prioritize local-service intent.","Only flag signals supported by the data."]):
        col.markdown(f'<div class="card"><b>{title}</b><p>{desc}</p></div>',unsafe_allow_html=True)
    st.stop()

try:data,skipped=read_zip(uploaded)
except Exception as e:st.error(f"Could not read ZIP: {e}");st.stop()
if skipped:
    with st.expander(f"Files skipped ({len(skipped)})"):
        for n,e in skipped:st.write(f"**{n}** — {e}")
if not data:st.error("No recognizable GSC CSV files were found.");st.stop()

all_dates=[]
for df in data.values():
    if "date" in df.columns:all_dates += list(df.date.dropna())
if all_dates:
    min_d,max_d=min(all_dates),max(all_dates)
    st.subheader("📅 Analysis Period")
    st.success(f"GSC data detected: {min_d} → {max_d} ({(max_d-min_d).days+1} days)")
    mode=st.selectbox("Choose analysis range",["1 Day","2 Days","5 Days","7 Days","14 Days","30 Days","60 Days","90 Days","Custom"],index=3)
    if mode=="Custom":
        a,b=st.columns(2)
        start=a.date_input("Start date",value=max(min_d,max_d-timedelta(days=6)),min_value=min_d,max_value=max_d)
        end=b.date_input("End date",value=max_d,min_value=min_d,max_value=max_d)
    else:
        n=int(mode.split()[0]);end=max_d;start=max(min_d,end-timedelta(days=n-1))
    if start>end:st.error("Start date must be before end date.");st.stop()
    st.caption(f"Selected period: **{start} → {end}** ({(end-start).days+1} days)")
else:
    start=end=None
    st.warning("No Date column was detected. Date customization is unavailable for this export.")

qraw=data.get("query",pd.DataFrame()); praw=data.get("page",pd.DataFrame())
if qraw.empty:st.error("No query-level dataset was detected. Upload the standard GSC export containing query data.");st.stop()
qdf=aggregate(qraw,"query",start,end) if start else qraw
pdf=aggregate(praw,"page",start,end) if start and not praw.empty else praw
st.subheader("📊 Performance Overview")
clicks=int(qdf.clicks.sum());imps=int(qdf.impressions.sum());ctr=clicks/imps*100 if imps else 0
avg=np.average(qdf.position.dropna(),weights=qdf.loc[qdf.position.notna(),"impressions"]) if qdf.position.notna().any() else np.nan
for col,label,value in zip(st.columns(4),["Clicks","Impressions","CTR","Weighted avg. position"],[f"{clicks:,}",f"{imps:,}",f"{ctr:.2f}%",f"{avg:.1f}" if pd.notna(avg) else "N/A"]):
    col.markdown(f'<div class="card"><div class="metric-label">{label}</div><div class="metric-value">{value}</div></div>',unsafe_allow_html=True)

st.subheader("🎯 Priority Content Optimization Plan")
opp=opportunities(qdf,pdf,locations,site,min_imp,top_n)
if opp.empty:st.warning("No opportunities meet the minimum-impression threshold. Lower the threshold and analyze again.");st.stop()

s=st.columns(5)
for col,label,value in zip(s,["High priority","Medium","Protect","Local commercial","Review candidates"],[
    int((opp.priority=="High").sum()),int((opp.priority=="Medium").sum()),
    int((opp.recommended_action=="PROTECT").sum()),int((opp.intent=="Local Commercial").sum()),
    int(((opp.position>20)&(opp.position<=50)&opp.intent.isin(["Commercial","Local Commercial"])).sum())]):
    col.metric(label,value)

show=opp[["priority","query","suggested_page","intent","clicks","impressions","ctr","position","opportunity_score","recommended_action"]].copy()
show.columns=["Priority","Keyword","Suggested page","Intent","Clicks","Impressions","CTR %","Position","Opportunity score","Recommended action"]
show["CTR %"]=show["CTR %"].round(2);show["Position"]=show.Position.round(1);show["Opportunity score"]=show["Opportunity score"].round(0)
st.dataframe(show,use_container_width=True,hide_index=True)

st.subheader("🔎 Detailed Recommendations")
for i,(_,r) in enumerate(opp.iterrows(),1):
    icon="🔴" if r.priority=="High" else "🟠" if r.priority=="Medium" else "🟢"
    with st.expander(f"{icon} #{i} — {r['query']} — {r['recommended_action']}"):
        a,b,c,d=st.columns(4)
        a.metric("Impressions",f"{int(r.impressions):,}");b.metric("Clicks",f"{int(r.clicks):,}");c.metric("CTR",f"{r.ctr:.2f}%");d.metric("Position",f"{r.position:.1f}" if pd.notna(r.position) else "N/A")
        st.write(f"**Intent:** {r.intent}")
        st.write(f"**Why selected:** {r.reason}")
        st.write(f"**Suggested page:** {r.suggested_page if pd.notna(r.suggested_page) else 'Not reliably mapped from aggregate GSC files'}")
        if r.recommended_action=="PROTECT":
            st.success("Protect this page from unnecessary content changes. Monitor performance instead.")
        elif r.recommended_action.startswith("OPTIMIZE"):
            st.write("**Recommended changes:** Review title/meta alignment; strengthen the primary topic; expand missing supporting sections; improve H2/H3 coverage; add relevant internal links; add useful FAQs where appropriate.")
            if r.intent=="Local Commercial":st.info("Local-service note: strengthen genuine location/service relevance without keyword stuffing or duplicate city pages.")
        elif r.recommended_action.startswith("REVIEW"):
            st.write("**Recommended changes:** Check search intent, page relevance, topical depth, internal links and SERP alignment before making structural changes.")
        else:st.write("**Recommendation:** Monitor until enough visibility exists for a confident content decision.")

st.subheader("🆕 New Page Candidates")
new=opp[(opp.position.fillna(999)>20)&(opp.position.fillna(999)<=50)&opp.intent.isin(["Commercial","Local Commercial"])&(~opp.brand)]
if new.empty:
    st.success("No strong new-page candidate identified. V5 prefers improving a relevant existing page when possible.")
else:
    st.dataframe(new[["query","intent","impressions","clicks","ctr","position","reason"]].rename(columns={"query":"Keyword","intent":"Intent","impressions":"Impressions","clicks":"Clicks","ctr":"CTR %","position":"Position","reason":"Why"}),use_container_width=True,hide_index=True)
    st.caption("Candidates require manual URL/intent review before creating a new page.")

st.subheader("⚠️ Cannibalization Review")
st.info("Standard separate GSC Query and Pages aggregate CSVs do not preserve the query→page relationship. V5 therefore does not pretend it can confirm cannibalization from those files. Exact query+page data will unlock this feature properly.")

st.subheader("🧠 Content Optimization Rules")
st.markdown("""
- **Optimize existing page** when the page already matches the intent.
- **Protect** strong pages instead of rewriting them unnecessarily.
- **Improve CTR** when a page has strong visibility but weak click-through.
- **Expand content** around related topics and intent, not keyword stuffing.
- **Strengthen local relevance** only where the business genuinely serves the location.
- **Create a new page** only when intent is distinct and no existing URL should own it.
- **Never automatically canonicalize** based on keyword overlap alone.
""")

plan=[]
for _,r in opp.iterrows():
    plan.append({
        "Priority":r.priority,"Keyword":r.query,"Suggested Page":r.suggested_page if pd.notna(r.suggested_page) else "",
        "Intent":r.intent,"Clicks":r.clicks,"Impressions":r.impressions,"CTR %":r.ctr,
        "Position":r.position,"Opportunity Score":r.opportunity_score,"Action":r.recommended_action,
        "Why":r.reason
    })
st.subheader("⬇️ Export")
st.download_button("Download prioritized content optimization plan (CSV)",pd.DataFrame(plan).to_csv(index=False).encode(), "gsc_content_optimization_plan.csv","text/csv")

with st.expander("Data quality & limitations"):
    st.write("Detected datasets: "+", ".join(sorted(data.keys())))
    st.write("V5 identifies CSVs by their columns, not only by filename.")
    st.write("Separate Query and Pages aggregate exports may not contain exact query→URL relationships. V5 intentionally avoids false cannibalization claims.")
