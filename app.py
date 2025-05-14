# app.py (AI_Quiz_Tutor_Upload version)

import streamlit as st
import re
import time
import google.generativeai as genai
import random
import numpy as np
import traceback
import io
import docx # Keep this, we still use it in a helper
import PyPDF2 # Keep this
from pptx import Presentation # Keep this
import faiss # <<< NEW IMPORT >>>

# Ensure chromadb and its embedding_functions are NOT imported

# --- Configuration ---
CORE_SUBJECT = "Insurance Principles" 
EMBEDDING_MODEL = "models/text-embedding-004"
CHROMA_COLLECTION_NAME = "uploaded_doc_chunks" 
NUM_CONTEXT_CHUNKS_TO_USE = 3      # Base number of chunks for final context
MIN_WORDS_FOR_CONTENT_CHUNK = 4 
# For semantic search when not taking from available_chunk_indices:
NUM_CHUNKS_TO_FETCH_SEMANTICALLY = 5 # How many to initially FETCH for semantic search (for simpler or harder-fallback)

# --- Document Reading Helper Functions & Main Loading/Filtering ---
# (get_docx_text, get_pdf_text, get_txt_text, get_pptx_text - Unchanged from response #151)
def get_docx_text(file_object):
    try: doc = docx.Document(file_object); return [p.text.strip() for p in doc.paragraphs if p.text and p.text.strip()]
    except Exception as e: print(f"Error reading DOCX: {e}"); traceback.print_exc(); return None
def get_pdf_text(file_object):
    text_paragraphs = [];
    try:
        pdf_reader = PyPDF2.PdfReader(file_object); print(f"PDF has {len(pdf_reader.pages)} pages.")
        for i, page in enumerate(pdf_reader.pages):
            try:
                page_text = page.extract_text()
                if page_text:
                    paragraphs_on_page = [p.strip() for p in page_text.replace('\r','\n').split('\n\n') if p and p.strip()]
                    for para_block in paragraphs_on_page: text_paragraphs.extend([p.strip() for p in para_block.split('\n') if p and p.strip()])
            except Exception as pdf_page_err: print(f"Warn: PDF page error {i+1}: {pdf_page_err}")
    except Exception as e: print(f"Error reading PDF: {e}"); traceback.print_exc(); return None
    return [p for p in text_paragraphs if p]
def get_txt_text(file_object):
    try:
        try: content_bytes = file_object.getvalue(); content = content_bytes.decode("utf-8", errors="ignore")
        except AttributeError: content = file_object.read()
        text_paragraphs = [p.strip() for p in content.replace('\r','\n').split('\n\n') if p and p.strip()]
        final_paragraphs = [];
        for para_block in text_paragraphs: final_paragraphs.extend([p.strip() for p in para_block.split('\n') if p and p.strip()])
        return [p for p in final_paragraphs if p]
    except Exception as e: print(f"Error reading TXT: {e}"); traceback.print_exc(); return None
def get_pptx_text(file_object):
    try:
        prs = Presentation(file_object); text_paragraphs = []
        for slide in prs.slides:
            for shape in slide.shapes:
                if hasattr(shape, "text_frame") and shape.text_frame and shape.text_frame.text:
                    shape_text = shape.text_frame.text.strip()
                    if shape_text:
                        normalized_text = shape_text.replace('\r','\n').replace('\v','\n'); paras_in_shape = [p.strip() for p in normalized_text.split('\n\n') if p and p.strip()]
                        for para_block in paras_in_shape: text_paragraphs.extend([p.strip() for p in para_block.split('\n') if p and p.strip()])
            if slide.has_notes_slide and slide.notes_slide.notes_text_frame and slide.notes_slide.notes_text_frame.text:
                notes_text = slide.notes_slide.notes_text_frame.text.strip()
                if notes_text:
                    normalized_notes = notes_text.replace('\r','\n').replace('\v','\n'); notes_paras = [p.strip() for p in normalized_notes.split('\n\n') if p and p.strip()]
                    for para_block in notes_paras: text_paragraphs.extend([p.strip() for p in para_block.split('\n') if p and p.strip()])
        return [p for p in text_paragraphs if p]
    except Exception as e: print(f"Error reading PPTX: {e}"); traceback.print_exc(); return None

def load_document_paragraphs_from_upload(uploaded_file):
    # (Unchanged)
    if uploaded_file is None: return None
    file_name = uploaded_file.name; print(f"--- Loading paragraphs from: {file_name} ---")
    try:
        uploaded_file.seek(0); file_bytes = uploaded_file.read(); file_bytes_io = io.BytesIO(file_bytes)
        if file_name.endswith(".docx"): paragraphs = get_docx_text(file_bytes_io)
        elif file_name.endswith(".pdf"): paragraphs = get_pdf_text(file_bytes_io)
        elif file_name.endswith(".pptx"): paragraphs = get_pptx_text(file_bytes_io)
        elif file_name.endswith(".txt"): paragraphs = get_txt_text(io.StringIO(file_bytes.decode("utf-8", errors="ignore")))
        else: st.error(f"Unsupported type: {file_name}."); return None
        if paragraphs is None: print(f"--- Loading failed for {file_name} ---"); return None
        print(f"--- Loaded {len(paragraphs)} raw strings from {file_name} ---"); return paragraphs
    except Exception as e: st.error(f"Error loading '{file_name}': {e}"); traceback.print_exc(); return None

def clean_paragraph(text):
    # (Unchanged)
    if not text: return ""; 
    try: text_cleaned = re.sub(r'[ \t]+', ' ', str(text)); text_stripped = text_cleaned.strip(); return text_stripped if text_stripped is not None else ""
    except Exception as e: print(f"--- Error cleaning text: {e}"); return "" 

