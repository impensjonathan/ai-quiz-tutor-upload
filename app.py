# app.py (AI_Quiz_Tutor_Upload version - Detailed LLM Error Logging)

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
    st.warning("This likely means 'docling', 'docling-core', or 'transformers' is not installed correctly in your Python 3.11 environment.")
    st.info("Please stop Streamlit, activate your 'py311_env', ensure correct installation of docling and its dependencies, then restart Streamlit.")
    st.stop() 
except Exception as e_generic_import:
    st.error(f"UNEXPECTED ERROR during crucial imports: {e_generic_import} ([Errno {e_generic_import.errno if hasattr(e_generic_import, 'errno') else 'N/A'}] {e_generic_import})")
    st.stop()

# --- Configuration ---
CORE_SUBJECT = "Insurance Principles" 
EMBEDDING_MODEL = "models/text-embedding-004"
CHROMA_COLLECTION_NAME = "uploaded_doc_chunks" 
NUM_CONTEXT_CHUNKS_TO_USE = 3      # Base number of chunks for final context
MIN_WORDS_FOR_CONTENT_CHUNK = 4 
NUM_CHUNKS_TO_FETCH_SEMANTICALLY = 5 # How many to initially FETCH for semantic search (for simpler or harder-fallback)

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
    # st.write("--- Starting Document Processing with Docling ---") # Removed as per user request
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

def generate_chunk_labels(chunks_list, llm_model, prompt_batch_size=5, inter_batch_delay_seconds=4):
    if not chunks_list: 
        print("--- Chunk Labeling: No chunks provided. ---")
        return [""] * len(chunks_list) 
    print(f"--- Chunk Labeling: Starting label generation for {len(chunks_list)} chunks in batches of {prompt_batch_size}. Delay: {inter_batch_delay_seconds}s ---")
    all_labels = []
    num_total_chunks = len(chunks_list)
    num_batches = (num_total_chunks + prompt_batch_size - 1) // prompt_batch_size
    progress_text = "Generating descriptive labels for document sections..."
    label_progress_bar = st.progress(0, text=f"{progress_text} (Batch 0/{num_batches})")
    for i in range(num_batches):
        batch_start_index = i * prompt_batch_size
        batch_end_index = min((i + 1) * prompt_batch_size, num_total_chunks)
        current_batch_chunks_texts = chunks_list[batch_start_index:batch_end_index]
        if not current_batch_chunks_texts: continue
        print(f"--- Chunk Labeling: Preparing Batch {i+1}/{num_batches} ({len(current_batch_chunks_texts)} chunks) for LLM ---")
        prompt_for_batch = "For each of the following numbered paragraphs, provide a very concise topic label (ideally 2-4 words) that best describes its main content. Each label should be suitable for a heatmap display. Focus on the most specific subject matter of each paragraph. Avoid generic phrases like 'paragraph content' or 'text excerpt'.\n\n"
        for idx_in_batch, chunk_text in enumerate(current_batch_chunks_texts):
            max_label_chunk_chars = 750 
            truncated_chunk_text = chunk_text[:max_label_chunk_chars]
            if len(chunk_text) > max_label_chunk_chars:
                truncated_chunk_text += "..."
            prompt_for_batch += f"Paragraph {idx_in_batch + 1}:\n---\n{truncated_chunk_text}\n---\n\n"
        prompt_for_batch += f"Output the labels in this exact format, each on a new line, numbered starting from 1 (e.g., '1: Label for Paragraph 1', '2: Label for Paragraph 2', etc. up to '{len(current_batch_chunks_texts)}:' ):"
        batch_generated_labels = []
        try:
            print(f"--- Chunk Labeling: Sending Batch {i+1} to LLM ---")
            for attempt in range(2): 
                label_response = llm_model.generate_content(prompt_for_batch, request_options={'timeout': 90}) 
                if label_response and label_response.text:
                    raw_labels_text = label_response.text.strip()
                    print(f"--- Chunk Labeling: Batch {i+1} Raw LLM Response ---\n{raw_labels_text}\n--------------------")
                    temp_labels_for_batch = {} 
                    for line in raw_labels_text.splitlines():
                        match = re.match(r"^\s*(\d+)\s*[:\-]\s*(.+)", line)
                        if match:
                            label_num = int(match.group(1))
                            label_text = match.group(2).strip().replace("\"", "").replace("'", "")
                            label_text = " ".join(label_text.split())[:50] 
                            if label_text: temp_labels_for_batch[label_num] = label_text
                    processed_all_in_batch = True
                    for k_idx in range(len(current_batch_chunks_texts)):
                        if (k_idx + 1) not in temp_labels_for_batch:
                            processed_all_in_batch = False; break
                    if processed_all_in_batch:
                        for k_idx in range(len(current_batch_chunks_texts)):
                            batch_generated_labels.append(temp_labels_for_batch[k_idx+1])
                        print(f"--- Chunk Labeling: Successfully labeled batch {i+1} with {len(batch_generated_labels)} labels. ---")
                        break 
                    else: 
                        print(f"--- Chunk Labeling: Warning - Batch {i+1} parsing failed or label count mismatch. Expected {len(current_batch_chunks_texts)}, got {len(temp_labels_for_batch)}. Retrying batch if possible. ---")
                        batch_generated_labels = [] 
                if attempt < 1: 
                    print(f"--- Chunk Labeling: Retrying batch {i+1} in {inter_batch_delay_seconds * 2}s ... ---")
                    time.sleep(inter_batch_delay_seconds * 2) 
                else: 
                    print(f"--- Chunk Labeling: Max retries for batch {i+1} reached. Using default labels for this batch. ---")
        except Exception as e_label_batch:
            print(f"--- Chunk Labeling: Error generating labels for batch {i+1}: {e_label_batch} ---")
        if len(batch_generated_labels) != len(current_batch_chunks_texts):
            batch_generated_labels = [f"Chunk {batch_start_index + k + 1}" for k in range(len(current_batch_chunks_texts))]
            print(f"--- Chunk Labeling: Using default labels for batch {i+1}. ---")
        all_labels.extend(batch_generated_labels)
        label_progress_bar.progress(float((i + 1) / num_batches), text=f"{progress_text} (Batch {i+1}/{num_batches} processed)")
        if i < num_batches - 1: 
            print(f"--- Chunk Labeling: Waiting {inter_batch_delay_seconds}s before next batch... ---")
            time.sleep(inter_batch_delay_seconds)
    label_progress_bar.empty()
    print(f"--- Chunk Labeling: Finished. Generated {len(all_labels)} labels. ---")
    if len(all_labels) != num_total_chunks:
        print(f"--- Chunk Labeling: CRITICAL - Final label count mismatch. Expected {num_total_chunks}, got {len(all_labels)}. Padding with defaults. ---")
        all_labels.extend([f"Chunk {len(all_labels) + k + 1}" for k in range(num_total_chunks - len(all_labels))])
    return all_labels[:num_total_chunks]

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

