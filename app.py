import streamlit as st
import pandas as pd
import zipfile
import io
import re
import plotly.express as px

# =========================================================
# PAGE CONFIG
# =========================================================

st.set_page_config(
    page_title="GSC Content Optimizer",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded"
)

# =========================================================
# CUSTOM CSS
# =========================================================

st.markdown("""
<style>

.main {
    background-color: #f7f8fc;
}

.block-container {
    padding-top: 2rem;
    padding-bottom: 3rem;
}

h1 {
    font-size: 2.4rem !important;
    font-weight: 700 !important;
}

h2 {
    font-weight: 650 !important;
}

h3 {
    font-weight: 600 !important;
}

.metric-card {
    background: white;
    padding: 20px;
    border-radius: 14px;
    border: 1px solid #e6e8ef;
    box-shadow: 0 2px 8px rgba(0,0,0,0.04);
}

.metric-title {
    font-size: 14px;
    color: #6b7280;
}

.metric-value {
    font-size: 28px;
    font-weight: 700;
    margin-top: 5px;
}

.opportunity-card {
    background: white;
    padding: 22px;
    border-radius: 14px;
    border: 1px solid #e6e8ef;
    margin-bottom: 15px;
}

.high {
    border-left: 5px solid #dc2626;
}

.medium {
    border-left: 5px solid #f59e0b;
}

.low {
    border-left: 5px solid #16a34a;
}

.info-box {
    background: #eef6ff;
    padding: 16px;
    border-radius: 10px;
    border: 1px solid #cfe4ff;
}

.success-box {
    background: #ecfdf3;
    padding: 16px;
    border-radius: 10px;
    border: 1px solid #b7efcf;
}

.warning-box {
    background: #fff8e6;
    padding: 16px;
    border-radius: 10px;
    border: 1px solid #f7df9c;
}

</style>
""", unsafe_allow_html=True)


# =========================================================
# HEADER
# =========================================================

st.title("📈 GSC Content Optimizer")

st.markdown(
    "Turn Google Search Console performance data into "
    "**actionable content optimization recommendations.**"
)

st.caption(
    "Upload your GSC ZIP, CSV, or Excel export. "
    "The tool analyzes keywords, pages, CTR, impressions and rankings."
)


# =========================================================
# HELPER FUNCTIONS
# =========================================================

def clean_column_name(column):
    """Normalize GSC column names."""
    column = str(column).strip()
    column = column.replace("\ufeff", "")
    return column


def normalize_columns(df):
    """Clean dataframe column names."""
    df = df.copy()
    df.columns = [clean_column_name(c) for c in df.columns]
    return df


def identify_dataset(filename, df):
    """
    Identify what type of GSC dataset we are dealing with.
    """

    filename_lower = filename.lower()

    columns = [
        str(c).lower()
        for c in df.columns
    ]

    column_text = " ".join(columns)

    # Query dataset
    if (
        "query" in filename_lower
        or "queries" in filename_lower
        or "top queries" in column_text
        or "top query" in column_text
    ):
        return "queries"

    # Page dataset
    if (
        "page" in filename_lower
        or "pages" in filename_lower
        or "top pages" in column_text
        or "top page" in column_text
    ):
        return "pages"

    # Chart / date dataset
    if (
        "chart" in filename_lower
        or "date" in column_text
    ):
        return "chart"

    # Country dataset
    if (
        "country" in filename_lower
        or "countries" in filename_lower
        or "country" in column_text
    ):
        return "countries"

    # Device dataset
    if (
        "device" in filename_lower
        or "devices" in filename_lower
    ):
        return "devices"

    # Search appearance
    if (
        "search appearance" in filename_lower
        or "searchappearance" in filename_lower
        or "search appearance" in column_text
    ):
        return "search_appearance"

    return "unknown"


def read_csv_bytes(file_bytes):
    """Read CSV safely."""
    try:
        return pd.read_csv(
            io.BytesIO(file_bytes),
            encoding="utf-8-sig"
        )
    except Exception:
        try:
            return pd.read_csv(
                io.BytesIO(file_bytes),
                encoding="latin-1"
            )
        except Exception:
            return None


def read_excel_bytes(file_bytes):
    """Read Excel file and return useful sheets."""
    try:
        excel = pd.ExcelFile(io.BytesIO(file_bytes))

        sheets = {}

        for sheet in excel.sheet_names:
            try:
                sheets[sheet] = pd.read_excel(
                    io.BytesIO(file_bytes),
                    sheet_name=sheet
                )
            except Exception:
                pass

        return sheets

    except Exception:
        return {}