def is_likely_title_or_toc(text_line):
    # (Using the stricter filter from response #151)
    text_stripped = text_line.strip();
    if not text_stripped: return True 
    words = text_stripped.split(); num_words = len(words)
    problematic_exact_phrases = [
        "specific insurance definitions and related processes", "the document covers five different topics:",
        "in summary:", "introduction", "outro", "table of contents", "list of figures", "figure:", 
        "fig.", "index", "glossary", "bibliography", "references", "appendix", "contents"
    ]
    for phrase in problematic_exact_phrases:
        if text_stripped.lower().startswith(phrase) and num_words <= (len(phrase.split()) + 4) : print(f"--- FILTERED [H_SpecificPhrase]: '{text_stripped}'"); return True
    if num_words < MIN_WORDS_FOR_CONTENT_CHUNK + 2: 
        is_pattern = False
        if re.match(r"^(Chapter|Section|Part|Appendix)\s+([A-Z\d]+|[IVXLCDM]+)\b", text_stripped, re.IGNORECASE): is_pattern = True
        elif re.match(r"^\d+(\.\d+)*[\.\)]?\s*([A-Z].*|$)", text_stripped): is_pattern = True
        elif re.match(r"^[A-Z]\.[\sA-Z]?", text_stripped): is_pattern = True
        elif re.match(r"^[ivxlcdm]+\.[\sA-Z]?", text_stripped, re.IGNORECASE): is_pattern = True
        elif re.match(r"^[A-Za-z0-9IVXLCDM]{1,10}[\.\)]?$", text_stripped): is_pattern = True 
        elif text_stripped.isupper() and num_words <= 5: is_pattern = True
        if is_pattern: print(f"--- FILTERED [H1 - Short & Strong Title Pattern]: '{text_stripped}'"); return True
    if re.match(r"^(Chapter|Section|Part|Appendix)\s+([A-Z\d]+|[IVXLCDM]+)\b", text_stripped, re.IGNORECASE):
         if num_words <= 7 or (num_words > 1 and words[-1].isdigit() and len(words[-1]) <=3): print(f"--- FILTERED [H1a - TOC Keyword Phrase]: '{text_stripped}'"); return True
    if re.match(r"^\d+(\.\d+)*[\.\s\-\–\—\:]?", text_stripped): 
         if num_words > 1 and words[-1].isdigit() and len(words[-1]) <=3 and num_words <= 12: print(f"--- FILTERED [H1b - Numbered list ending in page#]: '{text_stripped}'"); return True
    if text_stripped.isupper() and num_words <= 10: print(f"--- FILTERED [H2 - ALL CAPS Short]: '{text_stripped}'"); return True
    if ".." in text_stripped and num_words > 1 and words[-1].isdigit() and len(words[-1]) <= 3: print(f"--- FILTERED [H3 - TOC with dots]: '{text_stripped}'"); return True
    if any(keyword in text_stripped.lower() for keyword in ["page "]): 
        if num_words <= 5 : print(f"--- FILTERED [H4 - Page Number Keyword]: '{text_stripped}'"); return True
    if num_words < MIN_WORDS_FOR_CONTENT_CHUNK: print(f"--- FILTERED [H5 - Global Min Words]: '{text_stripped}'"); return True
    return False

def load_clean_filter_paragraphs(uploaded_file_obj):
    # (Unchanged)
    if uploaded_file_obj is None: print("--- load_clean_filter_paragraphs: No file object received, returning None. ---"); return None
    file_name = uploaded_file_obj.name 
    print(f"--- Running load_and_clean_paragraphs for: {file_name} ---")
    raw_paragraphs = load_document_paragraphs_from_upload(uploaded_file_obj) 
    if raw_paragraphs is not None: 
        print(f"--- Starting heuristic filtering for {len(raw_paragraphs)} raw paragraphs ---")
        prepared_chunks = []
        for i, p_text in enumerate(raw_paragraphs):
            if p_text is not None: 
                cleaned_p = clean_paragraph(p_text) 
                if cleaned_p and not is_likely_title_or_toc(cleaned_p): prepared_chunks.append(cleaned_p)
        print(f"--- Doc processing complete. Prepared {len(prepared_chunks)} valid content chunks for {file_name}. ---")
        if not prepared_chunks: st.warning(f"Doc '{file_name}' processed, no valid content chunks found after filtering."); return None
        return prepared_chunks
    else: print(f"--- Failed to load/parse paragraphs from {file_name}. ---"); return None

# <<< Replace your OLD setup_vector_store function with this NEW one >>>
def setup_vector_store(substantive_chunks_list, api_key_for_ef, uploaded_filename="document"):
    """
    Generates embeddings for the provided text chunks and builds a FAISS index.
    Stores the FAISS index and the chunks in st.session_state.
    """
    if not substantive_chunks_list:
        st.warning("FAISS Setup: No substantive chunks provided to build index.")
        st.session_state.faiss_index = None
        st.session_state.faiss_index_chunks = []
        return False # Indicate failure or no index built

    print(f"--- FAISS: Starting setup for {len(substantive_chunks_list)} chunks from {uploaded_filename} ---")
    
    all_embeddings_list = []
    embedding_model_name = EMBEDDING_MODEL # Uses the global constant

    # Batching for embedding generation
    batch_size = 50 # Gemini API often has batch limits for embed_content (e.g., 100, but 50 is safer)
    num_batches = (len(substantive_chunks_list) + batch_size - 1) // batch_size
    
    progress_text = "Generating embeddings for document chunks..."
    progress_bar_embed = st.progress(0, text=progress_text)
    print(f"--- FAISS: Generating embeddings in {num_batches} batches of size {batch_size} ---")

    try:
        for i in range(num_batches):
            start_index = i * batch_size
            end_index = min((i + 1) * batch_size, len(substantive_chunks_list))
            batch_texts = substantive_chunks_list[start_index:end_index]
            
            if not batch_texts: continue # Should not happen if loop range is correct

            print(f"--- FAISS: Embedding Batch {i+1}/{num_batches} ({len(batch_texts)} chunks) ---")
            # Note: genai.embed_content expects a list of strings in 'content'
            # The task_type "RETRIEVAL_DOCUMENT" is appropriate for texts to be stored/indexed.
            response = genai.embed_content(
                model=embedding_model_name,
                content=batch_texts,
                task_type="RETRIEVAL_DOCUMENT"
            )
            batch_embeddings = response['embedding']
            all_embeddings_list.extend(batch_embeddings)
            
            progress_bar_embed.progress(float(end_index / len(substantive_chunks_list)), text=f"{progress_text} (Batch {i+1}/{num_batches})")
            time.sleep(0.5) # Small delay to be kind to API rate limits, adjust if needed

        if not all_embeddings_list or len(all_embeddings_list) != len(substantive_chunks_list):
            st.error("FAISS Setup: Embedding generation failed or produced incorrect number of embeddings.")
            return False

        embeddings_np = np.array(all_embeddings_list).astype('float32') # FAISS expects float32 NumPy array
        dimension = embeddings_np.shape[1]
        print(f"--- FAISS: Embeddings generated. Shape: {embeddings_np.shape} ---")

        print("--- FAISS: Building FAISS index (IndexFlatL2) ---")
        faiss_index = faiss.IndexFlatL2(dimension) # Using L2 distance (Euclidean)
        # For cosine similarity with normalized embeddings, IndexFlatIP would be an option:
        # faiss.normalize_L2(embeddings_np) # Normalize if using IndexFlatIP
        # faiss_index = faiss.IndexFlatIP(dimension)
        
        faiss_index.add(embeddings_np)
        print(f"--- FAISS: Index built. Total vectors in index: {faiss_index.ntotal} ---")

        st.session_state.faiss_index = faiss_index
        st.session_state.faiss_index_chunks = substantive_chunks_list # Store the original texts paired with the index
        st.session_state.vector_store_setup_done = True # Reuse this flag
        progress_bar_embed.empty()
        return True # Indicate success

    except Exception as e:
        print(f"--- Error during FAISS setup (embedding or indexing): {type(e).__name__}: {e} ---")
        traceback.print_exc()
        st.error(f"FAISS index creation failed: {e}")
        st.session_state.faiss_index = None
        st.session_state.faiss_index_chunks = []
        progress_bar_embed.empty()
        return False
