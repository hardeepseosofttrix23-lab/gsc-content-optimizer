import streamlit as st
import pandas as pd
import zipfile
import io

st.set_page_config(
    page_title="GSC Content Optimizer",
    page_icon="📈",
    layout="wide"
)

st.title("📈 GSC Content Optimizer")

st.write(
    "Upload your Google Search Console Performance ZIP file "
    "to identify content optimization opportunities."
)

uploaded_file = st.file_uploader(
    "Upload your GSC Performance ZIP",
    type=["zip", "csv", "xlsx", "xls"]
)

if uploaded_file is not None:

    try:

        # Open ZIP file
        with zipfile.ZipFile(uploaded_file, "r") as zip_file:

            # Find CSV files inside ZIP
            csv_files = [
                file for file in zip_file.namelist()
                if file.lower().endswith(".csv")
            ]

            if not csv_files:

                st.error(
                    "No CSV files were found inside the ZIP file."
                )

            else:

                st.success(
                    f"ZIP uploaded successfully! "
                    f"Found {len(csv_files)} CSV file(s)."
                )

                st.subheader("CSV Files Found")

                for file in csv_files:
                    st.write(f"📄 {file}")

                # Try to find the main query CSV
                selected_file = None

                for file in csv_files:

                    filename = file.lower()

                    if "query" in filename:
                        selected_file = file
                        break

                # If no query file is found, use first CSV
                if selected_file is None:
                    selected_file = csv_files[0]

                st.info(
                    f"Analyzing: {selected_file}"
                )

                # Read selected CSV
                with zip_file.open(selected_file) as csv_file:

                    df = pd.read_csv(
                        csv_file,
                        encoding="utf-8-sig"
                    )

                st.success("GSC data loaded successfully!")

                st.subheader("Data Preview")

                st.dataframe(
                    df.head(10),
                    use_container_width=True
                )

                st.subheader("File Information")

                col1, col2, col3 = st.columns(3)

                with col1:
                    st.metric(
                        "Rows",
                        len(df)
                    )

                with col2:
                    st.metric(
                        "Columns",
                        len(df.columns)
                    )

                with col3:
                    st.metric(
                        "Unique Queries",
                        df.iloc[:, 0].nunique()
                    )

                st.subheader("Available Columns")

                st.write(
                    list(df.columns)
                )

    except Exception as e:

        st.error(
            f"Unable to process the GSC ZIP file: {e}"
        )