def load_uploaded_files(uploaded_files):
    """
    Read ZIP, CSV and Excel files.
    Returns:
        datasets = {
            queries: dataframe,
            pages: dataframe,
            chart: dataframe,
            countries: dataframe,
            ...
        }
    """

    datasets = {}

    for uploaded_file in uploaded_files:

        filename = uploaded_file.name
        extension = filename.lower().split(".")[-1]

        # -------------------------------------------------
        # ZIP
        # -------------------------------------------------

        if extension == "zip":

            try:

                with zipfile.ZipFile(uploaded_file, "r") as z:

                    for internal_file in z.namelist():

                        if internal_file.endswith("/"):
                            continue

                        if not internal_file.lower().endswith(".csv"):
                            continue

                        file_bytes = z.read(internal_file)

                        df = read_csv_bytes(file_bytes)

                        if df is None or df.empty:
                            continue

                        df = normalize_columns(df)

                        dataset_type = identify_dataset(
                            internal_file,
                            df
                        )

                        if dataset_type != "unknown":

                            datasets[dataset_type] = df

            except Exception as e:

                st.error(
                    f"Could not read ZIP file: {e}"
                )

        # -------------------------------------------------
        # CSV
        # -------------------------------------------------

        elif extension == "csv":

            df = read_csv_bytes(
                uploaded_file.getvalue()
            )

            if df is not None and not df.empty:

                df = normalize_columns(df)

                dataset_type = identify_dataset(
                    filename,
                    df
                )

                if dataset_type != "unknown":
                    datasets[dataset_type] = df

        # -------------------------------------------------
        # EXCEL
        # -------------------------------------------------

        elif extension in ["xlsx", "xls"]:

            sheets = read_excel_bytes(
                uploaded_file.getvalue()
            )

            for sheet_name, df in sheets.items():

                if df is None or df.empty:
                    continue

                df = normalize_columns(df)

                dataset_type = identify_dataset(
                    sheet_name,
                    df
                )

                if dataset_type != "unknown":
                    datasets[dataset_type] = df

    return datasets


def find_column(df, possible_names):
    """Find a column using flexible matching."""

    normalized = {
        str(c).lower().strip(): c
        for c in df.columns
    }

    # Exact match
    for name in possible_names:

        name_lower = name.lower()

        if name_lower in normalized:
            return normalized[name_lower]

    # Partial match
    for column_lower, original in normalized.items():

        for name in possible_names:

            if name.lower() in column_lower:
                return original

    return None


def prepare_gsc_dataframe(df, dataset_type):
    """
    Standardize GSC metrics.
    """

    df = df.copy()

    if dataset_type == "queries":

        dimension_column = find_column(
            df,
            [
                "query",
                "queries",
                "top queries",
                "top query"
            ]
        )

    elif dataset_type == "pages":

        dimension_column = find_column(
            df,
            [
                "page",
                "pages",
                "top pages",
                "top page"
            ]
        )

    else:
        dimension_column = None

    clicks_column = find_column(
        df,
        ["clicks", "total clicks"]
    )

    impressions_column = find_column(
        df,
        ["impressions", "total impressions"]
    )

    ctr_column = find_column(
        df,
        ["ctr", "average ctr"]
    )

    position_column = find_column(
        df,
        ["position", "average position"]
    )

    rename_map = {}

    if dimension_column:
        rename_map[dimension_column] = "Dimension"

    if clicks_column:
        rename_map[clicks_column] = "Clicks"

    if impressions_column:
        rename_map[impressions_column] = "Impressions"

    if ctr_column:
        rename_map[ctr_column] = "CTR"

    if position_column:
        rename_map[position_column] = "Position"

    df = df.rename(
        columns=rename_map
    )

    # Numeric conversion

    for column in [
        "Clicks",
        "Impressions",
        "Position"
    ]:

        if column in df.columns:

            df[column] = pd.to_numeric(
                df[column],
                errors="coerce"
            )

    # CTR can come as "3.25%"
    if "CTR" in df.columns:

        if df["CTR"].dtype == "object":

            df["CTR"] = (
                df["CTR"]
                .astype(str)
                .str.replace("%", "", regex=False)
            )

        df["CTR"] = pd.to_numeric(
            df["CTR"],
            errors="coerce"
        )

        # Convert percentage to decimal
        if df["CTR"].max() > 1:
            df["CTR"] = df["CTR"] / 100

    return df