# <<< End of new setup_vector_store function >>>

# <<< ADD THIS NEW FUNCTION TO YOUR app.py >>>

# <<< Replace your ENTIRE existing determine_document_theme function with this one >>>
def determine_document_theme(sampled_chunks, llm_model):
    """
    Uses the LLM to determine the core subject and primary learning objective
    from a sample of document chunks.
    """
    if not sampled_chunks:
        print("--- Theme Determination: No chunks provided to determine theme. ---")
        return CORE_SUBJECT, "To understand general concepts from the document." # Fallback

    print(f"--- Theme Determination: Analyzing {len(sampled_chunks)} sampled chunks. ---")
    
    combined_sample_text = ""
    char_limit_for_theme_prompt = 6000 
    
    for chunk in sampled_chunks:
        if len(combined_sample_text) + len(chunk) + 4 < char_limit_for_theme_prompt: 
            combined_sample_text += chunk + "\n---\n"
        else:
            break 
    
    if not combined_sample_text: 
        print("--- Theme Determination: Combined sample text is empty. Using fallback. ---")
        return CORE_SUBJECT, "To learn about the provided content."

    print(f"--- Theme Determination: Sending combined sample (approx {len(combined_sample_text)} chars) to LLM. ---")

    prompt = f"""
    Analyze the following text excerpts from a document. Your goal is to identify its main theme.
    1.  Identify the primary core subject of this document. Be concise and specific (e.g., "Principles of Marine Insurance," "Risk Management in Software Projects," "Introduction to Astrophysics"). Aim for 3-7 words.
    2.  Identify the primary learning objective or purpose of this document from a reader's perspective (e.g., "To understand key components of reinsurance treaties," "To learn how to apply agile methodologies," "To explain the life cycle of stars"). Start with "To..."

    Text Excerpts:
    ---
    {combined_sample_text}
    ---

    Provide your answer in the following exact format, with each item on a new line:
    Core Subject: [Identified core subject here]
    Primary Objective: [Identified primary objective here]
    """

    try:
        # Assuming llm_model is your configured Gemini model instance
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
# <<< End of new determine_document_theme function >>>

