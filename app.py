# app.py (AI_Quiz_Tutor_Upload version - The True Fix)

import streamlit as st

# --------------------------------------------------------------------------
# SET PAGE CONFIG - MUST BE THE FIRST STREAMLIT COMMAND
# --------------------------------------------------------------------------
st.set_page_config(layout="centered", page_title="AI Quiz Tutor")
# --------------------------------------------------------------------------

import re
import time
import google.generativeai as genai
import random
import numpy as np
import traceback
import io
import docx 
import PyPDF2 
from pptx import Presentation
import faiss 

try:
    from docling.document_converter import DocumentConverter
    from docling.datamodel.base_models import DocumentStream
    from docling.chunking import HybridChunker
    from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer # For Docling
    from transformers import AutoTokenizer 
except ImportError as e_import:
    st.error(f"CRITICAL IMPORT ERROR occurred: {e_import}")
    st.warning("This likely means 'docling', 'docling-core', or 'transformers' is not installed correctly in your Python environment.")
    st.stop() 
except Exception as e_generic_import:
    st.error(f"UNEXPECTED ERROR during crucial imports: {e_generic_import}")
    st.stop()

# --- Configuration ---
CORE_SUBJECT = "Insurance Principles" 
EMBEDDING_MODEL = "models/text-embedding-004"
CHROMA_COLLECTION_NAME = "uploaded_doc_chunks" 
NUM_CONTEXT_CHUNKS_TO_USE = 3      
MIN_WORDS_FOR_CONTENT_CHUNK = 4 
NUM_CHUNKS_TO_FETCH_SEMANTICALLY = 5 

# --- Function Definitions ---
def setup_vector_store(substantive_chunks_list, api_key_for_ef, uploaded_filename="document"):
    if not substantive_chunks_list:
        st.warning("FAISS Setup: No substantive chunks provided to build index.")
        st.session_state.faiss_index = None
        st.session_state.faiss_index_chunks = []
        return False
    print(f"--- FAISS: Starting setup for {len(substantive_chunks_list)} chunks from {uploaded_filename} ---")
    all_embeddings_list = []
    embedding_model_name = EMBEDDING_MODEL
    batch_size = 50
    num_batches = (len(substantive_chunks_list) + batch_size - 1) // batch_size
    progress_bar_embed = st.progress(0, text="Generating embeddings for document chunks...") 
    print(f"--- FAISS: Generating embeddings in {num_batches} batches of size {batch_size} ---")
    try:
        for i in range(num_batches):
            start_index = i * batch_size
            end_index = min((i + 1) * batch_size, len(substantive_chunks_list))
            batch_texts = substantive_chunks_list[start_index:end_index]
            if not batch_texts: continue
            response = genai.embed_content(
                model=embedding_model_name,
                content=batch_texts,
                task_type="RETRIEVAL_DOCUMENT"
            )
            batch_embeddings = response['embedding']
            all_embeddings_list.extend(batch_embeddings)
            progress_bar_embed.progress(float(end_index / len(substantive_chunks_list)), text=f"Generating embeddings... (Batch {i+1}/{num_batches})")
            time.sleep(0.1) 
        if not all_embeddings_list or len(all_embeddings_list) != len(substantive_chunks_list):
            st.error("FAISS Setup: Embedding generation failed or produced incorrect number of embeddings.")
            progress_bar_embed.empty()
            return False
        embeddings_np = np.array(all_embeddings_list).astype('float32')
        dimension = embeddings_np.shape[1]
        print(f"--- FAISS: Embeddings generated. Shape: {embeddings_np.shape}. ---")
        print("--- FAISS: Building FAISS index (IndexFlatL2) ---")
        faiss_index = faiss.IndexFlatL2(dimension)
        faiss_index.add(embeddings_np)
        print(f"--- FAISS: Index built. Total vectors in index: {faiss_index.ntotal}. ---")
        st.session_state.faiss_index = faiss_index
        st.session_state.faiss_index_chunks = substantive_chunks_list
        st.session_state.vector_store_setup_done = True
        progress_bar_embed.empty()
        return True
    except Exception as e:
        st.error(f"FAISS index creation failed: {e}")
        traceback.print_exc() 
        st.session_state.faiss_index = None
        st.session_state.faiss_index_chunks = []
        progress_bar_embed.empty()
        return False

def determine_document_theme(sampled_chunks, llm_model):
    if not sampled_chunks:
        print("--- Theme Determination: No chunks provided to determine theme. ---")
        return CORE_SUBJECT, "To understand general concepts from the document."
    print(f"--- Theme Determination: Analyzing {len(sampled_chunks)} sampled chunks. ---")
    combined_sample_text = ""
    char_limit_for_theme_prompt = 6000 
    for chunk in sampled_chunks:
        if len(combined_sample_text) + len(chunk) + 4 < char_limit_for_theme_prompt: 
            combined_sample_text += chunk + "\n---\n"
        else: break 
    if not combined_sample_text: 
        print("--- Theme Determination: Combined sample text is empty. Using fallback. ---")
        return CORE_SUBJECT, "To learn about the provided content."
    print(f"--- Theme Determination: Sending combined sample (approx {len(combined_sample_text)} chars) to LLM. ---")
    prompt = f"""
    Analyze the following text excerpts from a document. Your goal is to identify its main theme.
    1.  Identify the primary core subject of this document. Be concise and specific (e.g., "Principles of Marine Insurance," "Risk Management in Software Projects," "Introduction to Astrophysics"). Aim for 3-7 words.
    2.  Identify the primary learning objective or purpose of this document from a reader's perspective (e.g., "To understand key components of reinsurance treaties," "To learn how to apply agile methodologies," "To explain the life cycle of stars"). Start with "To..."
    Text Excerpts:\n---\n{combined_sample_text}\n---\n
    Provide your answer in the following exact format, with each item on a new line:
    Core Subject: [Identified core subject here]
    Primary Objective: [Identified primary objective here]
    """
    try:
        response = llm_model.generate_content(prompt, request_options={'timeout': 90}) 
        if response and response.text:
            response_text = response.text.strip()
            print(f"--- Theme Determination LLM Raw Response: ---\n{response_text}\n----------------------------------------")
            core_subject_match = re.search(r"Core Subject:\s*(.+)", response_text, re.IGNORECASE)
            primary_objective_match = re.search(r"Primary Objective:\s*(To .+)", response_text, re.IGNORECASE) 
            determined_subject = core_subject_match.group(1).strip() if core_subject_match else None
            determined_objective = primary_objective_match.group(1).strip() if primary_objective_match else None
            if determined_subject and determined_objective:
                print(f"--- Theme Determined: Subject='{determined_subject}', Objective='{determined_objective}' ---")
                return determined_subject, determined_objective
            else:
                print(f"--- Theme Determination: Could not parse subject/objective from LLM response. Core Subject Match: {core_subject_match}, Objective Match: {primary_objective_match} ---")
                subject_fallback = CORE_SUBJECT 
                objective_fallback = "To learn about the content of the uploaded document."
                if determined_subject: 
                    subject_fallback = determined_subject
                    objective_fallback = f"To understand key aspects of {determined_subject}."
                return subject_fallback, objective_fallback
        else:
            print("--- Theme Determination: LLM response was empty or invalid. ---")
            return CORE_SUBJECT, "To learn about the content of the uploaded document."
    except Exception as e:
        print(f"--- Error during theme determination LLM call: {type(e).__name__}: {e} ---")
        traceback.print_exc()
        return CORE_SUBJECT, "To analyze the provided document." 