# =========================================================
# OPPORTUNITY ANALYSIS
# =========================================================

def classify_intent(keyword):

    keyword = str(keyword).lower()

    commercial_words = [
        "buy",
        "price",
        "pricing",
        "cost",
        "service",
        "services",
        "agency",
        "company",
        "provider",
        "hire",
        "near me",
        "best",
        "quote"
    ]

    transactional_words = [
        "buy",
        "order",
        "book",
        "purchase",
        "hire",
        "quote"
    ]

    informational_words = [
        "what",
        "why",
        "how",
        "when",
        "where",
        "guide",
        "tips",
        "meaning",
        "difference",
        "benefits"
    ]

    local_words = [
        "near me",
        "near",
        "in ",
        "local",
        "city",
        "town"
    ]

    if any(
        word in keyword
        for word in transactional_words
    ):
        return "Transactional"

    if any(
        word in keyword
        for word in commercial_words
    ):
        return "Commercial"

    if any(
        word in keyword
        for word in local_words
    ):
        return "Local"

    if any(
        keyword.startswith(word)
        for word in informational_words
    ):
        return "Informational"

    return "Mixed / Unclear"


def expected_ctr(position):

    """
    Heuristic CTR benchmark.

    This is NOT a Google benchmark.
    It is used only to identify potential
    CTR opportunities.
    """

    if position <= 1:
        return 0.28

    if position <= 2:
        return 0.18

    if position <= 3:
        return 0.12

    if position <= 5:
        return 0.08

    if position <= 10:
        return 0.045

    if position <= 20:
        return 0.02

    return 0.008


def analyze_keyword(row):

    clicks = row.get("Clicks", 0)
    impressions = row.get("Impressions", 0)
    ctr = row.get("CTR", 0)
    position = row.get("Position", 100)

    if pd.isna(clicks):
        clicks = 0

    if pd.isna(impressions):
        impressions = 0

    if pd.isna(ctr):
        ctr = 0

    if pd.isna(position):
        position = 100

    score = 0

    opportunity = "Performing Well"

    reason = ""

    action = ""

    priority = "Low"

    # ---------------------------------------------
    # HIGH STRIKING DISTANCE
    # ---------------------------------------------

    if (
        position >= 5
        and position <= 10
        and impressions >= 100
    ):

        score += 40

        opportunity = "Striking Distance"

        reason = (
            "The keyword is already ranking on the first page "
            "but has not reached the strongest positions. "
            "The existing visibility indicates that the page "
            "has potential for additional organic traffic."
        )

        action = (
            "Strengthen topical coverage, improve the relevant "
            "content sections, review title/H1 alignment and "
            "add relevant internal links."
        )

        priority = "High"

    # ---------------------------------------------
    # POSITION 11-20
    # ---------------------------------------------

    elif (
        position > 10
        and position <= 20
        and impressions >= 100
    ):

        score += 30

        opportunity = "Page 2 Opportunity"

        reason = (
            "The keyword is ranking within positions 11–20 "
            "and has meaningful impressions. It is close enough "
            "to page one to justify content optimization."
        )

        action = (
            "Expand content depth, cover related subtopics, "
            "improve internal linking and strengthen topical relevance."
        )

        priority = "High"

    # ---------------------------------------------
    # LOW CTR
    # ---------------------------------------------

    expected = expected_ctr(position)

    if (
        impressions >= 100
        and position <= 10
        and ctr < expected * 0.55
    ):

        score += 30

        opportunity = "CTR Opportunity"

        reason = (
            "The keyword has meaningful search visibility, "
            "but the current click-through rate appears low "
            "relative to its ranking position."
        )

        action = (
            "Review the title tag and meta description for "
            "search intent, relevance and stronger value communication. "
            "Do not change the content solely for CTR without reviewing "
            "the actual SERP."
        )

        priority = "High"

    # ---------------------------------------------
    # HIGH IMPRESSIONS
    # ---------------------------------------------

    if impressions >= 1000:

        score += 15

    elif impressions >= 500:

        score += 10

    # ---------------------------------------------
    # HIGH CLICK VOLUME
    # ---------------------------------------------

    if clicks >= 100:

        score += 10

    elif clicks >= 50:

        score += 5

    # ---------------------------------------------
    # STRONG PERFORMER
    # ---------------------------------------------

    if (
        position <= 3
        and ctr >= expected
        and impressions >= 100
    ):

        opportunity = "Performing Well"

        reason = (
            "This keyword is already achieving a strong ranking "
            "and healthy engagement. Major content changes could "
            "risk existing performance."
        )

        action = (
            "Maintain the current content. Consider supporting "
            "the page with internal links and related content "
            "rather than making major changes."
        )

        priority = "Low"

        score = max(score, 20)

    # ---------------------------------------------
    # NORMALIZE SCORE
    # ---------------------------------------------

    score = min(score, 100)

    if priority == "High":
        score = max(score, 70)

    elif priority == "Medium":
        score = max(score, 40)

    return opportunity, priority, score, reason, action