# <<< Replace your ENTIRE existing generate_quiz_question function with this one >>>
def generate_quiz_question(model, subject="Document Content", difficulty="average", 
                           previous_question_text=None, all_doc_chunks=None):
    print(f"--- Generating question. Difficulty requested: {difficulty}. Subject: '{subject}'. Prev Q: {'Yes' if previous_question_text else 'No'} ---")
    if not model: st.error("Q Gen: AI Model not configured."); return None
    if not all_doc_chunks: st.error("Q Gen: No document chunks provided (all_doc_chunks)."); return None
    
    faiss_index = st.session_state.get('faiss_index')
    faiss_index_chunks = st.session_state.get('faiss_index_chunks') 

    # Get the dynamically determined objective, with a fallback
    doc_objective = st.session_state.get('dynamic_doc_objective', "To help the reader understand the provided text.")
    if not doc_objective: # Ensure it's not None or empty
        doc_objective = "To help the reader understand the provided text."
    print(f"--- Using Document Objective for Prompt: '{doc_objective}' ---")

    context_text_list = []
    source_of_context = "" 

    # --- Context Selection Logic (unchanged from response #161) ---
    if not previous_question_text: 
        source_of_context = "Q1 - From Available Shuffled"
        print(f"--- Context for Q1: Attempting to use 'available_chunk_indices'. ---")
        if 'available_chunk_indices' not in st.session_state or not st.session_state.available_chunk_indices:
            if len(all_doc_chunks) > 0: # Check if all_doc_chunks is not empty
                st.session_state.available_chunk_indices = list(range(len(all_doc_chunks)))
                random.shuffle(st.session_state.available_chunk_indices)
                print(f"--- Re-initialized and shuffled {len(st.session_state.available_chunk_indices)} available_chunk_indices for Q1 ---")
            else:
                st.error("No substantive chunks to select from for Q1.")
                return None
        
        if st.session_state.available_chunk_indices: # Check again after potential re-initialization
            indices_to_use = []
            for _ in range(NUM_CONTEXT_CHUNKS_TO_USE):
                if st.session_state.available_chunk_indices:
                    indices_to_use.append(st.session_state.available_chunk_indices.pop(0)) 
                else: break
            if indices_to_use:
                context_text_list = [all_doc_chunks[i] for i in indices_to_use if 0 <= i < len(all_doc_chunks)]
                print(f"--- Selected {len(context_text_list)} chunks from new area for Q1 using indices: {indices_to_use}. Remaining available: {len(st.session_state.available_chunk_indices)} ---")
        
        if not context_text_list: 
            print(f"--- Q1 Fallback (available_chunks empty/failed): Selecting {NUM_CONTEXT_CHUNKS_TO_USE} longest chunks from {len(all_doc_chunks)}. ---")
            if len(all_doc_chunks) == 0: st.error("No substantive chunks available for Q1 context fallback."); return None
            sorted_chunks = sorted(all_doc_chunks, key=len, reverse=True)
            context_text_list = sorted_chunks[:NUM_CONTEXT_CHUNKS_TO_USE]
            source_of_context = "Q1 - Longest Chunks (Fallback)"
        print(f"--- Selected {len(context_text_list)} chunks for Q1 context. ---")

    elif difficulty == "harder": 
        source_of_context = "New Section (Correct Answer)"
        print(f"--- Context for New Section (Correct Answer): Attempting to use 'available_chunk_indices'. ---")
        if st.session_state.available_chunk_indices:
            indices_to_use = []
            for _ in range(NUM_CONTEXT_CHUNKS_TO_USE): 
                if st.session_state.available_chunk_indices: 
                    indices_to_use.append(st.session_state.available_chunk_indices.pop(0)) 
                else: break
            if indices_to_use:
                context_text_list = [all_doc_chunks[i] for i in indices_to_use if 0 <= i < len(all_doc_chunks)]
                print(f"--- Selected {len(context_text_list)} chunks from new area using indices: {indices_to_use}. Remaining available: {len(st.session_state.available_chunk_indices)} ---")
        
        if not context_text_list: 
            print("--- No more new unvisited chunks for New Section. Falling back to FAISS search on prev_Q for 'harder'. ---")
            if faiss_index and faiss_index_chunks and previous_question_text:
                query_text_for_vector_search = previous_question_text
                try:
                    print(f"--- FAISS: Embedding query for 'harder' fallback: '{query_text_for_vector_search[:100]}...' ---")
                    query_embedding_response = genai.embed_content(model=EMBEDDING_MODEL, content=query_text_for_vector_search, task_type="RETRIEVAL_QUERY")
                    query_embedding = np.array(query_embedding_response['embedding']).astype('float32').reshape(1, -1)
                    distances, faiss_indices_ret = faiss_index.search(query_embedding, k=NUM_CHUNKS_TO_FETCH_SEMANTICALLY)
                    retrieved_chunks = [faiss_index_chunks[i] for i in faiss_indices_ret[0]]
                    context_text_list = [chunk for chunk in retrieved_chunks if not is_likely_title_or_toc(chunk)][:NUM_CONTEXT_CHUNKS_TO_USE]
                    print(f"--- Fallback 'harder' (FAISS): Retrieved {len(retrieved_chunks)}, kept {len(context_text_list)} after post-filter. ---")
                except Exception as e: print(f"--- Error in fallback FAISS query for 'harder': {e} ---")
            source_of_context = "New Section Fallback (FAISS on Prev Q for Harder)"

    elif difficulty == "simpler" and previous_question_text: 
        source_of_context = "Same Topic (Incorrect Answer - FAISS)"
        if faiss_index and faiss_index_chunks:
            query_text_for_vector_search = previous_question_text 
            print(f"--- FAISS: Embedding query for 'simpler': '{query_text_for_vector_search[:100]}...' ---")
            try:
                query_embedding_response = genai.embed_content(model=EMBEDDING_MODEL, content=query_text_for_vector_search, task_type="RETRIEVAL_QUERY")
                query_embedding = np.array(query_embedding_response['embedding']).astype('float32').reshape(1, -1)
                distances, faiss_indices_ret = faiss_index.search(query_embedding, k=NUM_CHUNKS_TO_FETCH_SEMANTICALLY)
                retrieved_chunks = [faiss_index_chunks[i] for i in faiss_indices_ret[0]]
                context_text_list = [chunk for chunk in retrieved_chunks if not is_likely_title_or_toc(chunk)][:NUM_CONTEXT_CHUNKS_TO_USE] 
                print(f"--- FAISS 'simpler': Retrieved {len(retrieved_chunks)}, kept {len(context_text_list)} after post-filter. ---")
                if not context_text_list and retrieved_chunks: context_text_list = retrieved_chunks[:NUM_CONTEXT_CHUNKS_TO_USE]
            except Exception as e: print(f"--- Error querying FAISS for 'simpler': {e} ---")
        else:
            print("--- FAISS index not available for 'simpler' question. Will use final random fallback. ---")
            source_of_context += " (FAISS Index Missing)"
    
    if not context_text_list and all_doc_chunks:
        source_of_context += " + Final Random Fallback" if source_of_context else "Final Random Fallback"
        # ... (rest of fallback logic unchanged)
        print(f"--- Context: Final fallback to random from {len(all_doc_chunks)} substantive chunks. ---")
        num_to_sample_random = min(NUM_CONTEXT_CHUNKS_TO_USE, len(all_doc_chunks))
        if num_to_sample_random > 0:
            potential_random_chunks = random.sample(all_doc_chunks, min(num_to_sample_random * 2, len(all_doc_chunks)))
            context_text_list = [chunk for chunk in potential_random_chunks if not is_likely_title_or_toc(chunk)][:num_to_sample_random]
            if not context_text_list and all_doc_chunks: context_text_list = random.sample(all_doc_chunks, num_to_sample_random)
        else: context_text_list = all_doc_chunks[:NUM_CONTEXT_CHUNKS_TO_USE] 
        print(f"--- Selected {len(context_text_list)} random chunks for context. ---")
    
    if not context_text_list: st.error("Failed to get any context for question generation."); return None
    
    context_to_send = "\n\n---\n\n".join(context_text_list)
    max_context_chars = 8000 
    if len(context_to_send) > max_context_chars: context_to_send = context_to_send[:max_context_chars] + "..."; print(f"--- Warn: Context truncated to {max_context_chars} ---")
    
    print(f"--- Context Source: {source_of_context} ---")
    print(f"--- FULL CONTEXT TO LLM ({len(context_to_send)} chars): ---")
    for i_chunk, chunk_ctx in enumerate(context_text_list): print(f"CTX CHUNK {i_chunk+1} (Length: {len(chunk_ctx.split())} words):\n'{chunk_ctx}'\n---")
    print("--- END OF FULL CONTEXT ---")
    
    # <<< MODIFIED: Prompt now uses dynamic subject and objective >>>
    difficulty_prompt_instruction = f"Generate a question of average difficulty based on the provided context. The document's primary objective is: '{doc_objective}'." 
    if difficulty == "harder": 
        difficulty_prompt_instruction = f"The user answered the previous question correctly. You are now being provided context from a new, different section of the document. The document's primary objective is: '{doc_objective}'. Generate a question of average difficulty that tests understanding of the core concepts presented in this new context. Aim to explore a different aspect or principle if the context allows."
    elif difficulty == "simpler" and previous_question_text:
        difficulty_prompt_instruction = f"The user answered the previous question incorrectly. The document's primary objective is: '{doc_objective}'. Generate another question of average difficulty that targets the core concept of the previous question, using straightforward language based on the provided context (which is related to the failed question) to help reinforce understanding."
    
    prompt = f"""
    You are an expert quiz generator. The subject of the document is '{subject}'.
    {difficulty_prompt_instruction}

    Guidelines:
    1. The question must test understanding of principles related to '{subject}' and the document's objective, directly covered in the 'Provided Text Context'.
    2. NO METADATA QUESTIONS (e.g., about section numbers, document structure, "based on the text", "according to the document"). Focus strictly on the substance of the subject matter.
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

    Provided Text Context:
    ---
    {context_to_send}
    ---
    Generate the question now.
    """ 
    # (LLM call and Regex Parsing logic - unchanged from response #167)
    llm_response_obj = None; response_text = None; max_retries = 3; retry_delay = 5
    try:
        for attempt in range(max_retries):
            try:
                print(f"--- Sending prompt to Gemini AI (Attempt {attempt + 1}/{max_retries}) ---")
                safety_settings = { gp: gpt.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE for gp, gpt in [(genai.types.HarmCategory.HARM_CATEGORY_HATE_SPEECH, genai.types), (genai.types.HarmCategory.HARM_CATEGORY_HARASSMENT, genai.types), (genai.types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, genai.types), (genai.types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, genai.types)] }
                llm_response_obj = model.generate_content(prompt, safety_settings=safety_settings, request_options={'timeout': 60}) 
                print("--- Received response from Gemini AI ---")
                if llm_response_obj and llm_response_obj.candidates and hasattr(llm_response_obj.candidates[0].content, 'parts') and llm_response_obj.candidates[0].content.parts:
                    response_text = llm_response_obj.candidates[0].content.parts[0].text.strip()
                    if response_text: break 
                    else: reason = llm_response_obj.candidates[0].finish_reason.name if llm_response_obj.candidates[0].finish_reason else "Empty content part"; print(f"AI Response Text Empty (Attempt {attempt + 1}). Reason: {reason}")
                elif llm_response_obj and not llm_response_obj.candidates: reason = llm_response_obj.prompt_feedback.block_reason.name if llm_response_obj.prompt_feedback and llm_response_obj.prompt_feedback.block_reason else "No candidates"; print(f"AI Response No Candidates (Attempt {attempt + 1}). Reason: {reason}")
                else: print(f"AI Response Invalid/Null (Attempt {attempt + 1})")
                if attempt < max_retries - 1: time.sleep(retry_delay); continue
                else: st.error(f"AI response issue after {max_retries} attempts."); return None
            except Exception as e_api: 
                print(f"LLM API Error (Attempt {attempt + 1}/{max_retries}): {type(e_api).__name__}: {e_api}")
                if attempt < max_retries - 1: print(f"--- Retrying in {retry_delay} seconds... ---"); time.sleep(retry_delay)
                else: print(f"--- Max retries reached for API call. ---"); raise e_api 
        if not response_text: st.error("Failed to get valid response text from AI after retries."); return None
        
        print(f"--- Raw AI Response Text ---\n{response_text}\n--- End Raw Response ---")
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
        def extract_with_pattern(key, pattern, text):
            flags = re.IGNORECASE;
            if key == "explanation": flags |= re.DOTALL
            match = re.search(pattern, text, flags)
            if match: 
                # For question text cleanup (from response #161)
                content = match.group(1).strip()
                if key == "question":
                    content = re.sub(r'\.(?=[a-zA-Z0-9])', '. ', content) 
                    content = re.sub(r'([a-zA-Z])([0-9])', r'\1 \2', content) 
                    content = re.sub(r'([0-9])([a-zA-Z])', r'\1 \2', content) 
                    content = re.sub(r'\s{2,}', ' ', content).strip()
                return content
            print(f"--- Parsing Warning: Could not find '{key}' ---"); return None
        
        parsed_data["question"] = extract_with_pattern("Question", patterns["question"], response_text)
        options["A"] = extract_with_pattern("Option A", patterns["A"], response_text)
        options["B"] = extract_with_pattern("Option B", patterns["B"], response_text)
        options["C"] = extract_with_pattern("Option C", patterns["C"], response_text)
        options["D"] = extract_with_pattern("Option D", patterns["D"], response_text)
        parsed_data["options"] = {k: v for k, v in options.items() if v is not None} 
        correct_ans_raw = extract_with_pattern("Correct Answer", patterns["correct_answer"], response_text)
        if correct_ans_raw: parsed_data["correct_answer"] = correct_ans_raw.upper()
        parsed_data["explanation"] = extract_with_pattern("Explanation", patterns["explanation"], response_text)
        
        req_keys = ["question", "options", "correct_answer", "explanation"];
        if not all(k in parsed_data and parsed_data[k] is not None for k in req_keys) or len(parsed_data.get("options", {})) != 4:
            print(f"--- PARSING FAILED ---"); print(f"--- Parsed Data (incomplete): {parsed_data} ---") 
            raise ValueError("Parsing failed. Missing required parts or options incomplete.")
        if parsed_data["correct_answer"] not in ["A", "B", "C", "D"]: raise ValueError(f"Invalid correct answer: '{parsed_data['correct_answer']}'")
        print("--- Successfully parsed question data (using Regex) ---"); return parsed_data
    
    except ValueError as ve_parsing: 
        print(f"Parsing Error: {ve_parsing}"); 
        st.error("AI response format issue."); 
        traceback.print_exc(); return None
    except Exception as e_overall: 
        print(f"Overall Question Generation Error: {type(e_overall).__name__}: {e_overall}")
        safety_fb = "";
        try: 
            if llm_response_obj and hasattr(llm_response_obj, 'prompt_feedback') and llm_response_obj.prompt_feedback and hasattr(llm_response_obj.prompt_feedback, 'block_reason') and llm_response_obj.prompt_feedback.block_reason: safety_fb = f"Reason: {llm_response_obj.prompt_feedback.block_reason.name}"
            elif llm_response_obj and llm_response_obj.candidates and hasattr(llm_response_obj.candidates[0], 'finish_reason') and llm_response_obj.candidates[0].finish_reason: safety_fb = f"Finish Reason: {llm_response_obj.candidates[0].finish_reason.name}"
        except Exception as e_safety: print(f"--- Error trying to get safety feedback: {e_safety} ---")
        st.error(f"AI communication or processing error. {safety_fb}"); traceback.print_exc(); return None