def generate_quiz_question(model, subject="Document Content", difficulty="average", 
                           previous_question_text=None, all_doc_chunks=None, focused_chunk_idx=None):
    print(f"--- Terminal Log: Generating question. Mode: {'Focused' if focused_chunk_idx is not None else 'Normal'}. Difficulty: {difficulty}, Subject: '{subject}'. Prev Q: {'Yes' if previous_question_text else 'No'}. Chunks: {len(all_doc_chunks) if all_doc_chunks else 'None'}. Focused Idx: {focused_chunk_idx}")
    
    if not model: 
        st.error("Q Gen: AI Model not configured.")
        return None, [] 
    if not all_doc_chunks: 
        st.error("Q Gen: No document chunks provided (all_doc_chunks).")
        return None, []
    
    faiss_index = st.session_state.get('faiss_index')
    doc_objective = st.session_state.get('dynamic_doc_objective', "To help the reader understand the provided text.")
    if not doc_objective: doc_objective = "To help the reader understand the provided text."

    context_text_list = []
    original_context_indices = [] 
    source_of_context = "" 

    if focused_chunk_idx is not None and faiss_index is not None and (0 <= focused_chunk_idx < len(all_doc_chunks)):
        source_of_context = f"Focused on Chunk {focused_chunk_idx + 1}"
        print(f"--- Terminal Log: Q Gen - Focused mode for chunk {focused_chunk_idx} ---")
        query_text = all_doc_chunks[focused_chunk_idx]
        try:
            query_embedding_response = genai.embed_content(model=EMBEDDING_MODEL, content=query_text, task_type="RETRIEVAL_QUERY")
            query_embedding = np.array(query_embedding_response['embedding']).astype('float32').reshape(1, -1)
            k_to_fetch = max(NUM_CHUNKS_TO_FETCH_SEMANTICALLY, NUM_CONTEXT_CHUNKS_TO_USE + 1) 
            distances, faiss_indices_ret = faiss_index.search(query_embedding, k=k_to_fetch)
            retrieved_indices = list(faiss_indices_ret[0])
            final_context_indices = [focused_chunk_idx]
            for idx in retrieved_indices:
                if len(final_context_indices) >= NUM_CONTEXT_CHUNKS_TO_USE: break
                if idx != focused_chunk_idx and idx not in final_context_indices and 0 <= idx < len(all_doc_chunks):
                    final_context_indices.append(idx)
            original_context_indices = final_context_indices
            context_text_list = [all_doc_chunks[i] for i in original_context_indices]
            source_of_context += f" + {len(context_text_list)-1} FAISS neighbors"
            print(f"--- Terminal Log: Q Gen - Focused context indices: {original_context_indices} ---")
        except Exception as e_faiss_focus:
            print(f"--- FAISS query error (focused_chunk_idx mode): {e_faiss_focus} ---")
            original_context_indices = [focused_chunk_idx]
            context_text_list = [all_doc_chunks[focused_chunk_idx]]
            source_of_context += " (FAISS failed, using only focused chunk)"
    elif focused_chunk_idx is not None and (0 <= focused_chunk_idx < len(all_doc_chunks)):
         original_context_indices = [focused_chunk_idx]
         context_text_list = [all_doc_chunks[focused_chunk_idx]]
         source_of_context = f"Focused on Chunk {focused_chunk_idx + 1} (No FAISS or invalid index, using only focused chunk)"
    elif not previous_question_text: 
        source_of_context = "Q1 - From Available Shuffled"
        if 'available_chunk_indices' not in st.session_state or not st.session_state.available_chunk_indices:
            if len(all_doc_chunks) > 0:
                st.session_state.available_chunk_indices = list(range(len(all_doc_chunks)))
                random.shuffle(st.session_state.available_chunk_indices)
            else: st.error("No substantive chunks to select from for Q1."); return None, []
        if st.session_state.available_chunk_indices:
            indices_to_use_for_q1 = []
            for _ in range(NUM_CONTEXT_CHUNKS_TO_USE):
                if st.session_state.available_chunk_indices:
                    indices_to_use_for_q1.append(st.session_state.available_chunk_indices.pop(0)) 
                else: break
            if indices_to_use_for_q1:
                original_context_indices = indices_to_use_for_q1[:] 
                context_text_list = [all_doc_chunks[i] for i in original_context_indices if 0 <= i < len(all_doc_chunks)]
        if not context_text_list: 
            if len(all_doc_chunks) == 0: st.error("No substantive chunks for Q1 context fallback."); return None, []
            indexed_chunks = list(enumerate(all_doc_chunks))
            sorted_indexed_chunks = sorted(indexed_chunks, key=lambda x: len(x[1]), reverse=True)
            top_chunks_with_indices = sorted_indexed_chunks[:NUM_CONTEXT_CHUNKS_TO_USE]
            context_text_list = [chunk_text for _, chunk_text in top_chunks_with_indices]
            original_context_indices = [idx for idx, _ in top_chunks_with_indices]
            source_of_context = "Q1 - Longest Chunks (Fallback)"
    elif difficulty == "harder": 
        source_of_context = "New Section (Correct Answer)"
        if st.session_state.available_chunk_indices:
            indices_to_use_new_section = []
            for _ in range(NUM_CONTEXT_CHUNKS_TO_USE): 
                if st.session_state.available_chunk_indices: 
                    indices_to_use_new_section.append(st.session_state.available_chunk_indices.pop(0)) 
                else: break
            if indices_to_use_new_section:
                original_context_indices = indices_to_use_new_section[:]
                context_text_list = [all_doc_chunks[i] for i in original_context_indices if 0 <= i < len(all_doc_chunks)]
        if not context_text_list: 
            if faiss_index and previous_question_text: 
                query_text_for_vector_search = previous_question_text
                try:
                    query_embedding_response = genai.embed_content(model=EMBEDDING_MODEL, content=query_text_for_vector_search, task_type="RETRIEVAL_QUERY")
                    query_embedding = np.array(query_embedding_response['embedding']).astype('float32').reshape(1, -1)
                    distances, faiss_indices_ret = faiss_index.search(query_embedding, k=NUM_CHUNKS_TO_FETCH_SEMANTICALLY)
                    original_context_indices = list(faiss_indices_ret[0]) 
                    context_text_list = [all_doc_chunks[i] for i in original_context_indices if 0 <= i < len(all_doc_chunks)][:NUM_CONTEXT_CHUNKS_TO_USE]
                    original_context_indices = original_context_indices[:len(context_text_list)] 
                except Exception as e_faiss_harder: 
                    print(f"--- FAISS query error (harder): {e_faiss_harder} ---") 
            source_of_context = "New Section Fallback (FAISS on Prev Q for Harder)"
    elif difficulty == "simpler" and previous_question_text: 
        if st.session_state.get('in_heatmap_quiz_mode', False) and st.session_state.get('heatmap_quiz_source_chunk_idx') is not None and faiss_index:
            focused_idx_for_simpler = st.session_state.heatmap_quiz_source_chunk_idx
            source_of_context = f"Focused Simpler on Chunk {focused_idx_for_simpler} + FAISS (from prev_q)"
            query_text = previous_question_text 
            try:
                query_embedding_response = genai.embed_content(model=EMBEDDING_MODEL, content=query_text, task_type="RETRIEVAL_QUERY")
                query_embedding = np.array(query_embedding_response['embedding']).astype('float32').reshape(1, -1)
                distances, faiss_indices_ret = faiss_index.search(query_embedding, k=max(NUM_CHUNKS_TO_FETCH_SEMANTICALLY, NUM_CONTEXT_CHUNKS_TO_USE +1))
                retrieved_indices = list(faiss_indices_ret[0])
                final_context_indices = [focused_idx_for_simpler]
                for idx in retrieved_indices:
                    if len(final_context_indices) >= NUM_CONTEXT_CHUNKS_TO_USE: break
                    if idx != focused_idx_for_simpler and idx not in final_context_indices and 0 <= idx < len(all_doc_chunks):
                        final_context_indices.append(idx)
                original_context_indices = final_context_indices
                context_text_list = [all_doc_chunks[i] for i in original_context_indices]
                print(f"--- Terminal Log: Q Gen - Focused Simpler context: {original_context_indices} ---")
            except Exception as e_faiss_focused_simpler:
                print(f"--- FAISS query error (focused simpler mode): {e_faiss_focused_simpler} ---")
                if 0 <= focused_idx_for_simpler < len(all_doc_chunks):
                    original_context_indices = [focused_idx_for_simpler]
                    context_text_list = [all_doc_chunks[focused_idx_for_simpler]]
                source_of_context += " (FAISS failed, using only focused chunk)"
        elif faiss_index: 
            source_of_context = "Same Topic (Incorrect Answer - FAISS)"
            query_text_for_vector_search = previous_question_text 
            try:
                query_embedding_response = genai.embed_content(model=EMBEDDING_MODEL, content=query_text_for_vector_search, task_type="RETRIEVAL_QUERY")
                query_embedding = np.array(query_embedding_response['embedding']).astype('float32').reshape(1, -1)
                distances, faiss_indices_ret = faiss_index.search(query_embedding, k=NUM_CHUNKS_TO_FETCH_SEMANTICALLY)
                original_context_indices = list(faiss_indices_ret[0])
                context_text_list = [all_doc_chunks[i] for i in original_context_indices if 0 <= i < len(all_doc_chunks)][:NUM_CONTEXT_CHUNKS_TO_USE]
                original_context_indices = original_context_indices[:len(context_text_list)] 
            except Exception as e_faiss_simpler: 
                print(f"--- FAISS query error (simpler): {e_faiss_simpler} ---")
        else:
            source_of_context += " (FAISS Index Missing for simpler)"
            if previous_question_text and st.session_state.get('current_question_context_indices'): 
                original_context_indices = st.session_state.current_question_context_indices
                context_text_list = [all_doc_chunks[i] for i in original_context_indices if 0 <= i < len(all_doc_chunks)]
    
    if not context_text_list and all_doc_chunks: 
        new_source_part = " + Final Random Fallback" if source_of_context else "Final Random Fallback"
        source_of_context += new_source_part
        num_to_sample_random = min(NUM_CONTEXT_CHUNKS_TO_USE, len(all_doc_chunks))
        if num_to_sample_random > 0:
            original_context_indices = random.sample(list(range(len(all_doc_chunks))), num_to_sample_random)
            context_text_list = [all_doc_chunks[i] for i in original_context_indices]
        else: 
            context_text_list = all_doc_chunks[:NUM_CONTEXT_CHUNKS_TO_USE] 
            original_context_indices = list(range(len(context_text_list)))

    if not context_text_list: 
        st.error("Failed to get any context for question generation after all fallbacks.")
        return None, [] 
    
    print(f"--- Terminal Log: Context Source: {source_of_context}. Num context: {len(context_text_list)}. Indices: {original_context_indices} ---")
    context_to_send = "\n\n---\n\n".join(context_text_list)
    max_context_chars = 8000 
    if len(context_to_send) > max_context_chars: context_to_send = context_to_send[:max_context_chars] + "..."
    
    if st.session_state.get('in_heatmap_quiz_mode', False):
        difficulty_prompt_instruction = f"Generate a question of average difficulty specifically focused on the core concepts within the 'Provided Text Context'. The document's primary objective is: '{doc_objective}'."
        if st.session_state.get('heatmap_quiz_last_answer_incorrect', False) : 
             difficulty_prompt_instruction = f"The user answered the previous question on this topic incorrectly. Generate a new, different question of average or simpler difficulty that targets the core concept of the 'Provided Text Context' to help reinforce understanding. The document's primary objective is: '{doc_objective}'."
    elif not previous_question_text :
        difficulty_prompt_instruction = f"Generate a question of average difficulty based on the provided context. The document's primary objective is: '{doc_objective}'."
    elif difficulty == "harder": 
        difficulty_prompt_instruction = f"The user answered the previous question correctly. You are now being provided context from a new, different section of the document. The document's primary objective is: '{doc_objective}'. Generate a question of average difficulty that tests understanding of the core concepts presented in this new context. Aim to explore a different aspect or principle if the context allows."
    elif difficulty == "simpler" and previous_question_text: 
        difficulty_prompt_instruction = f"The user answered the previous question incorrectly. Generate another question of average difficulty that targets the core concept of the previous question, using straightforward language based on the provided context (which is related to the failed question) to help reinforce understanding."
    else: 
        difficulty_prompt_instruction = f"Generate a question of average difficulty based on the provided context. The document's primary objective is: '{doc_objective}'."
    
    prompt = f"""
    You are an expert quiz generator. The subject of the document is '{subject}'.
    {difficulty_prompt_instruction}
    Guidelines:
    1. The question must test understanding of principles related to '{subject}' and the document's objective, directly covered in the 'Provided Text Context'.
    2. NO METADATA QUESTIONS. Focus strictly on the substance of insurance principles.
    3. Generate 4 plausible options (A, B, C, D).
    4. Ensure exactly ONE option is unambiguously correct according to the 'Provided Text Context'.
    5. Incorrect options must be relevant but clearly wrong based *only* on the 'Provided Text Context'.
    6. Output Format (EXACTLY as shown, using these precise labels and newlines, no extra markdown around labels):
    Question: [Your question here]
    A: [Option A text]
    B: [Option B text]
    C: [Option C text]
    D: [Option D text]
    Correct Answer: [Letter ONLY, e.g., C]
    Explanation: [Brief explanation from context.]
    Provided Text Context:\n---\n{context_to_send}\n---\nGenerate the question now.
    """ 
    llm_response_obj = None; response_text = None; max_retries = 3; retry_delay = 5; parsed_data = None
    try:
        for attempt in range(max_retries):
            try:
                print(f"--- Terminal Log: Sending prompt to Gemini AI (Attempt {attempt + 1}/{max_retries}) ---")
                safety_settings = { 
                    genai.types.HarmCategory.HARM_CATEGORY_HATE_SPEECH: genai.types.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
                    genai.types.HarmCategory.HARM_CATEGORY_HARASSMENT: genai.types.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
                    genai.types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: genai.types.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
                    genai.types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: genai.types.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
                }
                llm_response_obj = model.generate_content(prompt, safety_settings=safety_settings, request_options={'timeout': 60}) 
                print(f"--- Terminal Log: Received response from Gemini AI (Attempt {attempt + 1}) ---")
                if llm_response_obj and llm_response_obj.candidates and hasattr(llm_response_obj.candidates[0].content, 'parts') and llm_response_obj.candidates[0].content.parts:
                    response_text = llm_response_obj.candidates[0].content.parts[0].text.strip()
                    if response_text: 
                        print(f"--- Terminal Log: Got response text (Attempt {attempt + 1}). ---")
                        break 
                    else: 
                        reason = llm_response_obj.candidates[0].finish_reason.name if llm_response_obj.candidates[0].finish_reason else "Empty content part"
                        print(f"--- Terminal Log: AI Response Text Empty (Attempt {attempt + 1}). Reason: {reason} ---")
                elif llm_response_obj and not llm_response_obj.candidates: 
                    reason = llm_response_obj.prompt_feedback.block_reason.name if llm_response_obj.prompt_feedback and llm_response_obj.prompt_feedback.block_reason else "No candidates"
                    print(f"--- Terminal Log: AI Response No Candidates (Attempt {attempt + 1}). Reason: {reason} ---")
                else: 
                    print(f"--- Terminal Log: AI Response Invalid/Null (Attempt {attempt + 1}) ---")
                if attempt < max_retries - 1: 
                    print(f"--- Terminal Log: Retrying LLM call in {retry_delay}s... ---")
                    time.sleep(retry_delay)
                else: 
                    st.error(f"AI response issue after {max_retries} attempts.")
                    return None, [] 
            except Exception as e_api: 
                print(f"--- Terminal Log: LLM API Error (Attempt {attempt + 1}/{max_retries}): {type(e_api).__name__}: {e_api} ---")
                if attempt < max_retries - 1: 
                    print(f"--- Terminal Log: Retrying LLM API call in {retry_delay}s... ---")
                    time.sleep(retry_delay)
                else: 
                    raise e_api 
        if not response_text: 
            st.error("Failed to get valid response text from AI after retries.")
            return None, [] 
        
        print(f"--- Terminal Log: Raw AI Response (first 200 chars): {response_text[:200]}... ---")
        parsed_data = {}; options = {}
        patterns = {
            "question": r"^\**Question\**\s*[:\-]\s*(.+?)\s*(?=\n\s*\**\s*[A-Z]\s*[:\.\)]|\Z)",
            "A": r"\n\s*\**\s*[Aa]\s*[:\.\)]\s*\**(.+?)\**\s*(?=\n\s*\**\s*[Bb]\s*[:\.\)]|\Z)",
            "B": r"\n\s*\**\s*[Bb]\s*[:\.\)]\s*\**(.+?)\**\s*(?=\n\s*\**\s*[Cc]\s*[:\.\)]|\Z)",
            "C": r"\n\s*\**\s*[Cc]\s*[:\.\)]\s*\**(.+?)\**\s*(?=\n\s*\**\s*[Dd]\s*[:\.\)]|\Z)",
            "D": r"\n\s*\**\s*[Dd]\s*[:\.\)]\s*\**(.+?)\**\s*(?=\n\s*\**\s*Correct Answer\s*[:\.\)]|\Z)",
            "correct_answer": r"\n\s*\**\s*Correct Answer\s*[:\.\)]\s*\**\s*\[?([A-Da-d])\]?\s*\**",
            "explanation": r"\n\s*\**\s*Explanation\s*[:\.\)]\s*\**([\s\S]+?)\**\s*(\Z|\n\s*\**\s*(Question:|A:|B:|C:|D:|Correct Answer:))"
        }
        def extract_with_pattern(key, pattern, text_to_search):
            flags = re.IGNORECASE
            if key == "explanation": flags |= re.DOTALL
            match = re.search(pattern, text_to_search, flags)
            if match: 
                content = match.group(1).strip()
                if key == "question": 
                    content = re.sub(r'\.(?=[a-zA-Z0-9])', '. ', content) 
                    content = re.sub(r'([a-zA-Z])([0-9])', r'\1 \2', content) 
                    content = re.sub(r'([0-9])([a-zA-Z])', r'\1 \2', content)
                    content = re.sub(r'([a-zA-Z0-9])(\()', r'\1 \2', content) 
                    content = re.sub(r'(\))([a-zA-Z0-9])', r'\1 \2', content) 
                    common_stuck_words = ['and', 'of', 'in', 'to', 'for', 'with', 'on', 'at', 'from', 'by', 'about', 
                                          'million', 'thousand', 'hundred', 'dollar', 'dollars', 'euro', 'euros', 
                                          'usd', 'eur', 'premium', 'policy', 'insurance', 'claim', 'risk']
                    for word_to_space in common_stuck_words:
                        content = re.sub(rf'([a-zA-Z0-9,\.]+)({re.escape(word_to_space)})([a-zA-Z0-9,\.]+)', rf'\1 \2 \3', content, flags=re.IGNORECASE)
                        content = re.sub(rf'([a-zA-Z0-9,\.]+)({re.escape(word_to_space)})', rf'\1 \2', content, flags=re.IGNORECASE)
                        content = re.sub(rf'({re.escape(word_to_space)})([a-zA-Z0-9,\.]+)', rf'\1 \2', content, flags=re.IGNORECASE)
                    content = re.sub(r'\s{2,}', ' ', content).strip() 
                return content
            print(f"--- Terminal Log: Parsing Warning: Could not find '{key}' in response. ---")
            return None
        
        parsed_data["question"] = extract_with_pattern("Question", patterns["question"], response_text)
        options["A"] = extract_with_pattern("Option A", patterns["A"], response_text)
        options["B"] = extract_with_pattern("Option B", patterns["B"], response_text)
        options["C"] = extract_with_pattern("Option C", patterns["C"], response_text)
        options["D"] = extract_with_pattern("Option D", patterns["D"], response_text)
        parsed_data["options"] = {k: v for k, v in options.items() if v is not None} 
        correct_ans_raw = extract_with_pattern("Correct Answer", patterns["correct_answer"], response_text)
        if correct_ans_raw: parsed_data["correct_answer"] = correct_ans_raw.upper()
        else: parsed_data["correct_answer"] = None 
        parsed_data["explanation"] = extract_with_pattern("Explanation", patterns["explanation"], response_text)
        
        req_keys = ["question", "options", "correct_answer", "explanation"];
        if not all(k in parsed_data and parsed_data[k] is not None for k in req_keys) or len(parsed_data.get("options", {})) != 4:
            print(f"--- Terminal Log: PARSING FAILED. Data: {parsed_data}. Options count: {len(parsed_data.get('options', {}))}. ---")
            raise ValueError("Parsing failed. Missing required parts or options incomplete.")
        if parsed_data["correct_answer"] not in ["A", "B", "C", "D"]: 
            print(f"--- Terminal Log: Invalid correct answer: '{parsed_data['correct_answer']}'. ---")
            raise ValueError(f"Invalid correct answer: '{parsed_data['correct_answer']}'")
        
        print(f"--- Terminal Log: Successfully parsed question data. Indices: {original_context_indices}. ---")
        return parsed_data, original_context_indices 
    
    except ValueError as ve_parsing: 
        print(f"--- Terminal Log: Parsing ValueError: {ve_parsing}. Raw response (first 500): '{response_text[:500] if response_text else 'None'}' ---")
        st.error("AI response format issue."); 
        traceback.print_exc()
        return None, [] 
    except Exception as e_overall: 
        print(f"--- Terminal Log: Overall Q Gen Error: {type(e_overall).__name__}: {e_overall} ---")
        safety_fb = "";
        try: 
            if llm_response_obj and hasattr(llm_response_obj, 'prompt_feedback') and llm_response_obj.prompt_feedback and hasattr(llm_response_obj.prompt_feedback, 'block_reason') and llm_response_obj.prompt_feedback.block_reason: safety_fb = f"Reason: {llm_response_obj.prompt_feedback.block_reason.name}"
            elif llm_response_obj and llm_response_obj.candidates and hasattr(llm_response_obj.candidates[0], 'finish_reason') and llm_response_obj.candidates[0].finish_reason: safety_fb = f"Finish Reason: {llm_response_obj.candidates[0].finish_reason.name}"
        except Exception as e_safety: print(f"--- Terminal Log: Error getting safety feedback: {e_safety} ---")
        st.error(f"AI communication or processing error. {safety_fb}"); 
        traceback.print_exc()
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
            print("--- TERMINAL DEBUG: GEMINI_API_KEY not found in st.secrets! ---")
            raise KeyError("API key not found in st.secrets")
        
        st.session_state.gemini_api_key = st.secrets["GEMINI_API_KEY"]
        if not st.session_state.gemini_api_key: 
             print("--- TERMINAL DEBUG: GEMINI_API_KEY is present in secrets but is empty! ---")
             raise ValueError("GEMINI_API_KEY value is empty in secrets.")

        print(f"--- Terminal DEBUG: Configuring with API key ending: ...{st.session_state.gemini_api_key[-4:] if st.session_state.gemini_api_key and len(st.session_state.gemini_api_key) >=4 else 'KEY_IS_SHORT_EMPTY_OR_NONE'} ---")
        genai.configure(api_key=st.session_state.gemini_api_key)
        print("--- Terminal DEBUG: genai.configure called. ---")
        st.session_state.gemini_model = genai.GenerativeModel('gemini-1.5-flash')
        print("--- Terminal DEBUG: GenerativeModel created. ---")
        st.session_state.llm_configured = True
        print("--- Terminal DEBUG: Gemini AI Configured successfully. ---")
    else:
        print("--- Terminal DEBUG: Gemini AI was already configured. ---")