def create_keyword_analysis(df):

    results = []

    for _, row in df.iterrows():

        keyword = row.get(
            "Dimension",
            ""
        )

        if not keyword or pd.isna(keyword):
            continue

        opportunity, priority, score, reason, action = (
            analyze_keyword(row)
        )

        intent = classify_intent(
            keyword
        )

        results.append({
            "Keyword": keyword,
            "Clicks": row.get("Clicks", 0),
            "Impressions": row.get("Impressions", 0),
            "CTR": row.get("CTR", 0),
            "Position": row.get("Position", 0),
            "Search Intent": intent,
            "Opportunity": opportunity,
            "Priority": priority,
            "Score": score,
            "Why": reason,
            "Recommended Action": action
        })

    return pd.DataFrame(results)


# =========================================================
# SIDEBAR
# =========================================================

with st.sidebar:

    st.header("📂 Upload GSC Data")

    uploaded_files = st.file_uploader(
        "Upload GSC files",
        type=[
            "zip",
            "csv",
            "xlsx",
            "xls"
        ],
        accept_multiple_files=True
    )

    st.divider()

    st.markdown(
        """
        **Supported formats**

        • GSC ZIP export  
        • CSV  
        • Excel XLSX  
        • Excel XLS  

        Multiple files can be uploaded.
        """
    )


# =========================================================
# MAIN APPLICATION
# =========================================================

if not uploaded_files:

    st.info(
        "👈 Upload your Google Search Console export "
        "from the sidebar to begin."
    )

    st.markdown("### What this tool analyzes")

    col1, col2, col3 = st.columns(3)

    with col1:

        st.markdown(
            """
            ### 🔎 Keywords

            Identify:

            • Top-performing keywords  
            • Striking-distance keywords  
            • Page 2 opportunities  
            • CTR opportunities  
            • High-impression keywords
            """
        )

    with col2:

        st.markdown(
            """
            ### 📄 Pages

            Identify:

            • Strong pages  
            • Pages needing optimization  
            • High-impression pages  
            • Low-CTR pages  
            • Content opportunities
            """
        )

    with col3:

        st.markdown(
            """
            ### 🎯 Recommendations

            Get:

            • What is happening  
            • Why it matters  
            • What to change  
            • How to improve it  
            • Priority level
            """
        )

    st.stop()


# =========================================================
# LOAD DATA
# =========================================================

with st.spinner("Reading and analyzing your GSC files..."):

    datasets = load_uploaded_files(
        uploaded_files
    )


# =========================================================
# DATASET STATUS
# =========================================================

st.subheader("📦 GSC Data Detected")

status_columns = st.columns(4)

dataset_names = [
    ("queries", "🔎 Queries"),
    ("pages", "📄 Pages"),
    ("chart", "📊 Performance"),
    ("countries", "🌍 Countries")
]

for index, (key, label) in enumerate(dataset_names):

    with status_columns[index]:

        if key in datasets:

            st.success(
                f"✓ {label}"
            )

        else:

            st.warning(
                f"— {label}"
            )


# =========================================================
# PREPARE QUERY DATA
# =========================================================

keyword_df = None
keyword_analysis = None

if "queries" in datasets:

    keyword_df = prepare_gsc_dataframe(
        datasets["queries"],
        "queries"
    )

    if "Dimension" in keyword_df.columns:

        keyword_analysis = create_keyword_analysis(
            keyword_df
        )


# =========================================================
# OVERVIEW METRICS
# =========================================================

st.divider()

st.header("📊 Performance Overview")