def process_document_with_docling(uploaded_file_object, filename):
    if uploaded_file_object is None:
        st.error("Docling Processing: No file object received.")
        return None
    print(f"--- Docling Processing: Starting for file: {filename} ---")
    final_content_chunks = []
    start_time = time.time()
    try:
        uploaded_file_object.seek(0) 
        file_bytes = uploaded_file_object.read()
        buf = io.BytesIO(file_bytes)
        source = DocumentStream(name=filename, stream=buf) 
        print("--- Docling: Initializing DocumentConverter... ---")
        converter = DocumentConverter() 
        print("--- Docling: Converting document... ---")
        convert_result = converter.convert(source) 
        docling_doc_obj = convert_result.document
        if not docling_doc_obj:
            st.error("Docling Processing: Failed to convert document.")
            print("--- Docling: Document conversion returned None. ---")
            return None
        print(f"--- Docling: Document converted. Initial text elements found by converter: {len(docling_doc_obj.texts if hasattr(docling_doc_obj, 'texts') else 'N/A')} ---")
        print("--- Docling: Configuring Tokenizer for HybridChunker... ---")
        EMBED_MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2" 
        MAX_TOKENS_PER_CHUNK = 150
        hf_tokenizer_instance = AutoTokenizer.from_pretrained(EMBED_MODEL_ID)
        docling_tokenizer = HuggingFaceTokenizer(
            tokenizer=hf_tokenizer_instance,
            max_tokens=MAX_TOKENS_PER_CHUNK
        )
        print(f"--- Docling: Initializing HybridChunker with max_tokens={MAX_TOKENS_PER_CHUNK}, merge_peers=False ---")
        chunker = HybridChunker(tokenizer=docling_tokenizer, merge_peers=False)
        print("--- Docling: Starting HybridChunker process... ---")
        docling_chunk_iterator = chunker.chunk(docling_doc_obj)
        all_docling_chunks_from_hybridchunker = list(docling_chunk_iterator) 
        original_hybridchunker_count = len(all_docling_chunks_from_hybridchunker)
        print(f"--- Docling: HybridChunker produced {original_hybridchunker_count} initial chunks. Filtering... ---")
        for i, chunk_obj in enumerate(all_docling_chunks_from_hybridchunker):
            text = chunk_obj.text.strip() if hasattr(chunk_obj, 'text') else ""
            meta = chunk_obj.meta if hasattr(chunk_obj, 'meta') else None
            headings = meta.headings if meta and hasattr(meta, 'headings') and meta.headings else []
            words = text.split()
            num_words = len(words)
            if headings and num_words >= MIN_WORDS_FOR_CONTENT_CHUNK:
                final_content_chunks.append({
                    "text": text,
                    "headings": headings,
                    "original_docling_chunk_index": i
                })
        final_content_chunk_count = len(final_content_chunks)
        processing_time = time.time() - start_time
        print(f"--- Docling Processing: Original HybridChunker chunks: {original_hybridchunker_count}. Final substantive chunks: {final_content_chunk_count}. Time: {processing_time:.2f}s. ---")
        if not final_content_chunks:
            st.warning("Docling processed the document, but no substantive chunks with headings were extracted after filtering.")
            return None
        return final_content_chunks
    except Exception as e:
        processing_time = time.time() - start_time
        st.error(f"Docling Processing Error after {processing_time:.2f}s: {e}")
        print(f"--- Docling Processing Error: {type(e).__name__}: {e} ---")
        traceback.print_exc()
        return None