except KeyError as ke: 
    error_message = f"Gemini Config Error: {ke} - Check secrets."
    print(f"--- TERMINAL DEBUG: LLM Config KeyError: {error_message} ---")
    traceback.print_exc() 
    st.error(error_message) 
    st.session_state.llm_configured = False
except Exception as e_gemini: 
    error_message = f"AI Config Error: {e_gemini}"
    print(f"--- TERMINAL DEBUG: LLM Config Exception: {error_message} ---")
    traceback.print_exc() 
    st.error(error_message) 
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
        print(f"--- New File Detected: {uploaded_file.name}. Triggering full processing and state reset. ---")
    elif not st.session_state.get('vector_store_setup_done', False):
        needs_full_processing = True
        print(f"--- Same File ('{uploaded_file.name}'), but previous processing indicates setup was not completed. Retrying processing. ---")
    
    if needs_full_processing:
        print("--- Resetting session states for new file processing. ---")
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
            print(f"--- Post-Docling: Prepared {len(st.session_state.substantive_chunks_for_quiz)} chunks with details. ---")

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
                print(f"--- Dynamically Determined Subject (after processing): '{st.session_state.dynamic_doc_subject}' ---")
                print(f"--- Dynamically Determined Objective (after processing): '{st.session_state.dynamic_doc_objective}' ---")

            if st.session_state.get('dynamic_doc_subject'):
                st.session_state.current_doc_subject = st.session_state.dynamic_doc_subject
            elif uploaded_file: 
                st.session_state.current_doc_subject = uploaded_file.name.rsplit('.', 1)[0].replace('_', ' ').replace('-', ' ')
            else: 
                st.session_state.current_doc_subject = CORE_SUBJECT
            if not st.session_state.get('dynamic_doc_objective') and st.session_state.current_doc_subject != CORE_SUBJECT:
                st.session_state.dynamic_doc_objective = f"To learn about {st.session_state.current_doc_subject}."
            elif not st.session_state.get('dynamic_doc_objective'):
                st.session_state.dynamic_doc_objective = "To understand general concepts."
            
            if st.session_state.substantive_chunks_for_quiz and st.session_state.llm_configured: 
                with st.spinner(f"Building FAISS index for '{uploaded_file.name}'..."):
                    setup_success = setup_vector_store(st.session_state.substantive_chunks_for_quiz, st.session_state.gemini_api_key, uploaded_file.name)
                    st.session_state.vector_store_setup_done = setup_success
                    if not setup_success:
                        print(f"--- FAISS VS setup FAILED for {uploaded_file.name}. ---")
            else:
                print("--- Skipping FAISS setup: No substantive chunks from Docling or LLM not configured. ---")
                st.session_state.vector_store_setup_done = False
        else: 
            st.session_state.vector_store_setup_done = False
    else: 
        if uploaded_file:
            print(f"--- Document '{uploaded_file.name}' already processed and vector store setup. Using cached data. ---")
            if st.session_state.get('dynamic_doc_subject'):
                st.session_state.current_doc_subject = st.session_state.dynamic_doc_subject
            elif st.session_state.current_doc_subject == CORE_SUBJECT:
                st.session_state.current_doc_subject = uploaded_file.name.rsplit('.', 1)[0].replace('_', ' ').replace('-', ' ')
    
    if uploaded_file and not st.session_state.get('vector_store_setup_done') and \
       st.session_state.get('substantive_chunks_for_quiz') is not None :
        st.warning(f"Doc '{uploaded_file.name}' processed, but vector store setup might have failed. Quiz may use basic context.")

