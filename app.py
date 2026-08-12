import streamlit as st
import pandas as pd

st.set_page_config(
    page_title="GSC Content Optimizer",
    page_icon="📈",
    layout="wide"
)

st.title("📈 GSC Content Optimizer")

st.write(
    "Upload your Google Search Console Performance CSV "
    "to identify content optimization opportunities."
)

uploaded_file = st.file_uploader(
    "Upload your GSC CSV",
    type=["csv"]
)

if uploaded_file is not None:

    try:
        df = pd.read_csv(uploaded_file)

        st.success("CSV uploaded successfully!")

        st.subheader("Data Preview")

        st.dataframe(
            df.head(10),
            use_container_width=True
        )

        st.subheader("File Information")

        col1, col2, col3 = st.columns(3)

        with col1:
            st.metric("Rows", len(df))

        with col2:
            st.metric("Columns", len(df.columns))

        with col3:
            if "Page" in df.columns:
                st.metric(
                    "Unique URLs",
                    df["Page"].nunique()
                )
            else:
                st.metric("Unique URLs", "N/A")

    except Exception as e:

        st.error(
            f"Unable to read the CSV file: {e}"
        )