def display_heatmap_grid(): 
    st.subheader("📘 Document Coverage & Performance Heatmap")
    st.caption("Click on a section's colored square to view its full text. Colors indicate performance.")
    st.markdown("""
    <style>
        button[aria-label^="heatmap_square_btn_"] { 
            width: 22px !important; min-width: 22px !important; height: 22px !important;
            padding: 0px !important; margin: 1px !important; border: none !important;
            background-color: transparent !important; box-shadow: none !important;
            font-size: 14px !important; line-height: 18px !important; 
            text-align: center !important; display: inline-flex !important;
            align-items: center !important; justify-content: center !important; overflow: hidden;
        }
        div[data-testid="stExpander"] div[data-testid="stVerticalBlock"] div[data-testid="stMarkdownContainer"] p {
            margin-top: 0.15rem !important; margin-bottom: 0.15rem !important; line-height: 1.3 !important;
        }
        div[data-testid="stExpander"] div[data-testid="stVerticalBlock"] hr {
            margin-top: 0.25rem !important; margin-bottom: 0.25rem !important; border-top: 1px solid #e0e0e0 !important;
        }
    </style>
    """, unsafe_allow_html=True)
    
    colors_map = {
        0: {"bg": "#e7f3fe", "text": "#0c5460", "border": "#b8daff", "label": "Not Quizzed", "emoji": "🟦"},
        1: {"bg": "#d4edda", "text": "#155724", "border": "#c3e6cb", "label": "Correct", "emoji": "🟩"},
        2: {"bg": "#fff3cd", "text": "#856404", "border": "#ffeeba", "label": "Incorrect (1x)", "emoji": "🟨"},
        3: {"bg": "#f8d7da", "text": "#721c24", "border": "#f5c6cb", "label": "Incorrect (2+x)", "emoji": "🟥"},
        4: {"bg": "#e8eaf6", "text": "#303f9f", "border": "#c5cae9", "label": "Reviewed", "emoji": "🟣"} 
    }
    default_color_info = colors_map[0] 
    
    doc_chunk_details_list = st.session_state.get('doc_chunk_details', [])
    hover_labels_list = st.session_state.get('chunk_hover_labels', [])
    statuses_list = st.session_state.get('chunk_review_status', [])

    if not doc_chunk_details_list or not (len(doc_chunk_details_list) == len(hover_labels_list) == len(statuses_list)):
        st.warning("Heatmap data not fully initialized or inconsistent.")
        return
             
    legend_html_parts = [f'<span style="font-size:1.1em; margin-right:3px; vertical-align:middle;">{info["emoji"]}</span><span style="font-size:0.9em; margin-right:15px;">{info["label"]}</span>' for _, info in colors_map.items()]
    st.markdown("**Legend:** " + "".join(legend_html_parts), unsafe_allow_html=True)
    st.write("") 
            
    current_displayed_headings_path = [None] * 6 
    last_printed_heading_tuple = None
    cols_for_squares = None
    col_idx_for_squares = 0
    squares_per_row = 15 
    
    for chunk_idx, chunk_detail in enumerate(doc_chunk_details_list):
        chunk_full_headings = chunk_detail.get("full_headings_list", [])
        current_chunk_heading_tuple = tuple(chunk_full_headings)
        chunk_status_code = statuses_list[chunk_idx]
        chunk_hover_text_for_tooltip = hover_labels_list[chunk_idx] 
        if current_chunk_heading_tuple != last_printed_heading_tuple:
            if cols_for_squares and col_idx_for_squares != 0: 
                for _ in range(col_idx_for_squares, squares_per_row): cols_for_squares[_].empty()
            for level, heading_text in enumerate(chunk_full_headings):
                if level >= len(current_displayed_headings_path) or current_displayed_headings_path[level] != heading_text:
                    for l_reset in range(level, len(current_displayed_headings_path)): 
                        current_displayed_headings_path[l_reset] = None
                    current_displayed_headings_path[level] = heading_text
                    if level == 0: st.markdown(f"<h5>{heading_text}</h5>", unsafe_allow_html=True) 
                    elif level == 1: st.markdown(f"<h6 style='padding-left: 20px;'>{heading_text}</h6>", unsafe_allow_html=True)
                    else: st.markdown(f"<p style='padding-left: {(level)*20}px; font-size:0.9em; font-weight:bold; margin-bottom:2px;'>{heading_text}</p>", unsafe_allow_html=True)
            last_printed_heading_tuple = current_chunk_heading_tuple
            cols_for_squares = st.columns(squares_per_row) 
            col_idx_for_squares = 0
        elif not chunk_full_headings and last_printed_heading_tuple != ("(General Content)",):
            if cols_for_squares and col_idx_for_squares != 0:
                for _ in range(col_idx_for_squares, squares_per_row): cols_for_squares[_].empty()
            st.markdown(f"<h6><em>(Content without specific subsection heading)</em></h6>", unsafe_allow_html=True)
            last_printed_heading_tuple = ("(General Content)",)
            cols_for_squares = st.columns(squares_per_row)
            col_idx_for_squares = 0
        
        color_info = colors_map.get(chunk_status_code, default_color_info)
        button_key = f"heatmap_square_btn_{chunk_idx}"

        def _create_show_detail_callback(idx_to_show):
            def _callback():
                current_status = st.session_state.chunk_review_status[idx_to_show]
                if current_status == 0: 
                    st.session_state.chunk_review_status[idx_to_show] = 4 
                
                st.session_state.selected_heatmap_chunk_index = idx_to_show
                st.session_state.show_heatmap_chunk_detail = True
                print(f"--- Callback: show_heatmap_chunk_detail set to {st.session_state.show_heatmap_chunk_detail} for index {st.session_state.selected_heatmap_chunk_index}, status now: {st.session_state.chunk_review_status[idx_to_show]} ---")
            return _callback

        if cols_for_squares is None: 
            cols_for_squares = st.columns(squares_per_row)
            col_idx_for_squares = 0
        with cols_for_squares[col_idx_for_squares]:
            st.button(label=f"{color_info['emoji']}", 
                        key=button_key, 
                        help=f"{chunk_hover_text_for_tooltip}", 
                        on_click=_create_show_detail_callback(chunk_idx),
                        use_container_width=False)
        col_idx_for_squares = (col_idx_for_squares + 1) % squares_per_row
        if col_idx_for_squares == 0 and chunk_idx < len(doc_chunk_details_list) -1 : 
            cols_for_squares = None 
    if cols_for_squares and col_idx_for_squares != 0:
        for _ in range(col_idx_for_squares, squares_per_row):
            cols_for_squares[_].empty()