# --- App Logic (Conditions for displaying quiz UI, summary, etc.) ---

if st.session_state.get('in_heatmap_quiz_mode', False) and uploaded_file is not None:
    if uploaded_file:
        st.caption(f"Document: {uploaded_file.name}")
        
    st.subheader(f"Focused Quiz on Topic from Document Section") 
    if st.session_state.get('heatmap_quiz_source_chunk_idx') is not None:
         st.caption(f"Question based on content from document section related to chunk {st.session_state.heatmap_quiz_source_chunk_idx + 1}.")

    if not st.session_state.get('current_question_data'):
        with st.spinner("Generating focused question..."):
            difficulty_for_hm_q = "simpler" if st.session_state.get('heatmap_quiz_last_answer_incorrect', False) else "average"
            prev_q_text_for_hm = None
            if st.session_state.get('heatmap_quiz_last_answer_incorrect') and st.session_state.get('current_question_data'):
                prev_q_text_for_hm = st.session_state.current_question_data.get('question')

            q_data, context_indices = generate_quiz_question(
                model=st.session_state.gemini_model,
                subject=st.session_state.current_doc_subject, 
                difficulty=difficulty_for_hm_q, 
                previous_question_text=prev_q_text_for_hm,
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
            st.session_state.heatmap_quiz_source_chunk_idx = None
            st.session_state.current_question_data = None 
            st.session_state.show_summary = True 
            st.rerun()

    if st.session_state.current_question_data:
        q_data = st.session_state.current_question_data
        quiz_container = st.container(border=True)
        with quiz_container:
            st.markdown(f"**{q_data['question']}**")
            options_dict = q_data.get("options", {})
            options_list = [f"{k}: {options_dict.get(k, f'Err {k}')}" for k in ["A","B","C","D"] if k in options_dict]
            
            idx = None
            radio_key_suffix = str(st.session_state.get('heatmap_quiz_source_chunk_idx', 'na')) 
            if q_data.get('question'): 
                 radio_key_suffix += "_" + str(hash(q_data['question']))

            if st.session_state.show_explanation and st.session_state.user_answer:
                try: idx = [opt.startswith(f"{st.session_state.user_answer}:") for opt in options_list].index(True)
                except ValueError: idx = None
            
            selected_opt = st.radio("Select:", options_list, index=idx, key=f"hm_q_radio_{radio_key_suffix}", disabled=st.session_state.show_explanation, label_visibility="collapsed")

            if not st.session_state.show_explanation:
                st.session_state.user_answer = selected_opt.split(":")[0] if selected_opt and ":" in selected_opt else None

            submit_hm_q_button = st.button("Submit Answer", key="submit_hm_q_btn", disabled=st.session_state.show_explanation)

            if submit_hm_q_button:
                if st.session_state.user_answer is None:
                    st.warning("Please select an answer.")
                else:
                    st.session_state.total_questions_answered += 1 
                    correct = q_data.get("correct_answer", "Error")
                    if correct == "Error":
                        st.session_state.feedback_message = "Error: Could not determine correct answer."
                        st.session_state.last_answer_correct = None
                        st.session_state.heatmap_quiz_last_answer_incorrect = False 
                    elif st.session_state.user_answer == correct:
                        st.session_state.feedback_message = "Correct!"
                        st.session_state.last_answer_correct = True
                        st.session_state.heatmap_quiz_last_answer_incorrect = False
                    else:
                        st.session_state.feedback_message = f"Incorrect. Correct was: **{correct}**."
                        st.session_state.last_answer_correct = False
                        st.session_state.heatmap_quiz_last_answer_incorrect = True 
                        st.session_state.incorrectly_answered_questions.append({
                            "question_number": f"Focused (Topic from chunk {st.session_state.heatmap_quiz_source_chunk_idx + 1})",
                            "question_text": q_data["question"],
                            "your_answer": st.session_state.user_answer,
                            "correct_answer": correct,
                            "explanation": q_data.get("explanation", "N/A"),
                            "options_dict": q_data.get("options", {})
                        })
                    
                    context_indices_to_update = st.session_state.heatmap_quiz_current_context_indices 
                    if context_indices_to_update and st.session_state.last_answer_correct is not None:
                        for idx_status in context_indices_to_update:
                            if 0 <= idx_status < len(st.session_state.chunk_review_status):
                                current_status = st.session_state.chunk_review_status[idx_status]
                                if st.session_state.last_answer_correct: 
                                    st.session_state.chunk_review_status[idx_status] = 1 
                                else: 
                                    if current_status in [0, 1, 4]: 
                                        st.session_state.chunk_review_status[idx_status] = 2 
                                    elif current_status == 2: 
                                        st.session_state.chunk_review_status[idx_status] = 3
                                print(f"--- Terminal Log: Heatmap Quiz - Chunk {idx_status} status updated to {st.session_state.chunk_review_status[idx_status]} ---")
                    
                    st.session_state.show_explanation = True
                    st.rerun()

            if st.session_state.show_explanation:
                if st.session_state.feedback_message:
                    if st.session_state.last_answer_correct: st.success(st.session_state.feedback_message)
                    else: st.error(st.session_state.feedback_message)
                st.caption(f"Explanation: {q_data.get('explanation', 'N/A')}")

                if st.session_state.last_answer_correct:
                    if st.button("Back to Quiz Summary", key="hm_q_correct_to_summary_btn"):
                        st.session_state.in_heatmap_quiz_mode = False
                        st.session_state.heatmap_quiz_source_chunk_idx = None
                        st.session_state.current_question_data = None
                        st.session_state.show_summary = True
                        st.session_state.heatmap_quiz_last_answer_incorrect = False
                        st.rerun()
                else: 
                    if st.session_state.last_answer_correct is False : 
                        if st.button("Try Another Question on this Topic", key="hm_q_retry_topic_btn"):
                            st.session_state.current_question_data = None 
                            st.session_state.show_explanation = False
                            st.session_state.feedback_message = None
                            st.rerun()
            
            st.divider()
            if st.button("End Focused Quiz & View Summary", key="hm_q_stop_summary_btn"):
                st.session_state.in_heatmap_quiz_mode = False
                st.session_state.heatmap_quiz_source_chunk_idx = None
                st.session_state.current_question_data = None 
                st.session_state.show_summary = True
                st.session_state.heatmap_quiz_last_answer_incorrect = False
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
    with col1:
        st.metric(label="Score", value=f"{(num_correct / total_answered * 100):.1f}%" if total_answered > 0 else "N/A")
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
                options_for_item = item.get("options_dict", {})
                your_answer_letter = item.get('your_answer', 'N/A')
                correct_answer_letter = item.get('correct_answer', 'N/A')
                your_answer_text = options_for_item.get(your_answer_letter, f"'{your_answer_letter}' (Text not found)")
                correct_answer_text = options_for_item.get(correct_answer_letter, f"'{correct_answer_letter}' (Text not found)")
                st.write(f"> Your Answer: **{your_answer_letter}**. {your_answer_text}")
                st.write(f"> Correct Answer: **{correct_answer_letter}**. {correct_answer_text}")
                st.caption(f"Explanation: {item.get('explanation', 'N/A')}")
                st.markdown("---")
    elif total_answered == 0:
        st.info("No questions were answered in this session.")
    st.divider() 
    
    # Chunk Detail Expander - Placed before the heatmap's own expander
    if st.session_state.get('show_heatmap_chunk_detail', False) and \
       st.session_state.get('selected_heatmap_chunk_index') is not None:
        selected_idx = st.session_state.selected_heatmap_chunk_index
        doc_chunk_details_list_summary = st.session_state.get('doc_chunk_details', []) 

        if 0 <= selected_idx < len(doc_chunk_details_list_summary):
            chunk_info = doc_chunk_details_list_summary[selected_idx]
            full_headings_str_for_title = " -> ".join(chunk_info.get("full_headings_list", [])) if chunk_info.get("full_headings_list") else "General Content"
            display_texts = []
            current_chunk_headings_tuple = tuple(chunk_info.get("full_headings_list", []))

            if selected_idx > 0:
                prev_chunk_info = doc_chunk_details_list_summary[selected_idx - 1]
                if tuple(prev_chunk_info.get("full_headings_list", [])) == current_chunk_headings_tuple:
                    display_texts.append(prev_chunk_info.get("text", "Error: Prev text missing"))
                    if chunk_info.get("text"): display_texts.append("---") 
            current_text = chunk_info.get('text', 'Error: Current text missing')
            display_texts.append(f"<b>{current_text}</b>")
            if selected_idx < len(doc_chunk_details_list_summary) - 1:
                next_chunk_info = doc_chunk_details_list_summary[selected_idx + 1]
                if tuple(next_chunk_info.get("full_headings_list", [])) == current_chunk_headings_tuple:
                    if chunk_info.get("text"): display_texts.append("---")
                    display_texts.append(next_chunk_info.get("text", "Error: Next text missing"))
            
            expander_label_detail = f"Path: {full_headings_str_for_title} (Context for Paragraph Index {selected_idx + 1})"
            with st.expander(expander_label_detail, expanded=True): 
                content_html = ""
                first_text_segment_in_expander = True 
                for i, text_segment in enumerate(display_texts):
                    if text_segment == "---":
                        if not first_text_segment_in_expander: 
                            content_html += "<hr style='margin-top: 2px; margin-bottom: 2px; border-top: 1px solid #eee;'>" 
                    else:
                        content_html += f"<p style='margin-top: 2px; margin-bottom: 2px; line-height: 1.3;'>{text_segment}</p>"
                    if text_segment != "---":
                        first_text_segment_in_expander = False
                st.markdown(content_html, unsafe_allow_html=True)
                
                col1_exp, col2_exp = st.columns(2)
                with col1_exp:
                    if st.button("Quiz me on this chunk", key=f"quiz_me_btn_summary_{selected_idx}"): 
                        st.session_state.in_heatmap_quiz_mode = True
                        st.session_state.heatmap_quiz_source_chunk_idx = selected_idx
                        st.session_state.current_question_data = None 
                        st.session_state.quiz_started = False 
                        st.session_state.show_summary = False 
                        st.session_state.show_heatmap_chunk_detail = False 
                        st.rerun()
                with col2_exp:
                    if st.button("Close Detail", key=f"close_detail_exp_summary_{selected_idx}"): 
                        st.session_state.show_heatmap_chunk_detail = False
                        st.session_state.selected_heatmap_chunk_index = None
                        st.rerun()
        else: 
            st.session_state.show_heatmap_chunk_detail = False
            st.session_state.selected_heatmap_chunk_index = None
    
    with st.expander("📘 Document Coverage & Performance Heatmap"): # Removed expanded=False
        display_heatmap_grid() 
    
    st.divider()
    if st.button("Start New Quiz Once More", key="start_new_quiz_summary"):
        st.session_state.quiz_started = False
        st.session_state.question_number = 0 
        st.session_state.current_question_data = None
        st.session_state.user_answer = None
        st.session_state.feedback_message = None
        st.session_state.show_explanation = False
        st.session_state.last_answer_correct = None
        st.session_state.incorrectly_answered_questions = []
        st.session_state.total_questions_answered = 0
        st.session_state.show_summary = False
        st.session_state.in_heatmap_quiz_mode = False 
        st.session_state.heatmap_quiz_source_chunk_idx = None
        if st.session_state.get('substantive_chunks_for_quiz'):
            num_chunks = len(st.session_state.substantive_chunks_for_quiz)
            st.session_state.available_chunk_indices = list(range(num_chunks))
            random.shuffle(st.session_state.available_chunk_indices)
            st.session_state.chunk_review_status = [0] * num_chunks
        st.session_state.current_question_context_indices = []
        st.rerun()

elif st.session_state.get('vector_store_setup_done') and \
     st.session_state.get('substantive_chunks_for_quiz') and \
     st.session_state.llm_configured and \
     not st.session_state.get('quiz_started', False) and \
     uploaded_file is not None:
    # st.info("Document Analyzed and ready to test your knowledge") # Original info message
    st.markdown("#### Document Analyzed and ready to test your knowledge") # Using markdown for potentially bolder look
    if st.session_state.current_doc_subject:
        st.markdown(f"**Subject:** {st.session_state.current_doc_subject}")
    if st.session_state.dynamic_doc_objective: 
        st.markdown(f"**Document objective:** {st.session_state.dynamic_doc_objective}")
    
    if not st.session_state.get('vector_store_setup_done', False): 
        st.warning("Note: FAISS index setup may have failed. Quiz will use basic random context selection if so.")
    
    if st.button("Start Quiz!", type="primary", key="start_quiz_main_btn"): 
        st.session_state.quiz_started = True
        st.session_state.question_number = 1
        st.session_state.feedback_message = None 
        st.session_state.show_explanation = False
        st.session_state.last_answer_correct = None
        st.session_state.user_answer = None
        st.session_state.current_question_data = None 
        st.session_state.incorrectly_answered_questions = []
        st.session_state.total_questions_answered = 0
        st.session_state.current_question_context_indices = []
        st.session_state.show_summary = False
        st.session_state.in_heatmap_quiz_mode = False 
        doc_subject_for_q1 = st.session_state.get('dynamic_doc_subject', CORE_SUBJECT)
        if not doc_subject_for_q1 or doc_subject_for_q1 == CORE_SUBJECT:
            if uploaded_file:
                doc_subject_for_q1 = uploaded_file.name.rsplit('.', 1)[0].replace('_', ' ').replace('-', ' ')
            else: doc_subject_for_q1 = CORE_SUBJECT
        st.session_state.current_doc_subject = doc_subject_for_q1
        
        print(f"--- Start Quiz Clicked. Using Subject for Q1: '{st.session_state.current_doc_subject}' ---") 

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
            st.session_state.question_number = 0

elif st.session_state.get('quiz_started', False) and uploaded_file is not None :
    if uploaded_file:
        st.caption(f"Document: {uploaded_file.name}")

    quiz_container = st.container(border=True)
    with quiz_container:
        if st.session_state.current_question_data:
            q_data = st.session_state.current_question_data; doc_subject = st.session_state.current_doc_subject
            st.subheader(f"Question {st.session_state.question_number}"); st.markdown(f"**{q_data['question']}**")
            options_dict = q_data.get("options", {}); 
            options_list = [f"{k}: {options_dict.get(k, f'Err {k}')}" for k in ["A","B","C","D"] if k in options_dict]
            idx = None
            if st.session_state.show_explanation and st.session_state.user_answer:
                try: idx = [opt.startswith(f"{st.session_state.user_answer}:") for opt in options_list].index(True)
                except ValueError: idx = None 
            selected_opt = st.radio("Select:", options_list, index=idx, key=f"q_{st.session_state.question_number}", disabled=st.session_state.show_explanation, label_visibility="collapsed")
            if not st.session_state.show_explanation: st.session_state.user_answer = selected_opt.split(":")[0] if selected_opt and ":" in selected_opt else None
            st.write("---"); submit_btn_type = "primary" if not st.session_state.show_explanation else "secondary"; submit_btn = st.button("Submit Answer", disabled=st.session_state.show_explanation, type=submit_btn_type)
            if submit_btn:
                if st.session_state.user_answer is None: st.warning("Select answer."); st.stop()
                else:
                    st.session_state.total_questions_answered += 1; correct = q_data.get("correct_answer", "Error")
                    if correct == "Error": st.session_state.feedback_message = "Error"; st.session_state.last_answer_correct = None
                    elif st.session_state.user_answer == correct: st.session_state.feedback_message = "Correct!"; st.session_state.last_answer_correct = True
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
                    if st.session_state.current_question_context_indices and st.session_state.last_answer_correct is not None and hasattr(st.session_state, 'chunk_review_status') and st.session_state.chunk_review_status:
                        for idx_status in st.session_state.current_question_context_indices: 
                            if 0 <= idx_status < len(st.session_state.chunk_review_status):
                                current_status = st.session_state.chunk_review_status[idx_status]
                                if st.session_state.last_answer_correct: 
                                    if current_status == 0 or current_status == 4: 
                                        st.session_state.chunk_review_status[idx_status] = 1
                                else: 
                                    if current_status in [0, 1, 4]: 
                                        st.session_state.chunk_review_status[idx_status] = 2 
                                    elif current_status == 2: 
                                        st.session_state.chunk_review_status[idx_status] = 3
                                print(f"--- Terminal Log: Chunk index {idx_status} (quiz): old status {current_status}, new status {st.session_state.chunk_review_status[idx_status]} ---")
                    st.session_state.show_explanation = True; st.rerun()
            feedback_container = st.container()
            with feedback_container:
                if st.session_state.feedback_message:
                    if st.session_state.last_answer_correct is True: st.success(st.session_state.feedback_message)
                    elif st.session_state.last_answer_correct is False: st.error(st.session_state.feedback_message)
                    else: st.warning(st.session_state.feedback_message)
                    if st.session_state.show_explanation: st.caption(f"Explanation: {q_data.get('explanation', 'N/A')}")
            if st.button("Next Question"):
                spinner_message = "Moving to a new section..." if st.session_state.last_answer_correct else "Revisiting this topic..."
                difficulty_for_next_q = "harder" if st.session_state.last_answer_correct else "simpler"
                st.session_state.feedback_message = None; st.session_state.show_explanation = False
                st.session_state.user_answer = None; st.session_state.last_answer_correct = None
                with st.spinner(spinner_message):
                    next_q, context_indices = generate_quiz_question(
                        model=st.session_state.gemini_model, subject=doc_subject, 
                        difficulty=difficulty_for_next_q, previous_question_text=q_data['question'], 
                        all_doc_chunks=st.session_state.substantive_chunks_for_quiz
                    )
                if next_q: 
                    st.session_state.current_question_data = next_q
                    st.session_state.current_question_context_indices = context_indices
                    st.session_state.question_number += 1
                    st.rerun()
                else: st.error(f"Failed to generate next question (type: {difficulty_for_next_q}). Please try again or stop quiz.")
            st.divider()
            if st.button("Stop Quiz"): 
                st.session_state.show_summary = True; st.session_state.quiz_started = False; 
                st.session_state.in_heatmap_quiz_mode = False 
                st.session_state.heatmap_quiz_source_chunk_idx = None
                st.session_state.show_heatmap_chunk_detail = False 
                st.session_state.selected_heatmap_chunk_index = None  
                st.rerun()
        else:
            st.error("Quiz active, but no question data. Error? Stop/restart.")
            if st.button("Stop Quiz (Error State)"): 
                st.session_state.quiz_started = False; st.session_state.show_summary = True; st.rerun()

else: 
    if uploaded_file is None and st.session_state.llm_configured :
        # CHANGE 1: Data Privacy text and tooltip
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
        else:
            pass