if keyword_df is not None:

    total_clicks = keyword_df["Clicks"].sum()

    total_impressions = keyword_df["Impressions"].sum()

    weighted_ctr = (
        total_clicks / total_impressions
        if total_impressions > 0
        else 0
    )

    weighted_position = (
        keyword_df["Position"].mean()
        if "Position" in keyword_df.columns
        else 0
    )

    total_keywords = len(
        keyword_df
    )

else:

    total_clicks = 0
    total_impressions = 0
    weighted_ctr = 0
    weighted_position = 0
    total_keywords = 0


metric1, metric2, metric3, metric4, metric5 = st.columns(5)

with metric1:

    st.metric(
        "Clicks",
        f"{total_clicks:,.0f}"
    )

with metric2:

    st.metric(
        "Impressions",
        f"{total_impressions:,.0f}"
    )

with metric3:

    st.metric(
        "CTR",
        f"{weighted_ctr * 100:.2f}%"
    )

with metric4:

    st.metric(
        "Avg. Position",
        f"{weighted_position:.1f}"
    )

with metric5:

    st.metric(
        "Keywords",
        f"{total_keywords:,}"
    )


# =========================================================
# OPPORTUNITY SUMMARY
# =========================================================

if keyword_analysis is not None and not keyword_analysis.empty:

    st.divider()

    st.header("🚀 Optimization Opportunities")

    high_count = len(
        keyword_analysis[
            keyword_analysis["Priority"] == "High"
        ]
    )

    medium_count = len(
        keyword_analysis[
            keyword_analysis["Priority"] == "Medium"
        ]
    )

    performing_count = len(
        keyword_analysis[
            keyword_analysis["Opportunity"]
            == "Performing Well"
        ]
    )

    col1, col2, col3 = st.columns(3)

    with col1:

        st.metric(
            "🔴 High Priority",
            high_count
        )

    with col2:

        st.metric(
            "🟠 Medium Priority",
            medium_count
        )

    with col3:

        st.metric(
            "🟢 Performing Well",
            performing_count
        )


# =========================================================
# CHART
# =========================================================

if keyword_analysis is not None and not keyword_analysis.empty:

    st.divider()

    st.header("📈 Keyword Opportunity Distribution")

    opportunity_counts = (
        keyword_analysis["Opportunity"]
        .value_counts()
        .reset_index()
    )

    opportunity_counts.columns = [
        "Opportunity",
        "Count"
    ]

    fig = px.bar(
        opportunity_counts,
        x="Opportunity",
        y="Count",
        text="Count",
        title="Keyword Opportunities"
    )

    fig.update_layout(
        showlegend=False,
        height=400
    )

    st.plotly_chart(
        fig,
        use_container_width=True
    )


# =========================================================
# TOP PERFORMERS
# =========================================================

if keyword_analysis is not None and not keyword_analysis.empty:

    st.divider()

    st.header("🏆 Top Performing Keywords")

    top_keywords = (
        keyword_analysis
        .sort_values(
            by="Clicks",
            ascending=False
        )
        .head(20)
        .copy()
    )

    top_keywords["CTR"] = (
        top_keywords["CTR"] * 100
    ).round(2).astype(str) + "%"

    top_keywords["Position"] = (
        top_keywords["Position"]
        .round(1)
    )

    display_columns = [
        "Keyword",
        "Clicks",
        "Impressions",
        "CTR",
        "Position",
        "Search Intent"
    ]

    st.dataframe(
        top_keywords[display_columns],
        use_container_width=True,
        hide_index=True
    )


# =========================================================
# HIGH PRIORITY OPPORTUNITIES
# =========================================================

if keyword_analysis is not None and not keyword_analysis.empty:

    st.divider()

    st.header("🔴 High-Priority Optimization Opportunities")

    high_opportunities = (
        keyword_analysis[
            keyword_analysis["Priority"] == "High"
        ]
        .sort_values(
            by="Score",
            ascending=False
        )
        .head(50)
    )

    if high_opportunities.empty:

        st.success(
            "No high-priority opportunities were detected "
            "using the current rules."
        )

    else:

        for _, row in high_opportunities.iterrows():

            st.markdown(
                f"""
                <div class="opportunity-card high">

                <h3>🔴 {row['Keyword']}</h3>

                <b>Opportunity:</b> {row['Opportunity']}
                &nbsp;&nbsp; | &nbsp;&nbsp;
                <b>Score:</b> {row['Score']}/100
                &nbsp;&nbsp; | &nbsp;&nbsp;
                <b>Intent:</b> {row['Search Intent']}

                <hr>

                <b>Current Performance</b>

                <br>
                Clicks: {row['Clicks']:,.0f}
                &nbsp; | &nbsp;
                Impressions: {row['Impressions']:,.0f}
                &nbsp; | &nbsp;
                CTR: {row['CTR'] * 100:.2f}%
                &nbsp; | &nbsp;
                Position: {row['Position']:.1f}

                <br><br>

                <b>WHAT:</b><br>
                {row['Opportunity']}

                <br><br>

                <b>WHY:</b><br>
                {row['Why']}

                <br><br>

                <b>HOW:</b><br>
                {row['Recommended Action']}

                </div>
                """,
                unsafe_allow_html=True
            )


