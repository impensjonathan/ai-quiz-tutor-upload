# app.py (AI_Quiz_Tutor_Upload version - Final Architecture + Safe State)

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
    from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer
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


# ==========================================================================
# ---- ALL LOGIC FUNCTION DEFINITIONS ----
# ==========================================================================

def setup_vector_store(substantive_chunks_list, api_key_for_ef, uploaded_filename="document"):
    if not substantive_chunks_list:
        st.session_state.faiss_index = None
        st.session_state.faiss_index_chunks = []
        return False
    all_embeddings_list = []
    embedding_model_name = EMBEDDING_MODEL
    batch_size = 50
    num_batches = (len(substantive_chunks_list) + batch_size - 1) // batch_size
    progress_bar_embed = st.progress(0, text="Generating embeddings for document chunks...") 
    try:
        for i in range(num_batches):
            start_index = i * batch_size
            end_index = min((i + 1) * batch_size, len(substantive_chunks_list))
            batch_texts = substantive_chunks_list[start_index:end_index]
            if not batch_texts: continue
            response = genai.embed_content(
                model=embedding_model_name, content=batch_texts, task_type="RETRIEVAL_DOCUMENT"
            )
            all_embeddings_list.extend(response['embedding'])
            progress_bar_embed.progress(float(end_index / len(substantive_chunks_list)), text=f"Generating embeddings... (Batch {i+1}/{num_batches})")
            time.sleep(0.1) 
        
        embeddings_np = np.array(all_embeddings_list).astype('float32')
        dimension = embeddings_np.shape[1]
        faiss_index = faiss.IndexFlatL2(dimension)
        faiss_index.add(embeddings_np)
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
        return CORE_SUBJECT, "To understand general concepts from the document."
    combined_sample_text = ""
    char_limit_for_theme_prompt = 6000 
    for chunk in sampled_chunks:
        if len(combined_sample_text) + len(chunk) + 4 < char_limit_for_theme_prompt: 
            combined_sample_text += chunk + "\n---\n"
        else: break 
    if not combined_sample_text: 
        return CORE_SUBJECT, "To learn about the provided content."
    prompt = f"""
    Analyze the following text excerpts from a document. Your goal is to identify its main theme.
    1.  Identify the primary core subject of this document. Be concise and specific. Aim for 3-7 words.
    2.  Identify the primary learning objective or purpose of this document from a reader's perspective. Start with "To..."
    Text Excerpts:\n---\n{combined_sample_text}\n---\n
    Provide your answer in the following exact format, with each item on a new line:
    Core Subject: [Identified core subject here]
    Primary Objective: [Identified primary objective here]
    """
    try:
        response = llm_model.generate_content(prompt, request_options={'timeout': 90}) 
        if response and response.text:
            response_text = response.text.strip()
            core_subject_match = re.search(r"Core Subject:\s*(.+)", response_text, re.IGNORECASE)
            primary_objective_match = re.search(r"Primary Objective:\s*(To .+)", response_text, re.IGNORECASE) 
            determined_subject = core_subject_match.group(1).strip() if core_subject_match else None
            determined_objective = primary_objective_match.group(1).strip() if primary_objective_match else None
            if determined_subject and determined_objective:
                return determined_subject, determined_objective
            else:
                return determined_subject or CORE_SUBJECT, "To understand key aspects of the document."
        else:
            return CORE_SUBJECT, "To learn about the content of the uploaded document."
    except Exception as e:
        return CORE_SUBJECT, "To analyze the provided document." 