def generate_quiz_question(model, subject="Document Content", difficulty="average", previous_question_text=None, all_doc_chunks=None, focused_chunk_idx=None):
    if not model or not all_doc_chunks: return None, []
    faiss_index = st.session_state.get('faiss_index')
    doc_objective = st.session_state.get('dynamic_doc_objective', "To help the reader understand the provided text.")
    original_context_indices = [] 
    
    # ---------------------------------------------------------
    # FIXED: Hard Anchor to Focused Chunk - Never drops context
    # ---------------------------------------------------------
    if focused_chunk_idx is not None and (0 <= focused_chunk_idx < len(all_doc_chunks)):
        try:
            original_context_indices = [focused_chunk_idx]
            if faiss_index is not None:
                query_emb = genai.embed_content(
                    model=EMBEDDING_MODEL,
                    content=all_doc_chunks[focused_chunk_idx],
                    task_type="RETRIEVAL_QUERY"
                )['embedding']

                distances, faiss_indices_ret = faiss_index.search(
                    np.array(query_emb).astype('float32').reshape(1, -1),
                    k=NUM_CONTEXT_CHUNKS_TO_USE
                )
                for idx in faiss_indices_ret[0]:
                    if idx != focused_chunk_idx and idx not in original_context_indices and 0 <= idx < len(all_doc_chunks):
                        original_context_indices.append(int(idx))
                    if len(original_context_indices) >= NUM_CONTEXT_CHUNKS_TO_USE:
                        break
        except Exception:
            original_context_indices = [focused_chunk_idx]
            
    elif not previous_question_text: 
        if not st.session_state.available_chunk_indices:
            st.session_state.available_chunk_indices = list(range(len(all_doc_chunks)))
            random.shuffle(st.session_state.available_chunk_indices)
        original_context_indices = [st.session_state.available_chunk_indices.pop(0) for _ in range(min(NUM_CONTEXT_CHUNKS_TO_USE, len(st.session_state.available_chunk_indices)))]
    elif difficulty == "harder" and st.session_state.available_chunk_indices: 
        original_context_indices = [st.session_state.available_chunk_indices.pop(0) for _ in range(min(NUM_CONTEXT_CHUNKS_TO_USE, len(st.session_state.available_chunk_indices)))]
    elif difficulty == "simpler" and previous_question_text and faiss_index:
        try:
            query_emb = genai.embed_content(model=EMBEDDING_MODEL, content=previous_question_text, task_type="RETRIEVAL_QUERY")['embedding']
            distances, faiss_indices_ret = faiss_index.search(np.array(query_emb).astype('float32').reshape(1, -1), k=NUM_CONTEXT_CHUNKS_TO_USE)
            original_context_indices = [int(i) for i in faiss_indices_ret[0] if 0 <= i < len(all_doc_chunks)]
        except: original_context_indices = st.session_state.get('current_question_context_indices', [])
    
    if not original_context_indices:
        original_context_indices = random.sample(range(len(all_doc_chunks)), min(NUM_CONTEXT_CHUNKS_TO_USE, len(all_doc_chunks)))

    context_to_send = "\n\n---\n\n".join([all_doc_chunks[i] for i in original_context_indices])[:8000]
    
    prompt = f"""
    You are an expert quiz generator. The subject of the document is '{subject}'. Objective: '{doc_objective}'.
    Generate a question of {difficulty} difficulty testing understanding of principles covered directly in the 'Provided Text Context'.
    NO METADATA QUESTIONS. Focus strictly on the substance.
    Output Format (EXACTLY as shown, using these precise labels):
    Question: [Your question here]
    A: [Option A text]
    B: [Option B text]
    C: [Option C text]
    D: [Option D text]
    Correct Answer: [Letter ONLY, e.g., C]
    Explanation: [Brief explanation from context.]
    Provided Text Context:\n---\n{context_to_send}\n---\nGenerate the question now.
    """ 
    try:
        response = model.generate_content(prompt, request_options={'timeout': 60}).text.strip()
        parsed_data = {
            "question": re.search(r"Question:\s*(.+)", response).group(1).strip(),
            "options": {
                "A": re.search(r"A:\s*(.+)", response).group(1).strip(),
                "B": re.search(r"B:\s*(.+)", response).group(1).strip(),
                "C": re.search(r"C:\s*(.+)", response).group(1).strip(),
                "D": re.search(r"D:\s*(.+)", response).group(1).strip()
            },
            "correct_answer": re.search(r"Correct Answer:\s*([A-D])", response).group(1).strip(),
            "explanation": re.search(r"Explanation:\s*(.+)", response, re.S).group(1).strip()
        }
        return parsed_data, original_context_indices 
    except Exception as e:
        return None, []


# --- Main Application Logic Starts Here ---

# Conditional Title Setting
if st.session_state.get('show_summary', False):
    st.title("Quiz Summary") 
elif st.session_state.get('in_heatmap_quiz_mode', False):
    pass 
else:
    st.title("AI Quiz Tutor") 

# --- LLM Configuration ---
if 'llm_configured' not in st.session_state: st.session_state.llm_configured = False
if 'gemini_model' not in st.session_state: st.session_state.gemini_model = None
if 'gemini_api_key' not in st.session_state: st.session_state.gemini_api_key = None
try:
    if not st.session_state.llm_configured:
        if "GEMINI_API_KEY" not in st.secrets: 
            raise KeyError("API key not found in st.secrets")
        st.session_state.gemini_api_key = st.secrets["GEMINI_API_KEY"]
        genai.configure(api_key=st.session_state.gemini_api_key)
        st.session_state.gemini_model = genai.GenerativeModel('gemini-2.5-flash')
        st.session_state.llm_configured = True
except Exception as e: 
    st.error(f"AI Config Error: {e}") 
    st.session_state.llm_configured = False