# =========================================================
# ALL KEYWORD ANALYSIS
# =========================================================

if keyword_analysis is not None and not keyword_analysis.empty:

    st.divider()

    st.header("🔎 Complete Keyword Analysis")

    st.caption(
        "Use the filters to find the exact keywords "
        "you should prioritize."
    )

    col1, col2, col3 = st.columns(3)

    with col1:

        opportunity_filter = st.multiselect(
            "Opportunity Type",
            options=sorted(
                keyword_analysis[
                    "Opportunity"
                ].dropna()
                .unique()
                .tolist()
            )
        )

    with col2:

        priority_filter = st.multiselect(
            "Priority",
            options=[
                "High",
                "Medium",
                "Low"
            ]
        )

    with col3:

        intent_filter = st.multiselect(
            "Search Intent",
            options=sorted(
                keyword_analysis[
                    "Search Intent"
                ].dropna()
                .unique()
                .tolist()
            )
        )

    filtered = keyword_analysis.copy()

    if opportunity_filter:

        filtered = filtered[
            filtered["Opportunity"]
            .isin(opportunity_filter)
        ]

    if priority_filter:

        filtered = filtered[
            filtered["Priority"]
            .isin(priority_filter)
        ]

    if intent_filter:

        filtered = filtered[
            filtered["Search Intent"]
            .isin(intent_filter)
        ]

    display_df = filtered.copy()

    display_df["CTR"] = (
        display_df["CTR"] * 100
    ).round(2).astype(str) + "%"

    display_df["Position"] = (
        display_df["Position"]
        .round(1)
    )

    display_df["Score"] = (
        display_df["Score"]
        .astype(int)
    )

    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True
    )


# =========================================================
# PAGE ANALYSIS
# =========================================================

if "pages" in datasets:

    st.divider()

    st.header("📄 Page Performance Analysis")

    page_df = prepare_gsc_dataframe(
        datasets["pages"],
        "pages"
    )

    if "Dimension" in page_df.columns:

        page_df = page_df.sort_values(
            by="Impressions",
            ascending=False
        )

        page_display = page_df.copy()

        if "CTR" in page_display.columns:

            page_display["CTR"] = (
                page_display["CTR"] * 100
            ).round(2).astype(str) + "%"

        if "Position" in page_display.columns:

            page_display["Position"] = (
                page_display["Position"]
                .round(1)
            )

        st.dataframe(
            page_display.head(100),
            use_container_width=True,
            hide_index=True
        )


# =========================================================
# DOWNLOAD REPORT
# =========================================================

if keyword_analysis is not None and not keyword_analysis.empty:

    st.divider()

    st.header("📥 Export Optimization Report")

    export_df = keyword_analysis.copy()

    export_df["CTR"] = (
        export_df["CTR"] * 100
    ).round(2)

    output = io.BytesIO()

    with pd.ExcelWriter(
        output,
        engine="openpyxl"
    ) as writer:

        export_df.to_excel(
            writer,
            sheet_name="Keyword Analysis",
            index=False
        )

        if "pages" in datasets:

            page_export = prepare_gsc_dataframe(
                datasets["pages"],
                "pages"
            )

            page_export.to_excel(
                writer,
                sheet_name="Page Analysis",
                index=False
            )

    output.seek(0)

    st.download_button(
        label="📥 Download Excel Optimization Report",
        data=output,
        file_name="GSC_Content_Optimization_Report.xlsx",
        mime=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        )
    )


# =========================================================
# FOOTER
# =========================================================

st.divider()

st.caption(
    "GSC Content Optimizer • Opportunity scores and CTR comparisons "
    "are heuristic recommendations and should be validated against "
    "actual SERPs and page content."
)