# <<< End of generate_quiz_question function replacement >>>

# --- Streamlit App ---
# (LLM Setup is unchanged)
st.set_page_config(layout="centered", page_title="AI Quiz Tutor")
st.title("AI Quiz Tutor")

if 'llm_configured' not in st.session_state: st.session_state.llm_configured = False
if 'gemini_model' not in st.session_state: st.session_state.gemini_model = None
if 'gemini_api_key' not in st.session_state: st.session_state.gemini_api_key = None
try:
    if not st.session_state.llm_configured:
        print("--- Configuring Gemini AI ---");
        if "GEMINI_API_KEY" not in st.secrets: raise KeyError("API key not found")
        st.session_state.gemini_api_key = st.secrets["GEMINI_API_KEY"]
        genai.configure(api_key=st.session_state.gemini_api_key)
        st.session_state.gemini_model = genai.GenerativeModel('gemini-1.5-flash')
        st.session_state.llm_configured = True; print("--- Gemini AI Configured ---")
except KeyError as ke: st.error(f"{ke} - Check secrets."); st.session_state.llm_configured = False
except Exception as e: st.error(f"AI Config Error: {e}"); st.session_state.llm_configured = False

# --- Initialize Session State ---
st.session_state.setdefault('uploaded_file_key', None) 
st.session_state.setdefault('substantive_chunks_for_quiz', None) 
# st.session_state.setdefault('chroma_collection', None) # Removed for FAISS
st.session_state.setdefault('vector_store_setup_done', False) # Still used
st.session_state.setdefault('faiss_index', None) # <<< ADDED for FAISS >>>
st.session_state.setdefault('faiss_index_chunks', []) # <<< ADDED for FAISS >>>
st.session_state.setdefault('available_chunk_indices', []) 