# --- Initialize Session State ---
st.session_state.setdefault('uploaded_file_key', None) 
st.session_state.setdefault('substantive_chunks_for_quiz', None) 
st.session_state.setdefault('vector_store_setup_done', False) 
st.session_state.setdefault('faiss_index', None) 
st.session_state.setdefault('faiss_index_chunks', []) 
st.session_state.setdefault('available_chunk_indices', []) 
st.session_state.setdefault('dynamic_doc_subject', None)
st.session_state.setdefault('dynamic_doc_objective', None)
st.session_state.setdefault('chunk_review_status', []) 
st.session_state.setdefault('chunk_labels', []) 
st.session_state.setdefault('current_question_context_indices', []) 
st.session_state.setdefault('doc_chunk_details', []) 
st.session_state.setdefault('chunk_hover_labels', []) 
st.session_state.setdefault('quiz_started', False) 
st.session_state.setdefault('current_question_data', None)
st.session_state.setdefault('question_number', 0)
st.session_state.setdefault('user_answer', None)
st.session_state.setdefault('feedback_message', None)
st.session_state.setdefault('show_explanation', False)
st.session_state.setdefault('last_answer_correct', None)
st.session_state.setdefault('incorrectly_answered_questions', [])
st.session_state.setdefault('total_questions_answered', 0)
st.session_state.setdefault('show_summary', False) 
st.session_state.setdefault('current_doc_subject', CORE_SUBJECT)
st.session_state.setdefault('show_heatmap_chunk_detail', False)
st.session_state.setdefault('selected_heatmap_chunk_index', None)
st.session_state.setdefault('uploaded_file_object_ref', None)
st.session_state.setdefault('in_heatmap_quiz_mode', False) 
st.session_state.setdefault('heatmap_quiz_source_chunk_idx', None) 
st.session_state.setdefault('heatmap_quiz_current_context_indices', [])
st.session_state.setdefault('heatmap_quiz_last_answer_incorrect', False) 


# --- File Uploader Logic ---
uploaded_file = None 
if not st.session_state.get('show_summary', False) and \
   not st.session_state.get('quiz_started', False) and \
   not st.session_state.get('in_heatmap_quiz_mode', False):
    uploaded_file_widget_result = st.file_uploader(
        "Upload your document ",
        type=["docx", "pdf", "pptx", "txt"], key="file_uploader"
    )
    st.caption("Upload of pdf files using a Mac with an Apple M-series chip (M1/M2/M3) does not work")
    if uploaded_file_widget_result is not None:
        st.session_state.uploaded_file_object_ref = uploaded_file_widget_result 
        uploaded_file = uploaded_file_widget_result
    else:
        uploaded_file = st.session_state.get('uploaded_file_object_ref', None) 
        # CAUSE OF BUG IDENTIFIED: If name had underscores, it wiped the file here.
        if uploaded_file and st.session_state.get('uploaded_file_key') and \
           uploaded_file.name != st.session_state.get('uploaded_file_key','').split('_')[0] :
            uploaded_file = None 
            st.session_state.uploaded_file_object_ref = None
else: 
    uploaded_file = st.session_state.get('uploaded_file_object_ref', None)

