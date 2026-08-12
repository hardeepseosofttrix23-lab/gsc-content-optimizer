import streamlit as st
import pandas as pd
import numpy as np
import zipfile
import io
import re
from urllib.parse import urlparse
from datetime import timedelta

# ============================================================
# PAGE CONFIG
# ============================================================

st.set_page_config(
    page_title="GSC Content Optimizer",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ============================================================
# CUSTOM CSS
# ============================================================

st.markdown("""
<style>

.main {
    background-color: #f7f8fa;
}

.block-container {
    padding-top: 2rem;
    padding-bottom: 4rem;
}

.hero {
    padding: 30px;
    border-radius: 18px;
    background: linear-gradient(135deg, #111827, #263449);
    color: white;
    margin-bottom: 25px;
}

.hero h1 {
    font-size: 42px;
    margin-bottom: 8px;
}

.hero p {
    color: #d1d5db;
    font-size: 17px;
}

.metric-card {
    background: white;
    border: 1px solid #e5e7eb;
    border-radius: 14px;
    padding: 20px;
    min-height: 125px;
}

.metric-title {
    font-size: 13px;
    color: #6b7280;
}

.metric-value {
    font-size: 30px;
    font-weight: 700;
    margin-top: 8px;
}

.priority-high {
    background: #fee2e2;
    color: #991b1b;
    padding: 5px 10px;
    border-radius: 20px;
    font-weight: 600;
}

.priority-medium {
    background: #fef3c7;
    color: #92400e;
    padding: 5px 10px;
    border-radius: 20px;
    font-weight: 600;
}

.priority-low {
    background: #dcfce7;
    color: #166534;
    padding: 5px 10px;
    border-radius: 20px;
    font-weight: 600;
}

.section-title {
    font-size: 26px;
    font-weight: 700;
    margin-top: 30px;
    margin-bottom: 15px;
}

.info-box {
    background: #eff6ff;
    border-left: 5px solid #2563eb;
    padding: 15px;
    border-radius: 8px;
    margin: 10px 0;
}

.warning-box {
    background: #fff7ed;
    border-left: 5px solid #f97316;
    padding: 15px;
    border-radius: 8px;
    margin: 10px 0;
}

</style>
""", unsafe_allow_html=True)


# ============================================================
# HELPERS
# ============================================================

def normalize_columns(df):
    df.columns = [
        str(c).strip().lower().replace(" ", "_")
        for c in df.columns
    ]
    return df


def find_column(df, possible_names):
    for name in possible_names:
        if name in df.columns:
            return name
    return None


def clean_numeric(series):
    if series is None:
        return pd.Series(dtype=float)

    return (
        series.astype(str)
        .str.replace("%", "", regex=False)
        .str.replace(",", "", regex=False)
        .str.strip()
        .replace(["-", "", "nan", "None"], np.nan)
        .astype(float)
    )


def calculate_ctr(clicks, impressions):
    if impressions == 0:
        return 0
    return (clicks / impressions) * 100


def safe_position(value):
    try:
        return float(value)
    except:
        return np.nan


def format_number(value):
    try:
        return f"{value:,.0f}"
    except:
        return "0"


def score_priority(row):
    """
    Rule-based SEO opportunity score.

    Higher score = stronger optimization opportunity.
    """

    impressions = row.get("impressions", 0)
    clicks = row.get("clicks", 0)
    ctr = row.get("ctr", 0)
    position = row.get("position", 100)

    score = 0

    # --------------------------------------------------------
    # Impression opportunity
    # --------------------------------------------------------

    if impressions >= 10000:
        score += 25
    elif impressions >= 5000:
        score += 22
    elif impressions >= 2000:
        score += 18
    elif impressions >= 1000:
        score += 14
    elif impressions >= 500:
        score += 9
    elif impressions >= 100:
        score += 5

    # --------------------------------------------------------
    # Ranking opportunity
    # --------------------------------------------------------

    if 4 <= position <= 10:
        score += 30
    elif 11 <= position <= 20:
        score += 25
    elif 21 <= position <= 30:
        score += 12
    elif 1 <= position < 4:
        score += 5

    # --------------------------------------------------------
    # CTR opportunity
    # --------------------------------------------------------

    if 4 <= position <= 10:
        expected_ctr = 8
    elif 11 <= position <= 20:
        expected_ctr = 4
    else:
        expected_ctr = 2

    if ctr < expected_ctr * 0.5:
        score += 20
    elif ctr < expected_ctr * 0.75:
        score += 12
    elif ctr < expected_ctr:
        score += 6

    # --------------------------------------------------------
    # Existing clicks
    # --------------------------------------------------------

    if clicks >= 500:
        score += 10
    elif clicks >= 100:
        score += 7
    elif clicks >= 25:
        score += 4

    # --------------------------------------------------------
    # Avoid optimizing already excellent keywords
    # --------------------------------------------------------

    if position <= 3 and ctr >= expected_ctr:
        score -= 25

    return max(0, min(100, score))


def classify_opportunity(row):

    position = row["position"]
    impressions = row["impressions"]
    ctr = row["ctr"]

    if position <= 3 and impressions >= 500:
        return "PROTECT"

    if 4 <= position <= 10 and impressions >= 500:
        return "STRIKING DISTANCE"

    if 11 <= position <= 20 and impressions >= 500:
        return "PAGE 2 OPPORTUNITY"

    if position <= 10 and impressions >= 500 and ctr < 2:
        return "CTR OPPORTUNITY"

    if impressions >= 2000 and position > 20:
        return "CONTENT OPPORTUNITY"

    return "LOW PRIORITY"


def recommendation(row):

    position = row["position"]
    ctr = row["ctr"]
    impressions = row["impressions"]

    if position <= 3:
        return (
            "Protect the existing page. Avoid major content changes. "
            "Focus on internal linking, freshness and monitoring."
        )

    if 4 <= position <= 10:
        if ctr < 3:
            return (
                "Prioritize the existing ranking page. Review title, "
                "meta description, search intent alignment, H1/H2 structure "
                "and expand missing topical coverage."
            )

        return (
            "Optimize the existing ranking page. Improve topical depth, "
            "search intent alignment, internal links and supporting sections."
        )

    if 11 <= position <= 20:
        return (
            "Refresh the existing page before creating a new page. "
            "Expand topical coverage, improve headings, strengthen internal "
            "links and answer missing search-intent questions."
        )

    if position > 20 and impressions >= 2000:
        return (
            "Investigate the ranking page. Check relevance and content depth "
            "before deciding whether to refresh the existing URL or create "
            "a dedicated page."
        )

    return "Low priority. Monitor before making major content changes."


# ============================================================
# ZIP READER
# ============================================================

def read_zip(uploaded_file):

    files = {}

    with zipfile.ZipFile(uploaded_file) as z:

        for name in z.namelist():

            if name.lower().endswith(".csv"):

                try:

                    data = z.read(name)

                    df = pd.read_csv(
                        io.BytesIO(data),
                        encoding="utf-8",
                        on_bad_lines="skip"
                    )

                    files[name.split("/")[-1]] = normalize_columns(df)

                except Exception:
                    pass

    return files


# ============================================================
# DATE ANALYSIS
# ============================================================

def analyze_chart(chart):

    if chart is None:
        return None

    date_col = find_column(
        chart,
        ["date"]
    )

    if not date_col:
        return None

    chart = chart.copy()

    chart[date_col] = pd.to_datetime(
        chart[date_col],
        errors="coerce"
    )

    chart = chart.dropna(subset=[date_col])

    click_col = find_column(chart, ["clicks"])
    impression_col = find_column(chart, ["impressions"])

    if click_col:
        chart[click_col] = clean_numeric(chart[click_col])

    if impression_col:
        chart[impression_col] = clean_numeric(chart[impression_col])

    return chart


# ============================================================
# FIND QUERY DATA
# ============================================================

def prepare_queries(df):

    if df is None:
        return None

    q = df.copy()

    query_col = find_column(
        q,
        ["query", "queries", "keyword"]
    )

    clicks_col = find_column(
        q,
        ["clicks"]
    )

    impressions_col = find_column(
        q,
        ["impressions"]
    )

    ctr_col = find_column(
        q,
        ["ctr"]
    )

    position_col = find_column(
        q,
        ["position", "average_position"]
    )

    if not query_col:
        return None

    q = q.rename(
        columns={
            query_col: "query",
            clicks_col: "clicks" if clicks_col else query_col,
            impressions_col: "impressions" if impressions_col else query_col,
            ctr_col: "ctr" if ctr_col else query_col,
            position_col: "position" if position_col else query_col,
        }
    )

    if "clicks" not in q.columns:
        q["clicks"] = 0

    if "impressions" not in q.columns:
        q["impressions"] = 0

    if "ctr" not in q.columns:
        q["ctr"] = 0

    if "position" not in q.columns:
        q["position"] = 100

    q["clicks"] = clean_numeric(q["clicks"])
    q["impressions"] = clean_numeric(q["impressions"])
    q["ctr"] = clean_numeric(q["ctr"])
    q["position"] = clean_numeric(q["position"])

    q = q.dropna(subset=["query"])

    q["query"] = q["query"].astype(str).str.strip()

    q = q[q["query"] != ""]

    return q


# ============================================================
# FIND PAGE DATA
# ============================================================

def prepare_pages(df):

    if df is None:
        return None

    p = df.copy()

    page_col = find_column(
        p,
        ["top_pages", "page", "url", "pages"]
    )

    clicks_col = find_column(p, ["clicks"])
    impressions_col = find_column(p, ["impressions"])
    ctr_col = find_column(p, ["ctr"])
    position_col = find_column(p, ["position", "average_position"])

    if not page_col:
        return None

    rename = {
        page_col: "page"
    }

    if clicks_col:
        rename[clicks_col] = "clicks"

    if impressions_col:
        rename[impressions_col] = "impressions"

    if ctr_col:
        rename[ctr_col] = "ctr"

    if position_col:
        rename[position_col] = "position"

    p = p.rename(columns=rename)

    for col in ["clicks", "impressions", "ctr", "position"]:
        if col not in p.columns:
            p[col] = 0

    p["clicks"] = clean_numeric(p["clicks"])
    p["impressions"] = clean_numeric(p["impressions"])
    p["ctr"] = clean_numeric(p["ctr"])
    p["position"] = clean_numeric(p["position"])

    return p


# ============================================================
# DETECT DAILY QUERY DATA
# ============================================================

def detect_daily_query_data(files):

    for name, df in files.items():

        if "query" not in " ".join(df.columns):
            continue

        date_col = find_column(df, ["date"])

        query_col = find_column(
            df,
            ["query", "queries", "keyword"]
        )

        if date_col and query_col:
            return df

    return None


# ============================================================
# APP
# ============================================================

st.markdown("""
<div class="hero">

<h1>📈 GSC Content Optimizer</h1>

<p>
Turn Google Search Console data into a prioritized SEO content
optimization plan — not a list of thousands of keywords.
</p>

</div>
""", unsafe_allow_html=True)


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:

    st.header("⚙️ Analysis Settings")

    st.write(
        "Upload a GSC ZIP exported from Google Search Console."
    )

    uploaded = st.file_uploader(
        "Upload GSC ZIP",
        type=["zip"]
    )

    st.divider()

    st.subheader("Opportunity Settings")

    max_opportunities = st.slider(
        "Number of opportunities",
        min_value=5,
        max_value=50,
        value=15,
        step=5
    )

    min_impressions = st.number_input(
        "Minimum impressions",
        min_value=0,
        value=100,
        step=50
    )


# ============================================================
# NO DATA
# ============================================================

if not uploaded:

    st.info(
        "Upload your GSC ZIP from the sidebar to begin the analysis."
    )

    st.markdown("""
### What this tool will identify

🎯 **Striking-distance keywords**

Keywords ranking around positions 4–10.

📈 **Page 2 opportunities**

Keywords ranking positions 11–20 with meaningful impressions.

🎯 **CTR opportunities**

Keywords receiving impressions but unusually low CTR.

🛡️ **Protect keywords**

Keywords already performing strongly.

📄 **Page recommendations**

Which existing page should be optimized.

⚠️ **Cannibalization**

Potential query/page competition when Query + Page data is available.

📅 **Date analysis**

Analyze different date ranges when daily query data is available.
""")

    st.stop()


# ============================================================
# READ DATA
# ============================================================

try:

    files = read_zip(uploaded)

except Exception as e:

    st.error(f"Could not read ZIP: {e}")
    st.stop()


# ============================================================
# IDENTIFY FILES
# ============================================================

query_file = None
page_file = None
chart_file = None

for name, df in files.items():

    name_lower = name.lower()

    if "quer" in name_lower:
        query_file = df

    if "page" in name_lower:
        page_file = df

    if "chart" in name_lower:
        chart_file = df


queries = prepare_queries(query_file)
pages = prepare_pages(page_file)
chart = analyze_chart(chart_file)

daily_query = detect_daily_query_data(files)


# ============================================================
# DATE RANGE
# ============================================================

st.markdown(
    '<div class="section-title">📅 Analysis Period</div>',
    unsafe_allow_html=True
)

date_min = None
date_max = None

if chart is not None:

    date_col = find_column(chart, ["date"])

    if date_col:

        date_min = chart[date_col].min().date()
        date_max = chart[date_col].max().date()


if date_min and date_max:

    total_days = (date_max - date_min).days + 1

    st.success(
        f"GSC data detected: **{date_min} → {date_max}** "
        f"({total_days} days)"
    )

    range_type = st.selectbox(
        "Choose analysis range",
        [
            "Full available period",
            "Last 1 day",
            "Last 2 days",
            "Last 5 days",
            "Last 7 days",
            "Last 14 days",
            "Last 30 days",
            "Last 60 days",
            "Custom range"
        ]
    )

    if range_type == "Full available period":

        start_date = date_min
        end_date = date_max

    elif range_type.startswith("Last"):

        days = int(
            re.search(
                r"\d+",
                range_type
            ).group()
        )

        end_date = date_max
        start_date = max(
            date_min,
            date_max - timedelta(days=days - 1)
        )

    else:

        col1, col2 = st.columns(2)

        with col1:

            start_date = st.date_input(
                "Start date",
                value=date_min,
                min_value=date_min,
                max_value=date_max
            )

        with col2:

            end_date = st.date_input(
                "End date",
                value=date_max,
                min_value=date_min,
                max_value=date_max
            )

else:

    st.warning(
        "No daily Chart.csv date data was detected. "
        "The tool will analyze the available aggregate GSC data."
    )

    start_date = None
    end_date = None


# ============================================================
# DATE VALIDATION
# ============================================================

if start_date and end_date:

    selected_days = (
        end_date - start_date
    ).days + 1

    st.caption(
        f"Selected period: **{start_date} → {end_date}** "
        f"({selected_days} days)"
    )


# ============================================================
# DAILY DATA ANALYSIS
# ============================================================

if chart is not None and start_date and end_date:

    date_col = find_column(chart, ["date"])

    selected_chart = chart[
        (
            chart[date_col].dt.date >= start_date
        )
        &
        (
            chart[date_col].dt.date <= end_date
        )
    ].copy()

else:

    selected_chart = chart


# ============================================================
# OVERVIEW METRICS
# ============================================================

st.markdown(
    '<div class="section-title">📊 Performance Overview</div>',
    unsafe_allow_html=True
)

if selected_chart is not None:

    clicks_col = find_column(
        selected_chart,
        ["clicks"]
    )

    impressions_col = find_column(
        selected_chart,
        ["impressions"]
    )

    total_clicks = (
        selected_chart[clicks_col].sum()
        if clicks_col else 0
    )

    total_impressions = (
        selected_chart[impressions_col].sum()
        if impressions_col else 0
    )

    total_ctr = calculate_ctr(
        total_clicks,
        total_impressions
    )

else:

    total_clicks = (
        queries["clicks"].sum()
        if queries is not None else 0
    )

    total_impressions = (
        queries["impressions"].sum()
        if queries is not None else 0
    )

    total_ctr = calculate_ctr(
        total_clicks,
        total_impressions
    )


metric1, metric2, metric3, metric4 = st.columns(4)

with metric1:
    st.metric(
        "Clicks",
        format_number(total_clicks)
    )

with metric2:
    st.metric(
        "Impressions",
        format_number(total_impressions)
    )

with metric3:
    st.metric(
        "CTR",
        f"{total_ctr:.2f}%"
    )

with metric4:

    if queries is not None:

        avg_position = queries["position"].mean()

        st.metric(
            "Avg. Position",
            f"{avg_position:.1f}"
        )

    else:

        st.metric(
            "Avg. Position",
            "N/A"
        )


# ============================================================
# DATA CONFIDENCE
# ============================================================

st.markdown(
    '<div class="section-title">🔎 Data Quality</div>',
    unsafe_allow_html=True
)

col1, col2, col3, col4 = st.columns(4)

with col1:

    st.metric(
        "Query data",
        "Available" if queries is not None else "Missing"
    )

with col2:

    st.metric(
        "Page data",
        "Available" if pages is not None else "Missing"
    )

with col3:

    st.metric(
        "Daily query data",
        "Available" if daily_query is not None else "Not available"
    )

with col4:

    if date_min and date_max:

        period_days = (
            date_max - date_min
        ).days + 1

        if period_days >= 28:
            confidence = "HIGH"
        elif period_days >= 14:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

    else:

        confidence = "MEDIUM"

    st.metric(
        "Analysis confidence",
        confidence
    )


if daily_query is None:

    st.markdown("""
<div class="warning-box">

<b>Important:</b> Your current GSC ZIP contains aggregate query data.
The tool can filter the site's daily performance using Chart.csv,
but it cannot accurately recalculate keyword-level performance for
a custom 1/2/5/7-day period.

For true date-level keyword analysis, upload data containing:

<b>Date + Query + Page + Clicks + Impressions + CTR + Position</b>.

</div>
""", unsafe_allow_html=True)


# ============================================================
# KEYWORD ANALYSIS
# ============================================================

if queries is None:

    st.error(
        "Queries.csv could not be detected."
    )

    st.stop()


q = queries.copy()

q = q[
    q["impressions"] >= min_impressions
].copy()

q["opportunity_score"] = q.apply(
    score_priority,
    axis=1
)

q["opportunity_type"] = q.apply(
    classify_opportunity,
    axis=1
)

q["recommendation"] = q.apply(
    recommendation,
    axis=1
)


# ============================================================
# REMOVE LOW PRIORITY
# ============================================================

priority_order = {
    "STRIKING DISTANCE": 1,
    "PAGE 2 OPPORTUNITY": 2,
    "CTR OPPORTUNITY": 3,
    "CONTENT OPPORTUNITY": 4,
    "PROTECT": 5,
    "LOW PRIORITY": 6
}

q["priority_order"] = q[
    "opportunity_type"
].map(priority_order)


opportunities = q[
    q["opportunity_type"] != "LOW PRIORITY"
].sort_values(
    [
        "priority_order",
        "opportunity_score",
        "impressions"
    ],
    ascending=[True, False, False]
).head(max_opportunities)


# ============================================================
# SUMMARY COUNTS
# ============================================================

st.markdown(
    '<div class="section-title">🎯 Opportunity Summary</div>',
    unsafe_allow_html=True
)

counts = q["opportunity_type"].value_counts()

c1, c2, c3, c4 = st.columns(4)

with c1:
    st.metric(
        "🚀 Striking Distance",
        int(counts.get("STRIKING DISTANCE", 0))
    )

with c2:
    st.metric(
        "📄 Page 2",
        int(counts.get("PAGE 2 OPPORTUNITY", 0))
    )

with c3:
    st.metric(
        "🎯 CTR",
        int(counts.get("CTR OPPORTUNITY", 0))
    )

with c4:
    st.metric(
        "🛡️ Protect",
        int(counts.get("PROTECT", 0))
    )


# ============================================================
# TOP OPPORTUNITIES
# ============================================================

st.markdown(
    '<div class="section-title">🔥 Top Optimization Opportunities</div>',
    unsafe_allow_html=True
)

if opportunities.empty:

    st.info(
        "No significant optimization opportunities were found "
        "using the current thresholds."
    )

else:

    display = opportunities[
        [
            "query",
            "clicks",
            "impressions",
            "ctr",
            "position",
            "opportunity_type",
            "opportunity_score"
        ]
    ].copy()

    display.columns = [
        "Keyword",
        "Clicks",
        "Impressions",
        "CTR %",
        "Position",
        "Opportunity",
        "Score"
    ]

    st.dataframe(
        display,
        use_container_width=True,
        hide_index=True
    )


# ============================================================
# DETAILED ANALYSIS
# ============================================================

st.markdown(
    '<div class="section-title">🔬 Detailed Recommendations</div>',
    unsafe_allow_html=True
)

for index, row in opportunities.iterrows():

    keyword = row["query"]

    score = int(
        row["opportunity_score"]
    )

    opportunity = row[
        "opportunity_type"
    ]

    if opportunity == "STRIKING DISTANCE":
        badge = "🔴 HIGH PRIORITY"

    elif opportunity == "PAGE 2 OPPORTUNITY":
        badge = "🟠 MEDIUM-HIGH PRIORITY"

    elif opportunity == "CTR OPPORTUNITY":
        badge = "🟡 CTR PRIORITY"

    elif opportunity == "PROTECT":
        badge = "🟢 PROTECT"

    else:
        badge = "🔵 OPPORTUNITY"

    with st.expander(
        f"{badge}  |  {keyword}  |  Score {score}/100"
    ):

        col1, col2, col3, col4 = st.columns(4)

        with col1:
            st.metric(
                "Position",
                f"{row['position']:.1f}"
            )

        with col2:
            st.metric(
                "Impressions",
                format_number(row["impressions"])
            )

        with col3:
            st.metric(
                "Clicks",
                format_number(row["clicks"])
            )

        with col4:
            st.metric(
                "CTR",
                f"{row['ctr']:.2f}%"
            )

        st.markdown("### What is happening?")

        if opportunity == "STRIKING DISTANCE":

            st.write(
                "This keyword is already ranking on page 1 but "
                "has room to move into stronger positions."
            )

        elif opportunity == "PAGE 2 OPPORTUNITY":

            st.write(
                "This keyword has meaningful search visibility "
                "but the ranking page is currently on page 2."
            )

        elif opportunity == "CTR OPPORTUNITY":

            st.write(
                "The page has visibility but the CTR appears "
                "low relative to its ranking opportunity."
            )

        elif opportunity == "PROTECT":

            st.write(
                "This keyword is already performing strongly. "
                "Major content changes may introduce unnecessary risk."
            )

        else:

            st.write(
                "This keyword has enough visibility to justify "
                "further content investigation."
            )

        st.markdown("### Why prioritize it?")

        st.write(
            f"The keyword generated "
            f"**{format_number(row['impressions'])} impressions** "
            f"and currently ranks around "
            f"**position {row['position']:.1f}**."
        )

        st.markdown("### Recommended action")

        st.write(
            row["recommendation"]
        )

        if opportunity != "PROTECT":

            st.markdown("### Content optimization checklist")

            st.markdown("""
- Review whether the current page fully satisfies the search intent.
- Strengthen the main topic in the introduction.
- Review H1 and H2/H3 structure.
- Add missing supporting subtopics.
- Improve topical depth without keyword stuffing.
- Add useful FAQs where relevant.
- Strengthen contextual internal links.
- Review title and meta description when CTR is weak.
- Avoid creating another URL if an existing relevant page already ranks.
""")

        else:

            st.success(
                "Protect this keyword and avoid unnecessary major changes."
            )


# ============================================================
# PAGE ANALYSIS
# ============================================================

if pages is not None:

    st.markdown(
        '<div class="section-title">📄 Pages With Highest SEO Opportunity</div>',
        unsafe_allow_html=True
    )

    page_df = pages.copy()

    page_df["score"] = page_df.apply(
        score_priority,
        axis=1
    )

    page_df = page_df.sort_values(
        ["score", "impressions"],
        ascending=False
    ).head(20)

    st.dataframe(
        page_df[
            [
                "page",
                "clicks",
                "impressions",
                "ctr",
                "position",
                "score"
            ]
        ],
        use_container_width=True,
        hide_index=True
    )


# ============================================================
# CANNIBALIZATION
# ============================================================

st.markdown(
    '<div class="section-title">⚠️ Cannibalization Check</div>',
    unsafe_allow_html=True
)

if daily_query is not None:

    dq = normalize_columns(
        daily_query.copy()
    )

    query_col = find_column(
        dq,
        ["query", "queries", "keyword"]
    )

    page_col = find_column(
        dq,
        ["page", "url", "top_pages"]
    )

    if query_col and page_col:

        mapping = dq[
            [query_col, page_col]
        ].dropna()

        grouped = (
            mapping.groupby(query_col)[page_col]
            .nunique()
            .sort_values(
                ascending=False
            )
        )

        cannibalization = grouped[
            grouped >= 2
        ].head(20)

        if len(cannibalization):

            st.warning(
                f"{len(cannibalization)} queries have multiple "
                "ranking URLs in the supplied dataset."
            )

            for query, count in cannibalization.items():

                urls = (
                    mapping[
                        mapping[query_col] == query
                    ][page_col]
                    .drop_duplicates()
                    .tolist()
                )

                st.write(
                    f"**{query}** — {count} URLs"
                )

                for url in urls:
                    st.write(
                        f"- {url}"
                    )

        else:

            st.success(
                "No obvious multi-page query competition detected."
            )

    else:

        st.info(
            "Query + Page mapping is required for a reliable "
            "cannibalization analysis."
        )

else:

    st.info(
        "Cannibalization analysis requires Date + Query + Page data "
        "or another dataset that directly maps queries to URLs."
    )


# ============================================================
# EXPORT
# ============================================================

st.markdown(
    '<div class="section-title">📥 Export Optimization Plan</div>',
    unsafe_allow_html=True
)

export_columns = [
    "query",
    "clicks",
    "impressions",
    "ctr",
    "position",
    "opportunity_type",
    "opportunity_score",
    "recommendation"
]

export_df = opportunities[
    [
        c for c in export_columns
        if c in opportunities.columns
    ]
].copy()

csv_data = export_df.to_csv(
    index=False
).encode("utf-8")

st.download_button(
    label="⬇️ Download SEO Optimization Plan",
    data=csv_data,
    file_name="gsc-content-optimization-plan.csv",
    mime="text/csv"
)


# ============================================================
# FOOTER
# ============================================================

st.divider()

st.caption(
    "GSC Content Optimizer — rule-based analysis. "
    "Recommendations should be reviewed against actual page content "
    "and search intent before implementation."
)
