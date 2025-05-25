# test_uploader.py
import streamlit as st

st.set_page_config(page_title="Uploader Test")
st.write("--- Uploader Test: Page config set. Script execution started. ---")

# Initialize session state variables that control uploader visibility
st.session_state.setdefault('show_summary', False)
st.session_state.setdefault('quiz_started', False)
st.write(f"--- Uploader Test: Initial st.session_state.show_summary: {st.session_state.show_summary} ---")
st.write(f"--- Uploader Test: Initial st.session_state.quiz_started: {st.session_state.quiz_started} ---")

uploaded_file = None 
if not st.session_state.get('show_summary', False) and not st.session_state.get('quiz_started', False):
    st.write("--- Uploader Test: Condition MET - Displaying file uploader UI element. ---")
    uploaded_file_widget_result = st.file_uploader(
        "Upload your document (DOCX, PDF, PPTX, or TXT)",
        type=["docx", "pdf", "pptx", "txt"], 
        key="file_uploader_test" # Using a new key for this test
    )
    st.write(f"--- Uploader Test: File uploader widget rendered. Result is {'File selected' if uploaded_file_widget_result else 'None'}. ---")
    if uploaded_file_widget_result is not None:
        uploaded_file = uploaded_file_widget_result
        st.success(f"File '{uploaded_file.name}' selected in test uploader!")
else: 
    st.write("--- Uploader Test: Condition NOT MET - File uploader should be hidden. ---")
    st.info("File uploader is hidden because 'show_summary' or 'quiz_started' is True.")
    st.write(f"--- Uploader Test: st.session_state.show_summary: {st.session_state.show_summary} ---")
    st.write(f"--- Uploader Test: st.session_state.quiz_started: {st.session_state.quiz_started} ---")


st.write(f"--- Uploader Test: After file uploader logic. uploaded_file is {'set with ' + uploaded_file.name if uploaded_file else 'None'}. ---")

st.markdown("---")
st.write("Buttons to simulate app state changes (for testing):")

def start_quiz_sim():
    st.session_state.quiz_started = True
    st.session_state.show_summary = False
    st.info("Simulated: Quiz Started. Uploader should hide on rerun.")

def show_summary_sim():
    st.session_state.quiz_started = False
    st.session_state.show_summary = True
    st.info("Simulated: Show Summary. Uploader should hide on rerun.")

def reset_to_upload_sim():
    st.session_state.quiz_started = False
    st.session_state.show_summary = False
    st.info("Simulated: Reset to Upload state. Uploader should show on rerun.")

if st.button("Simulate Start Quiz", key="sim_start_quiz"):
    start_quiz_sim()
    st.rerun()

if st.button("Simulate Show Summary", key="sim_show_summary"):
    show_summary_sim()
    st.rerun()

if st.button("Simulate Reset to Upload", key="sim_reset_upload"):
    reset_to_upload_sim()
    st.rerun()

st.write("--- Uploader Test: End of script execution for this run. ---")