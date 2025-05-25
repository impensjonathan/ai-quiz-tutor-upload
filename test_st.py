# test_st.py
import streamlit as st

st.set_page_config(page_title="Streamlit Test") # Corrected argument

st.write("Hello from st.write!")
st.title("Streamlit Test App")
st.markdown("If you see this, basic st functions are working.")
st.button("Test Button")

st.write(f"Streamlit version: {st.__version__}")