def process_document_with_docling(uploaded_file_object, filename):
    if uploaded_file_object is None: return None
    final_content_chunks = []
    start_time = time.time()
    try:
        uploaded_file_object.seek(0) 
        file_bytes = uploaded_file_object.read()
        source = DocumentStream(name=filename, stream=io.BytesIO(file_bytes)) 
        converter = DocumentConverter() 
        docling_doc_obj = converter.convert(source).document
        if not docling_doc_obj: return None
        
        EMBED_MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2" 
        docling_tokenizer = HuggingFaceTokenizer(tokenizer=AutoTokenizer.from_pretrained(EMBED_MODEL_ID), max_tokens=150)
        chunker = HybridChunker(tokenizer=docling_tokenizer, merge_peers=False)
        
        for i, chunk_obj in enumerate(chunker.chunk(docling_doc_obj)):
            text = chunk_obj.text.strip() if hasattr(chunk_obj, 'text') else ""
            meta = chunk_obj.meta if hasattr(chunk_obj, 'meta') else None
            headings = meta.headings if meta and hasattr(meta, 'headings') and meta.headings else []
            if headings and len(text.split()) >= MIN_WORDS_FOR_CONTENT_CHUNK:
                final_content_chunks.append({"text": text, "headings": headings, "original_docling_chunk_index": i})
        return final_content_chunks
    except Exception as e:
        st.error(f"Docling Processing Error: {e}")
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
    </style>
    """, unsafe_allow_html=True)
    
    colors_map = {
        0: {"emoji": "🟦", "label": "Not Quizzed"},
        1: {"emoji": "🟩", "label": "Correct"},
        2: {"emoji": "🟨", "label": "Incorrect (1x)"},
        3: {"emoji": "🟥", "label": "Incorrect (2+x)"},
        4: {"emoji": "🟣", "label": "Reviewed"} 
    }
    
    doc_chunk_details = st.session_state.get('doc_chunk_details', [])
    statuses = st.session_state.get('chunk_review_status', [])
    hover_labels = st.session_state.get('chunk_hover_labels', [])

    if not doc_chunk_details: return
             
    legend_html = "".join([f'<span style="margin-right:15px;">{info["emoji"]} {info["label"]}</span>' for info in colors_map.values()])
    st.markdown(f"**Legend:** {legend_html}", unsafe_allow_html=True)
    
    current_path = [None] * 6 
    last_printed_tuple = None
    cols, c_idx, per_row = None, 0, 15 
    
    for chunk_idx, chunk_detail in enumerate(doc_chunk_details):
        headings = chunk_detail.get("full_headings_list", [])
        h_tuple = tuple(headings)
        
        if h_tuple != last_printed_tuple:
            for level, heading_text in enumerate(headings):
                if level >= len(current_path) or current_path[level] != heading_text:
                    for l_reset in range(level, len(current_path)): current_path[l_reset] = None
                    current_path[level] = heading_text
                    if level == 0: st.markdown(f"<h5>{heading_text}</h5>", unsafe_allow_html=True) 
                    elif level == 1: st.markdown(f"<h6 style='padding-left: 20px;'>{heading_text}</h6>", unsafe_allow_html=True)
                    else: st.markdown(f"<p style='padding-left: {(level)*20}px; font-size:0.9em; font-weight:bold; margin-bottom:2px;'>{heading_text}</p>", unsafe_allow_html=True)
            last_printed_tuple = h_tuple
            cols = st.columns(per_row); c_idx = 0
            
        color_info = colors_map.get(statuses[chunk_idx], colors_map[0])

        def _make_cb(idx):
            def _cb():
                if st.session_state.chunk_review_status[idx] == 0: st.session_state.chunk_review_status[idx] = 4 
                st.session_state.selected_heatmap_chunk_index = idx
                st.session_state.show_heatmap_chunk_detail = True
            return _cb

        if cols is None: cols = st.columns(per_row); c_idx = 0
        with cols[c_idx]:
            st.button(label=color_info['emoji'], key=f"hm_btn_{chunk_idx}", help=hover_labels[chunk_idx], on_click=_make_cb(chunk_idx))
        c_idx = (c_idx + 1) % per_row
        if c_idx == 0: cols = None 

def generate_quiz_question(model, subject="Document Content", difficulty="average", previous_question_text=None, all_doc_chunks=None, focused_chunk_idx=None):
    if not model or not all_doc_chunks: return None, []
    faiss_index = st.session_state.get('faiss_index')
    doc_objective = st.session_state.get('dynamic_doc_objective', "To help the reader understand the provided text.")
    original_context_indices = [] 
    
    if focused_chunk_idx is not None and faiss_index is not None and (0 <= focused_chunk_idx < len(all_doc_chunks)):
        try:
            query_emb = genai.embed_content(model=EMBEDDING_MODEL, content=all_doc_chunks[focused_chunk_idx], task_type="RETRIEVAL_QUERY")['embedding']
            distances, faiss_indices_ret = faiss_index.search(np.array(query_emb).astype('float32').reshape(1, -1), k=NUM_CHUNKS_TO_FETCH_SEMANTICALLY+1)
            final_context_indices = [focused_chunk_idx]
            for idx in faiss_indices_ret[0]:
                if len(final_context_indices) >= NUM_CONTEXT_CHUNKS_TO_USE: break
                if idx != focused_chunk_idx and idx not in final_context_indices and 0 <= idx < len(all_doc_chunks):
                    final_context_indices.append(idx)
            original_context_indices = final_context_indices
        except: original_context_indices = [focused_chunk_idx]
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
            original_context_indices = [i for i in faiss_indices_ret[0] if 0 <= i < len(all_doc_chunks)]
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


# ==========================================================================
# ---- UI PAGE RENDER FUNCTIONS ----
# ==========================================================================

def show_heatmap_quiz_mode(uploaded_file):
    if uploaded_file: st.caption(f"Document: {uploaded_file.name}")
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

def show_summary_mode(uploaded_file):
    if uploaded_file: st.caption(f"Document: {uploaded_file.name}")

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

def show_ready_screen(uploaded_file):
    st.markdown("#### Document Analyzed and ready to test your knowledge") 
    st.markdown(f"**Subject:** {st.session_state.get('current_doc_subject', CORE_SUBJECT)}")
    st.markdown(f"**Document objective:** {st.session_state.get('dynamic_doc_objective', 'To understand provided text')}")
    
    if st.button("Start Quiz!", type="primary"): 
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

def show_normal_quiz_mode(uploaded_file):
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

def handle_file_upload_and_processing():
    uploaded_file = st.file_uploader("Upload your document", type=["docx", "pdf", "pptx", "txt"], key="file_uploader")
    st.caption("Upload of pdf files using a Mac with an Apple M-series chip (M1/M2/M3) does not work")
    
    if uploaded_file:
        st.session_state.uploaded_file_object_ref = uploaded_file
        current_file_key = f"{uploaded_file.name}_{uploaded_file.size}"
        
        if st.session_state.get('uploaded_file_key') != current_file_key or not st.session_state.get('vector_store_setup_done'):
            st.session_state.uploaded_file_key = current_file_key
            st.session_state.substantive_chunks_for_quiz = None 
            st.session_state.doc_chunk_details = [] 
            st.session_state.vector_store_setup_done = False
            st.session_state.faiss_index = None      
            st.session_state.chunk_review_status = [] 
            st.session_state.in_heatmap_quiz_mode = False 
            st.session_state.show_summary = False
            st.session_state.quiz_started = False
            
            with st.spinner("Processing document logic..."):
                docling_output = process_document_with_docling(uploaded_file, uploaded_file.name)
                if docling_output:
                    st.session_state.doc_chunk_details = [{"text": i['text'], "full_headings_list": i.get('headings', [])} for i in docling_output]
                    st.session_state.substantive_chunks_for_quiz = [i['text'] for i in st.session_state.doc_chunk_details]
                    st.session_state.chunk_hover_labels = [(' '.join(i['text'].split()[:50]) + "...") for i in st.session_state.doc_chunk_details]
                    st.session_state.chunk_review_status = [0] * len(st.session_state.substantive_chunks_for_quiz)
                    st.session_state.available_chunk_indices = list(range(len(st.session_state.substantive_chunks_for_quiz)))
                    random.shuffle(st.session_state.available_chunk_indices)
                    
                    if st.session_state.llm_configured:
                        setup_vector_store(st.session_state.substantive_chunks_for_quiz, st.session_state.gemini_api_key, uploaded_file.name)
                        subj, obj = determine_document_theme(st.session_state.substantive_chunks_for_quiz[:8], st.session_state.gemini_model)
                        st.session_state.current_doc_subject = subj
                        st.session_state.dynamic_doc_objective = obj
                        st.rerun() 
    else:
        st.session_state.uploaded_file_object_ref = None
        data_privacy_explanation = "To provide quiz features, this application processes your uploaded document. Snippets of your document are sent to Google's Generative AI services to generate relevant content. Google's API policies state that this data is not used to train their general models. No original documents are stored by this application after your session ends."
        st.markdown("Data Privacy", help=data_privacy_explanation)


# ==========================================================================
# ---- INITIALIZATION & LLM CONFIG ----
# ==========================================================================
if 'llm_configured' not in st.session_state: st.session_state.llm_configured = False
if 'gemini_model' not in st.session_state: st.session_state.gemini_model = None

try:
    if not st.session_state.llm_configured:
        genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
        st.session_state.gemini_model = genai.GenerativeModel('gemini-2.5-flash')
        st.session_state.llm_configured = True
except Exception as e: 
    st.error(f"AI Config Error. Check API key setup in secrets: {e}") 

# --------------------------------------------------------------------------
# SAFE SESSION STATE INITIALIZATION (The Missing Key Fix)
# --------------------------------------------------------------------------
defaults = {
    "uploaded_file_key": None,
    "substantive_chunks_for_quiz": None,
    "vector_store_setup_done": False,
    "in_heatmap_quiz_mode": False,
    "show_summary": False,
    "quiz_started": False,
    "total_questions_answered": 0,
    "incorrectly_answered_questions": [],
    "current_question_data": None,
    "current_question_context_indices": [],
    "heatmap_quiz_current_context_indices": [],
    "heatmap_quiz_last_answer_incorrect": False,
    "user_answer": None,
    "show_explanation": False,
    "feedback_message": None,
    "last_answer_correct": None,
    "chunk_review_status": [],
    "available_chunk_indices": [],
    "selected_heatmap_chunk_index": None,
    "show_heatmap_chunk_detail": False,
    "heatmap_quiz_source_chunk_idx": None,
    "current_doc_subject": CORE_SUBJECT,
    "dynamic_doc_objective": "To understand provided text.",
    "faiss_index": None,
    "faiss_index_chunks": [],
    "chunk_hover_labels": [],
    "uploaded_file_object_ref": None
}

for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v


# ==========================================================================
# ---- EXPLICIT PAGE ROUTER (Executed exactly once per interaction) ----
# ==========================================================================

# 1. HEATMAP FOCUSED QUIZ
if st.session_state.in_heatmap_quiz_mode and st.session_state.substantive_chunks_for_quiz:
    show_heatmap_quiz_mode(st.session_state.uploaded_file_object_ref)
    st.stop()

# 2. SUMMARY SCREEN
if st.session_state.show_summary and st.session_state.substantive_chunks_for_quiz:
    st.title("Quiz Summary")
    show_summary_mode(st.session_state.uploaded_file_object_ref)
    st.stop()

# 3. NORMAL QUIZ
if st.session_state.quiz_started and st.session_state.substantive_chunks_for_quiz:
    st.title("AI Quiz Tutor")
    show_normal_quiz_mode(st.session_state.uploaded_file_object_ref)
    st.stop()

# 4. READY SCREEN (Doc uploaded, not started yet)
if st.session_state.substantive_chunks_for_quiz and st.session_state.vector_store_setup_done:
    st.title("AI Quiz Tutor")
    show_ready_screen(st.session_state.uploaded_file_object_ref)
    st.stop()

# 5. DEFAULT HOME SCREEN / UPLOADER
st.title("AI Quiz Tutor")
handle_file_upload_and_processing()