# --- Document Processing ---
if uploaded_file is not None and not st.session_state.get('in_heatmap_quiz_mode', False): 
    current_file_key = f"{uploaded_file.name}_{uploaded_file.size}"
    needs_full_processing = False
    if st.session_state.get('uploaded_file_key') != current_file_key:
        needs_full_processing = True
    elif not st.session_state.get('vector_store_setup_done', False):
        needs_full_processing = True
    
    if needs_full_processing:
        st.session_state.uploaded_file_key = current_file_key
        st.session_state.substantive_chunks_for_quiz = None 
        st.session_state.doc_chunk_details = [] 
        st.session_state.vector_store_setup_done = False
        st.session_state.faiss_index = None      
        st.session_state.faiss_index_chunks = []      
        st.session_state.available_chunk_indices = [] 
        st.session_state.dynamic_doc_subject = None
        st.session_state.dynamic_doc_objective = None
        st.session_state.chunk_hover_labels = [] 
        st.session_state.chunk_review_status = [] 
        st.session_state.current_question_context_indices = []
        st.session_state.quiz_started = False
        st.session_state.current_question_data = None
        st.session_state.question_number = 0
        st.session_state.incorrectly_answered_questions = []
        st.session_state.total_questions_answered = 0
        st.session_state.show_summary = False
        st.session_state.feedback_message = None
        st.session_state.show_explanation = False
        st.session_state.current_doc_subject = CORE_SUBJECT
        st.session_state.in_heatmap_quiz_mode = False 
        st.session_state.heatmap_quiz_source_chunk_idx = None
        
        docling_output_list = process_document_with_docling(uploaded_file, uploaded_file.name)
        
        if docling_output_list:
            st.session_state.doc_chunk_details = [{"text": item['text'], "full_headings_list": item.get('headings', [])} for item in docling_output_list]
            st.session_state.substantive_chunks_for_quiz = [item['text'] for item in st.session_state.doc_chunk_details]
            st.session_state.faiss_index_chunks = st.session_state.substantive_chunks_for_quiz 

            num_words_for_hover = 50
            st.session_state.chunk_hover_labels = [] 
            for item in st.session_state.doc_chunk_details: 
                words = item['text'].split()
                hover_label = ' '.join(words[:num_words_for_hover])
                if len(words) > num_words_for_hover:
                    hover_label += "..."
                st.session_state.chunk_hover_labels.append(hover_label)
            num_final_chunks = len(st.session_state.substantive_chunks_for_quiz)
            st.session_state.chunk_review_status = [0] * num_final_chunks 
            st.session_state.available_chunk_indices = list(range(num_final_chunks))
            random.shuffle(st.session_state.available_chunk_indices)
            
            if st.session_state.substantive_chunks_for_quiz: 
                with st.spinner("Determining document theme..."):
                    num_s_chunks = len(st.session_state.substantive_chunks_for_quiz)
                    sample_indices_theme = sorted(list(set( list(range(min(2, num_s_chunks))) + ([num_s_chunks // 3, min(num_s_chunks // 3 + 1, num_s_chunks - 1)] if num_s_chunks > 5 else []) + ([min(num_s_chunks * 2 // 3, num_s_chunks-1), min(num_s_chunks * 2 // 3 + 1, num_s_chunks -1)] if num_s_chunks > 8 else []) + (list(range(max(0, num_s_chunks - 2), num_s_chunks)) if num_s_chunks > 3 else []))))[:8]
                    final_sample_indices_theme = [idx for idx in sample_indices_theme if 0 <= idx < num_s_chunks]
                    sampled_chunks_for_theme_text = [st.session_state.substantive_chunks_for_quiz[i] for i in final_sample_indices_theme]
                    if sampled_chunks_for_theme_text:
                        subject, objective = determine_document_theme(sampled_chunks_for_theme_text, st.session_state.gemini_model)
                        st.session_state.dynamic_doc_subject = subject
                        st.session_state.dynamic_doc_objective = objective
                    else: 
                        st.session_state.dynamic_doc_subject = CORE_SUBJECT
                        st.session_state.dynamic_doc_objective = "To learn about the provided content."

            if st.session_state.get('dynamic_doc_subject'):
                st.session_state.current_doc_subject = st.session_state.dynamic_doc_subject
            elif uploaded_file: 
                st.session_state.current_doc_subject = uploaded_file.name.rsplit('.', 1)[0].replace('_', ' ').replace('-', ' ')
            else: 
                st.session_state.current_doc_subject = CORE_SUBJECT
            
            if st.session_state.substantive_chunks_for_quiz and st.session_state.llm_configured: 
                with st.spinner(f"Building FAISS index for '{uploaded_file.name}'..."):
                    setup_success = setup_vector_store(st.session_state.substantive_chunks_for_quiz, st.session_state.gemini_api_key, uploaded_file.name)
                    st.session_state.vector_store_setup_done = setup_success
        else: 
            st.session_state.vector_store_setup_done = False
    else: 
        if uploaded_file:
            if st.session_state.get('dynamic_doc_subject'):
                st.session_state.current_doc_subject = st.session_state.dynamic_doc_subject
            elif st.session_state.current_doc_subject == CORE_SUBJECT:
                st.session_state.current_doc_subject = uploaded_file.name.rsplit('.', 1)[0].replace('_', ' ').replace('-', ' ')
    
    if uploaded_file and not st.session_state.get('vector_store_setup_done') and \
       st.session_state.get('substantive_chunks_for_quiz') is not None :
        st.warning(f"Doc '{uploaded_file.name}' processed, but vector store setup might have failed. Quiz may use basic context.")

# --- App Logic (Conditions for displaying quiz UI, summary, etc.) ---

# FIX: Replaced 'uploaded_file is not None' with check for text chunks
if st.session_state.get('in_heatmap_quiz_mode', False) and st.session_state.get('substantive_chunks_for_quiz'):
    
    # Optional styling header
    st.subheader(f"Focused Quiz on Topic from Document Section") 
    
    if st.session_state.get('heatmap_quiz_source_chunk_idx') is not None:
         st.caption(f"Question based on content from document section related to chunk {st.session_state.heatmap_quiz_source_chunk_idx + 1}.")

    if not st.session_state.get('current_question_data'):
        with st.spinner("Generating focused question..."):
            difficulty = "simpler" if st.session_state.get('heatmap_quiz_last_answer_incorrect') else "average"
            prev_q = st.session_state.current_question_data.get('question') if st.session_state.get('heatmap_quiz_last_answer_incorrect') and st.session_state.get('current_question_data') else None
            
            q_data, context_indices = generate_quiz_question(
                model=st.session_state.gemini_model,
                subject=st.session_state.current_doc_subject, 
                difficulty=difficulty, 
                previous_question_text=prev_q,
                all_doc_chunks=st.session_state.substantive_chunks_for_quiz,
                focused_chunk_idx=st.session_state.heatmap_quiz_source_chunk_idx
            )
        if q_data:
            st.session_state.current_question_data = q_data
            st.session_state.heatmap_quiz_current_context_indices = context_indices 
            st.session_state.user_answer = None
            st.session_state.show_explanation = False
            st.session_state.feedback_message = None
            st.session_state.last_answer_correct = None 
            st.rerun()
        else:
            st.error("Failed to generate a question for this topic.")
            st.session_state.in_heatmap_quiz_mode = False
            st.session_state.show_summary = True 
            st.rerun()

    if st.session_state.get('current_question_data'):
        q_data = st.session_state.current_question_data
        with st.container(border=True):
            st.markdown(f"**{q_data['question']}**")
            options_list = [f"{k}: {q_data['options'].get(k)}" for k in ["A","B","C","D"]]
            idx = None
            if st.session_state.show_explanation and st.session_state.user_answer:
                try: idx = [opt.startswith(f"{st.session_state.user_answer}:") for opt in options_list].index(True)
                except ValueError: pass
            
            selected_opt = st.radio("Select:", options_list, index=idx, disabled=st.session_state.show_explanation, label_visibility="collapsed")
            if not st.session_state.show_explanation:
                st.session_state.user_answer = selected_opt.split(":")[0] if selected_opt else None

            if st.button("Submit Answer", disabled=st.session_state.show_explanation):
                if not st.session_state.user_answer:
                    st.warning("Please select an answer.")
                else:
                    st.session_state.total_questions_answered += 1 
                    correct = q_data.get("correct_answer")
                    if st.session_state.user_answer == correct:
                        st.session_state.feedback_message = "Correct!"
                        st.session_state.last_answer_correct = True
                        st.session_state.heatmap_quiz_last_answer_incorrect = False
                    else:
                        st.session_state.feedback_message = f"Incorrect. Correct was: **{correct}**."
                        st.session_state.last_answer_correct = False
                        st.session_state.heatmap_quiz_last_answer_incorrect = True 
                        st.session_state.incorrectly_answered_questions.append({
                            "question_number": f"Focused (Chunk {st.session_state.heatmap_quiz_source_chunk_idx + 1})",
                            "question_text": q_data["question"],
                            "your_answer": st.session_state.user_answer,
                            "correct_answer": correct,
                            "explanation": q_data.get("explanation", "N/A"),
                            "options_dict": q_data.get("options", {})
                        })
                    
                    if st.session_state.heatmap_quiz_current_context_indices:
                        for idx_status in st.session_state.heatmap_quiz_current_context_indices:
                            if 0 <= idx_status < len(st.session_state.chunk_review_status):
                                cs = st.session_state.chunk_review_status[idx_status]
                                if st.session_state.last_answer_correct: 
                                    st.session_state.chunk_review_status[idx_status] = 1 
                                else: 
                                    if cs in [0, 1, 4]: st.session_state.chunk_review_status[idx_status] = 2 
                                    elif cs == 2: st.session_state.chunk_review_status[idx_status] = 3
                    
                    st.session_state.show_explanation = True
                    st.rerun()

            if st.session_state.show_explanation:
                if st.session_state.last_answer_correct: st.success(st.session_state.feedback_message)
                else: st.error(st.session_state.feedback_message)
                st.caption(f"Explanation: {q_data.get('explanation', 'N/A')}")

                if st.session_state.last_answer_correct:
                    if st.button("Back to Quiz Summary"):
                        st.session_state.in_heatmap_quiz_mode = False
                        st.session_state.heatmap_quiz_source_chunk_idx = None
                        st.session_state.current_question_data = None
                        st.session_state.show_summary = True
                        st.session_state.heatmap_quiz_last_answer_incorrect = False
                        st.rerun()
                else: 
                    if st.button("Try Another Question on this Topic"):
                        st.session_state.current_question_data = None 
                        st.session_state.show_explanation = False
                        st.rerun()
            
            st.divider()
            if st.button("End Focused Quiz & View Summary"):
                st.session_state.in_heatmap_quiz_mode = False
                st.session_state.heatmap_quiz_source_chunk_idx = None
                st.session_state.current_question_data = None 
                st.session_state.show_summary = True
                st.rerun()

elif st.session_state.get('show_summary', False):
    _summary_scroll_anchor = st.empty() # Attempt to influence scroll

    if uploaded_file: 
        st.caption(f"Document: {uploaded_file.name}")

    total_answered = st.session_state.total_questions_answered
    incorrect_list = st.session_state.incorrectly_answered_questions
    num_incorrect = len(incorrect_list)
    num_correct = total_answered - num_incorrect
    col1, col2 = st.columns([1, 3])
    with col1: st.metric(label="Score", value=f"{(num_correct / total_answered * 100):.1f}%" if total_answered > 0 else "N/A")
    with col2:
        st.write(f"**Total Questions Answered:** {total_answered}")
        st.write(f"**Correct:** {num_correct}, **Incorrect:** {num_incorrect}")
    st.divider()
    
    if not incorrect_list and total_answered > 0 :
        st.success("Perfect score! All questions answered correctly.")
    elif incorrect_list:
        with st.expander("Review Topics for Incorrect Answers"): 
            for item in incorrect_list:
                st.error(f"**Q{item['question_number']}: {item['question_text']}**")
                st.write(f"> Your Answer: **{item['your_answer']}**. {item['options_dict'].get(item['your_answer'], '')}")
                st.write(f"> Correct Answer: **{item['correct_answer']}**. {item['options_dict'].get(item['correct_answer'], '')}")
                st.caption(f"Explanation: {item.get('explanation', 'N/A')}")
                st.markdown("---")
    elif total_answered == 0:
        st.info("No questions were answered in this session.")
    st.divider() 
    
    # Detail Expander
    if st.session_state.get('show_heatmap_chunk_detail', False) and st.session_state.get('selected_heatmap_chunk_index') is not None:
        idx = st.session_state.selected_heatmap_chunk_index
        doc_chunk_details = st.session_state.get('doc_chunk_details', []) 
        if 0 <= idx < len(doc_chunk_details):
            chunk_info = doc_chunk_details[idx]
            path_title = " -> ".join(chunk_info.get("full_headings_list", [])) or "General Content"
            with st.expander(f"Path: {path_title} (Paragraph {idx + 1})", expanded=True): 
                st.markdown(f"<p style='line-height: 1.3;'><b>{chunk_info.get('text')}</b></p>", unsafe_allow_html=True)
                col1_exp, col2_exp = st.columns(2)
                with col1_exp:
                    if st.button("Quiz me on this chunk"): 
                        st.session_state.in_heatmap_quiz_mode = True
                        st.session_state.heatmap_quiz_source_chunk_idx = idx
                        st.session_state.current_question_data = None 
                        st.session_state.quiz_started = False 
                        st.session_state.show_summary = False 
                        st.session_state.show_heatmap_chunk_detail = False 
                        st.rerun()
                with col2_exp:
                    if st.button("Close Detail"): 
                        st.session_state.show_heatmap_chunk_detail = False
                        st.session_state.selected_heatmap_chunk_index = None
                        st.rerun()
    
    with st.expander("📘 Document Coverage & Performance Heatmap"): 
        display_heatmap_grid() 
    
    st.divider()
    if st.button("Start New Quiz Once More"):
        st.session_state.quiz_started = False
        st.session_state.question_number = 0 
        st.session_state.current_question_data = None
        st.session_state.user_answer = None
        st.session_state.show_explanation = False
        st.session_state.incorrectly_answered_questions = []
        st.session_state.total_questions_answered = 0
        st.session_state.show_summary = False
        st.session_state.in_heatmap_quiz_mode = False 
        if st.session_state.get('substantive_chunks_for_quiz'):
            st.session_state.available_chunk_indices = list(range(len(st.session_state.substantive_chunks_for_quiz)))
            random.shuffle(st.session_state.available_chunk_indices)
            st.session_state.chunk_review_status = [0] * len(st.session_state.substantive_chunks_for_quiz)
        st.rerun()

# FIX: Replaced 'uploaded_file is not None' with check for text chunks
elif st.session_state.get('vector_store_setup_done') and \
     st.session_state.get('substantive_chunks_for_quiz') and \
     st.session_state.llm_configured and \
     not st.session_state.get('quiz_started', False):
    
    st.markdown("#### Document Analyzed and ready to test your knowledge") 
    if st.session_state.current_doc_subject:
        st.markdown(f"**Subject:** {st.session_state.current_doc_subject}")
    if st.session_state.dynamic_doc_objective: 
        st.markdown(f"**Document objective:** {st.session_state.dynamic_doc_objective}")
    
    if not st.session_state.get('vector_store_setup_done', False): 
        st.warning("Note: FAISS index setup may have failed. Quiz will use basic random context selection if so.")
    
    if st.button("Start Quiz!", type="primary", key="start_quiz_main_btn"): 
        st.session_state.quiz_started = True
        st.session_state.question_number = 1
        st.session_state.show_explanation = False
        st.session_state.user_answer = None
        st.session_state.current_question_data = None 
        st.session_state.incorrectly_answered_questions = []
        st.session_state.total_questions_answered = 0
        st.session_state.show_summary = False
        st.session_state.in_heatmap_quiz_mode = False 
        
        with st.spinner("Generating first question..."):
            q_data, context_indices = generate_quiz_question(
                model=st.session_state.gemini_model, 
                subject=st.session_state.current_doc_subject, 
                difficulty="average", 
                all_doc_chunks=st.session_state.substantive_chunks_for_quiz 
            )
        if q_data: 
            st.session_state.current_question_data = q_data
            st.session_state.current_question_context_indices = context_indices
            st.rerun() 
        else: 
            st.error("Failed to generate Q1. Please try starting the quiz again.")
            st.session_state.quiz_started = False 

# FIX: Replaced 'uploaded_file is not None' with check for text chunks
elif st.session_state.get('quiz_started', False) and st.session_state.get('substantive_chunks_for_quiz'):
    if uploaded_file: st.caption(f"Document: {uploaded_file.name}")

    with st.container(border=True):
        if st.session_state.current_question_data:
            q_data = st.session_state.current_question_data
            st.subheader(f"Question {st.session_state.question_number}")
            st.markdown(f"**{q_data['question']}**")
            options_list = [f"{k}: {q_data['options'].get(k)}" for k in ["A","B","C","D"]]
            idx = None
            if st.session_state.show_explanation and st.session_state.user_answer:
                try: idx = [opt.startswith(f"{st.session_state.user_answer}:") for opt in options_list].index(True)
                except ValueError: pass 

            selected_opt = st.radio("Select:", options_list, index=idx, disabled=st.session_state.show_explanation, label_visibility="collapsed")
            if not st.session_state.show_explanation: 
                st.session_state.user_answer = selected_opt.split(":")[0] if selected_opt else None

            st.write("---")
            if st.button("Submit Answer", disabled=st.session_state.show_explanation, type="primary" if not st.session_state.show_explanation else "secondary"):
                if not st.session_state.user_answer: st.warning("Select answer."); st.stop()
                
                st.session_state.total_questions_answered += 1
                correct = q_data.get("correct_answer")
                if st.session_state.user_answer == correct:
                    st.session_state.feedback_message = "Correct!"
                    st.session_state.last_answer_correct = True
                else: 
                    st.session_state.feedback_message = f"Incorrect. Correct: **{correct}**."
                    st.session_state.last_answer_correct = False
                    st.session_state.incorrectly_answered_questions.append({
                        "question_number": st.session_state.question_number, 
                        "question_text": q_data["question"], 
                        "your_answer": st.session_state.user_answer, 
                        "correct_answer": correct, 
                        "explanation": q_data.get("explanation", "N/A"),
                        "options_dict": q_data.get("options", {}) 
                    })
                
                if st.session_state.current_question_context_indices:
                    for idx_status in st.session_state.current_question_context_indices: 
                        if 0 <= idx_status < len(st.session_state.chunk_review_status):
                            cs = st.session_state.chunk_review_status[idx_status]
                            if st.session_state.last_answer_correct: 
                                if cs == 0 or cs == 4: st.session_state.chunk_review_status[idx_status] = 1
                            else: 
                                if cs in [0, 1, 4]: st.session_state.chunk_review_status[idx_status] = 2 
                                elif cs == 2: st.session_state.chunk_review_status[idx_status] = 3
                st.session_state.show_explanation = True
                st.rerun()

            if st.session_state.show_explanation:
                if st.session_state.last_answer_correct: st.success(st.session_state.feedback_message)
                else: st.error(st.session_state.feedback_message)
                st.caption(f"Explanation: {q_data.get('explanation', 'N/A')}")
            
            if st.button("Next Question"):
                diff = "harder" if st.session_state.last_answer_correct else "simpler"
                st.session_state.show_explanation = False
                st.session_state.user_answer = None
                with st.spinner("Moving to next section..." if st.session_state.last_answer_correct else "Revisiting topic..."):
                    next_q, ctx_idx = generate_quiz_question(
                        model=st.session_state.gemini_model, subject=st.session_state.current_doc_subject, 
                        difficulty=diff, previous_question_text=q_data['question'], 
                        all_doc_chunks=st.session_state.substantive_chunks_for_quiz
                    )
                if next_q: 
                    st.session_state.current_question_data = next_q
                    st.session_state.current_question_context_indices = ctx_idx
                    st.session_state.question_number += 1
                    st.rerun()
            
            st.divider()
            if st.button("Stop Quiz"): 
                st.session_state.show_summary = True
                st.session_state.quiz_started = False
                st.rerun()

else: 
    if uploaded_file is None and st.session_state.llm_configured :
        data_privacy_explanation = "To provide quiz features, this application processes your uploaded document. Snippets of your document are sent to Google's Generative AI services to generate relevant content. Google's API policies state that this data is not used to train their general models. No original documents are stored by this application after your session ends."
        st.markdown("Data Privacy", help=data_privacy_explanation)
    elif not st.session_state.llm_configured:
        st.warning("AI Model configuration failed. Please check API key and secrets setup.")
        st.caption("Ensure your `GEMINI_API_KEY` is correctly placed in `.streamlit/secrets.toml` and is valid.")
    else: 
        if 'uploaded_file_key' in st.session_state and \
           st.session_state.uploaded_file_key is not None and \
           st.session_state.substantive_chunks_for_quiz is None and \
           not (st.session_state.get('show_summary', False) or \
                st.session_state.get('quiz_started', False) or \
                st.session_state.get('in_heatmap_quiz_mode', False)): 
            st.error("Document processing failed after upload.")
        elif uploaded_file is None and not (st.session_state.get('show_summary', False) or st.session_state.get('quiz_started', False) or st.session_state.get('in_heatmap_quiz_mode', False)):
            data_privacy_explanation = "To provide quiz features, this application processes your uploaded document. Snippets of your document are sent to Google's Generative AI services to generate relevant content. Google's API policies state that this data is not used to train their general models. No original documents are stored by this application after your session ends."
            st.markdown("Data Privacy", help=data_privacy_explanation)