st.session_state.setdefault('quiz_started', False); st.session_state.setdefault('current_question_data', None)
st.session_state.setdefault('question_number', 0); st.session_state.setdefault('user_answer', None)
st.session_state.setdefault('feedback_message', None); st.session_state.setdefault('show_explanation', False)
st.session_state.setdefault('last_answer_correct', None); st.session_state.setdefault('incorrectly_answered_questions', [])
st.session_state.setdefault('total_questions_answered', 0); st.session_state.setdefault('show_summary', False)
st.session_state.setdefault('current_doc_subject', CORE_SUBJECT)

# --- Initialize Session State ---
# ... (other st.session_state.setdefault lines) ...
st.session_state.setdefault('available_chunk_indices', []) 
# <<< ADD THESE LINES for dynamic theme >>>
st.session_state.setdefault('dynamic_doc_subject', None)
st.session_state.setdefault('dynamic_doc_objective', None)
# <<< END ADDITION >>>
st.session_state.setdefault('quiz_started', False); 
# ... (rest of session state inits) ...

# --- File Uploader ---
uploaded_file = st.file_uploader(
    "Upload your document (DOCX, PDF, PPTX, or TXT)",
    type=["docx", "pdf", "pptx", "txt"], key="file_uploader" 
)

# --- Document Processing Triggered by File Upload ---
# Find this entire block in your app.py and replace it with the version below
if uploaded_file is not None:
    current_file_key = f"{uploaded_file.name}_{uploaded_file.size}" 
    if st.session_state.uploaded_file_key != current_file_key or not st.session_state.get('vector_store_setup_done', False):
        print(f"--- New File: {uploaded_file.name}. Processing... ---"); st.session_state.uploaded_file_key = current_file_key
        
        # Reset all relevant states for a new file or failed previous setup
        st.session_state.substantive_chunks_for_quiz = None
        st.session_state.vector_store_setup_done = False 
        st.session_state.faiss_index = None          
        st.session_state.faiss_index_chunks = []     
        st.session_state.available_chunk_indices = [] 
        st.session_state.dynamic_doc_subject = None  # Reset dynamic theme
        st.session_state.dynamic_doc_objective = None # Reset dynamic theme

        st.session_state.quiz_started = False
        st.session_state.current_question_data = None
        st.session_state.question_number = 0
        st.session_state.incorrectly_answered_questions = []
        st.session_state.total_questions_answered = 0
        st.session_state.show_summary = False
        st.session_state.feedback_message = None
        st.session_state.show_explanation = False
        # st.session_state.current_doc_subject will be set after theme determination or fallback
        
        substantive_chunks = load_clean_filter_paragraphs(uploaded_file) 
        st.session_state.substantive_chunks_for_quiz = substantive_chunks 

        # Determine document theme
        if st.session_state.substantive_chunks_for_quiz:
            with st.spinner("Determining document theme..."):
                num_chunks = len(st.session_state.substantive_chunks_for_quiz)
                sampled_indices = []
                if num_chunks > 0: sampled_indices.extend(list(range(min(2, num_chunks)))) 
                if num_chunks > 5: sampled_indices.extend([num_chunks // 3, min(num_chunks // 3 + 1, num_chunks - 1)])
                if num_chunks > 8: sampled_indices.extend([min(num_chunks * 2 // 3, num_chunks - 1), min(num_chunks * 2 // 3 + 1, num_chunks - 1)])
                if num_chunks > 3: sampled_indices.extend(list(range(max(0, num_chunks - 2), num_chunks))) 
                
                unique_valid_indices = sorted(list(set(i for i in sampled_indices if 0 <= i < num_chunks)))
                final_sample_indices = unique_valid_indices[:8] 
                
                sampled_chunks_for_theme = [st.session_state.substantive_chunks_for_quiz[i] for i in final_sample_indices]

                if sampled_chunks_for_theme:
                    subject, objective = determine_document_theme(sampled_chunks_for_theme, st.session_state.gemini_model)
                    st.session_state.dynamic_doc_subject = subject
                    st.session_state.dynamic_doc_objective = objective
                    print(f"--- Dynamically Determined Subject: {subject} ---")
                    print(f"--- Dynamically Determined Objective: {objective} ---")
                else:
                    print("--- Not enough chunks for smart sampling to determine theme. Using default. ---")
                    st.session_state.dynamic_doc_subject = CORE_SUBJECT # Fallback
                    st.session_state.dynamic_doc_objective = "To understand the content of the document." # Fallback

        # Set current_doc_subject based on dynamic determination or fallbacks
        if st.session_state.get('dynamic_doc_subject'):
            st.session_state.current_doc_subject = st.session_state.dynamic_doc_subject
        elif uploaded_file: 
            st.session_state.current_doc_subject = uploaded_file.name.rsplit('.', 1)[0].replace('_', ' ').replace('-', ' ')
            print(f"--- Using filename as subject: {st.session_state.current_doc_subject} ---")
            if not st.session_state.get('dynamic_doc_objective'): # If objective also not set
                 st.session_state.dynamic_doc_objective = f"To learn about {st.session_state.current_doc_subject}."
        else: 
            st.session_state.current_doc_subject = CORE_SUBJECT
            if not st.session_state.get('dynamic_doc_objective'): # If objective also not set
                 st.session_state.dynamic_doc_objective = "To understand general concepts."
        
        # Setup FAISS vector store if chunks were extracted and LLM is configured
        if st.session_state.substantive_chunks_for_quiz and st.session_state.llm_configured:
            print("--- Preparing to setup FAISS vector store for filtered chunks... ---")
            with st.spinner(f"Analyzing '{uploaded_file.name}' with FAISS... This may take a moment."):
                setup_success = setup_vector_store(
                    st.session_state.substantive_chunks_for_quiz, 
                    st.session_state.gemini_api_key, 
                    uploaded_file.name
                )
                st.session_state.vector_store_setup_done = setup_success 

                if setup_success:
                    if st.session_state.faiss_index_chunks: 
                        st.session_state.available_chunk_indices = list(range(len(st.session_state.faiss_index_chunks)))
                        random.shuffle(st.session_state.available_chunk_indices)
                        print(f"--- FAISS: Initialized and shuffled {len(st.session_state.available_chunk_indices)} available_chunk_indices ---")
                    else:
                         st.session_state.available_chunk_indices = [] 
                         print(f"--- FAISS Warning: No faiss_index_chunks after successful setup? available_chunk_indices empty. ---")
                    print(f"--- FAISS VS setup OK for {uploaded_file.name} with {len(st.session_state.substantive_chunks_for_quiz)} chunks. ---")
                else:
                    print(f"--- FAISS VS setup failed for {uploaded_file.name}. ---")
        
        # Display status after processing attempt
        if st.session_state.vector_store_setup_done: # This is the line (or similar) that was erroring
            st.success(f"Doc '{uploaded_file.name}' (Subject: '{st.session_state.current_doc_subject}') analyzed with FAISS and ready!")
        elif st.session_state.substantive_chunks_for_quiz: 
            st.warning(f"Doc '{uploaded_file.name}' processed, but FAISS index creation failed. Quiz might use basic context.")
        else: 
            st.error(f"Could not process '{uploaded_file.name}'. Check file or try another.")
# <<< End of the "if uploaded_file is not None:" block replacement >>>

# --- App Logic ---
# (Summary report is unchanged)
if st.session_state.show_summary:
    st.header("Quiz Summary"); total_answered=st.session_state.total_questions_answered; incorrect_list=st.session_state.incorrectly_answered_questions; num_incorrect=len(incorrect_list); num_correct = total_answered - num_incorrect
    col1, col2 = st.columns([1, 3]);
    with col1: st.metric(label="Score", value=f"{(num_correct / total_answered * 100):.1f}%" if total_answered > 0 else "N/A")
    with col2: st.write(f"**Total:** {total_answered}, **Correct:** {num_correct}, **Incorrect:** {num_incorrect}")
    st.divider()
    if not incorrect_list and total_answered > 0 : st.balloons(); st.success("Perfect!")
    elif incorrect_list:
        st.subheader("Review Incorrect:");
        for item in incorrect_list: st.error(f"**Q{item['question_number']}: {item['question_text']}**"); st.write(f"> Your Ans: {item['your_answer']}, Correct Answer: {item['correct_answer']}"); st.caption(f"Explanation: {item['explanation']}"); st.divider()
    elif total_answered == 0: st.info("No questions answered.")
    st.divider()
    if st.button("Start New Quiz"):
        st.session_state.quiz_started = False; st.session_state.question_number = 0; st.session_state.current_question_data = None; st.session_state.user_answer = None; st.session_state.feedback_message = None; st.session_state.show_explanation = False; st.session_state.last_answer_correct = None; st.session_state.incorrectly_answered_questions = []; st.session_state.total_questions_answered = 0; st.session_state.show_summary = False
        # Reset available chunks if starting a new quiz on the same document (or handle if new doc is uploaded)
        if 'substantive_chunks_for_quiz' in st.session_state and st.session_state.substantive_chunks_for_quiz:
            st.session_state.available_chunk_indices = list(range(len(st.session_state.substantive_chunks_for_quiz)))
            random.shuffle(st.session_state.available_chunk_indices)
        st.rerun()

# Condition 1: Ready to Start Quiz
elif st.session_state.substantive_chunks_for_quiz is not None and st.session_state.llm_configured and not st.session_state.quiz_started and uploaded_file is not None:
    st.info(f"Ready to test your knowledge on the uploaded document: '{uploaded_file.name}' (Topic: {st.session_state.current_doc_subject})")
    if not st.session_state.get('vector_store_setup_done', False): 
        st.warning("Note: FAISS index setup may have failed. Quiz will use basic random context selection if so.")

    if st.button("Start Quiz!", type="primary"):
        print(f"--- Start Quiz Clicked for: {uploaded_file.name} ---")
        st.session_state.quiz_started = True
        st.session_state.question_number = 1
        st.session_state.feedback_message = None
        st.session_state.show_explanation = False
        st.session_state.last_answer_correct = None
        st.session_state.user_answer = None
        st.session_state.current_question_data = None
        st.session_state.incorrectly_answered_questions = []
        st.session_state.total_questions_answered = 0

        doc_subject = st.session_state.current_doc_subject 
        with st.spinner("Generating first question..."):
            q_data = generate_quiz_question(
                model=st.session_state.gemini_model, 
                subject=doc_subject, 
                difficulty="average", 
                all_doc_chunks=st.session_state.substantive_chunks_for_quiz
                # previous_question_text is implicitly None for Q1
            ) # Ensure this parenthesis closes the call on its own line or correctly
        st.session_state.current_question_data = q_data # This must be on a new line
        if st.session_state.current_question_data is None: 
            st.error("Failed to generate Q1.")
            st.session_state.quiz_started = False
            st.session_state.question_number = 0
        else: 
            st.rerun()

# Condition 2: Quiz in Progress
elif st.session_state.quiz_started and uploaded_file is not None :
    quiz_container = st.container(border=True)
    with quiz_container:
        if st.session_state.current_question_data:
            q_data = st.session_state.current_question_data; doc_subject = st.session_state.current_doc_subject
            st.subheader(f"Question {st.session_state.question_number}"); st.markdown(f"**{q_data['question']}**")
            options_dict = q_data.get("options", {}); options_list = [f"{k}: {options_dict.get(k, f'Err {k}')}" for k in ["A","B","C","D"]]
            idx = None;
            if st.session_state.show_explanation and st.session_state.user_answer:
                try: idx = [opt.startswith(f"{st.session_state.user_answer}:") for opt in options_list].index(True)
                except ValueError: print(f"Warn: No index for ans '{st.session_state.user_answer}'"); idx = None
            selected_opt = st.radio("Select:", options_list, index=idx, key=f"q_{st.session_state.question_number}", disabled=st.session_state.show_explanation, label_visibility="collapsed")
            if not st.session_state.show_explanation: st.session_state.user_answer = selected_opt.split(":")[0] if selected_opt and ":" in selected_opt else None
            st.write("---"); submit_btn_type = "primary" if not st.session_state.show_explanation else "secondary"; submit_btn = st.button("Submit Answer", disabled=st.session_state.show_explanation, type=submit_btn_type)
            if submit_btn:
                if st.session_state.user_answer is None: st.warning("Select answer."); st.stop()
                else:
                    st.session_state.total_questions_answered += 1; correct = q_data.get("correct_answer", "Error")
                    if correct == "Error": st.error("Cannot check."); st.session_state.feedback_message = "Error"; st.session_state.last_answer_correct = None
                    elif st.session_state.user_answer == correct: st.session_state.feedback_message = "Correct!"; st.session_state.last_answer_correct = True
                    else: st.session_state.feedback_message = f"Incorrect. Correct: **{correct}**."; st.session_state.last_answer_correct = False; st.session_state.incorrectly_answered_questions.append({"question_number": st.session_state.question_number, "question_text": q_data["question"], "your_answer": st.session_state.user_answer, "correct_answer": correct, "explanation": q_data.get("explanation", "N/A")})
                    st.session_state.show_explanation = True; print(f"--- Q{st.session_state.question_number} Sub: User={st.session_state.user_answer}, Correct={correct}, Result={st.session_state.last_answer_correct} ---"); st.rerun()
            feedback_container = st.container()
            with feedback_container:
                 if st.session_state.feedback_message:
                     if st.session_state.last_answer_correct is True: st.success(st.session_state.feedback_message)
                     elif st.session_state.last_answer_correct is False: st.error(st.session_state.feedback_message)
                     else: st.warning(st.session_state.feedback_message)
                     if st.session_state.show_explanation: st.caption(f"Explanation: {q_data.get('explanation', 'N/A')}")
# Inside "Condition 2: Quiz in Progress", find and replace this entire "Next Question" button block:
                 if st.button("Next Question"):
                     spinner_message = ""
                     difficulty_for_next_q = ""
                     if st.session_state.last_answer_correct:
                         spinner_message = "Moving to a new section..."
                         difficulty_for_next_q = "harder" # Signal to get new area context
                     else: # Incorrect answer
                         spinner_message = "Revisiting this topic..."
                         difficulty_for_next_q = "simpler" # Signal to use semantic search on previous Q

                     print(f"--- Next Q Clicked --- User was {st.session_state.last_answer_correct}. Requesting type: {difficulty_for_next_q} ---")

                     st.session_state.feedback_message = None
                     st.session_state.show_explanation = False
                     st.session_state.user_answer = None
                     st.session_state.last_answer_correct = None
                     with st.spinner(spinner_message):
                          next_q = generate_quiz_question(
                              model=st.session_state.gemini_model, 
                              subject=doc_subject, 
                              difficulty=difficulty_for_next_q, 
                              previous_question_text=q_data['question'], 
                              all_doc_chunks=st.session_state.substantive_chunks_for_quiz
                          ) # Ensure this parenthesis closes the call correctly

                     # The 'if next_q:' MUST start on a new line, correctly indented
                     if next_q: 
                         st.session_state.current_question_data = next_q
                         st.session_state.question_number += 1
                         print(f"New Q generated. Q{st.session_state.question_number}")
                         st.rerun()
                     else: 
                         st.error(f"Failed to generate next question (type: {difficulty_for_next_q}). Please try again or stop quiz.")
                         # No st.stop() here, allow user to click again or stop
            st.divider()
            if st.button("Stop Quiz"): print("--- Stop Clicked ---"); st.session_state.show_summary = True; st.session_state.quiz_started = False; st.rerun()
        else:
             st.error("Quiz active, but no question data. Error? Stop/restart.")
             if st.button("Stop Quiz (Error State)"): 
                 st.session_state.quiz_started = False; st.session_state.question_number = 0; st.session_state.current_question_data = None; st.session_state.user_answer = None; st.session_state.feedback_message = None; st.session_state.show_explanation = False; st.session_state.last_answer_correct = None; st.session_state.incorrectly_answered_questions = []; st.session_state.total_questions_answered = 0; st.session_state.show_summary = False;
                 st.rerun()

# Condition 3: Waiting for file upload or Setup Failed
elif uploaded_file is None and st.session_state.llm_configured :
    st.info("Please upload a document (DOCX, PDF, PPTX, or TXT) to begin the analysis.")
elif not st.session_state.llm_configured:
    st.warning("AI Model configuration failed. Please check API key and secrets setup.")
    st.caption("Ensure your `GEMINI_API_KEY` is correctly placed in `.streamlit/secrets.toml` and is valid.")
else: 
    if 'uploaded_file_key' in st.session_state and st.session_state.uploaded_file_key is not None and st.session_state.substantive_chunks_for_quiz is None: st.error("Document processing failed after upload.")
    elif uploaded_file is None: st.info("Please upload a document to begin.")
    else: st.error("An unexpected state occurred.")
    st.info("Try uploading your document again. Check format/content if problems persist.")