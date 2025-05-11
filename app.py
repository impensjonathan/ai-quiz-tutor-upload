# app.py (AI_Quiz_Tutor_Upload version)

import docx
import streamlit as st
import re
import time
import google.generativeai as genai
import random
import numpy as np
import chromadb
import chromadb.utils.embedding_functions as embedding_functions
import traceback
import io
import PyPDF2
from pptx import Presentation

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

# --- Vector Store Setup ---
# (Unchanged)
def setup_vector_store(substantive_chunks_list, api_key_for_ef, uploaded_filename="document"):
    # ... (rest of function unchanged) ...
    if not substantive_chunks_list: st.warning("VS Setup: No substantive chunks."); return None 
    print(f"--- Setting up vector store for {len(substantive_chunks_list)} chunks from {uploaded_filename} ---")
    try:
        client = chromadb.Client(); print("--- ChromaDB client (in-memory) ---")
        collection_name_for_doc = f"{CHROMA_COLLECTION_NAME}_{re.sub(r'[^a-zA-Z0-9_-]', '_', uploaded_filename)}"
        google_ef = embedding_functions.GoogleGenerativeAiEmbeddingFunction(api_key=api_key_for_ef, model_name=EMBEDDING_MODEL, task_type="retrieval_document")
        print(f"--- Using Google EF for ChromaDB: {EMBEDDING_MODEL} ---")
        try: client.delete_collection(name=collection_name_for_doc); print(f"--- Deleted old collection: {collection_name_for_doc} ---")
        except: print(f"--- Collection {collection_name_for_doc} not found or delete error (ok). ---")
        collection = client.create_collection(name=collection_name_for_doc, embedding_function=google_ef); print(f"--- Collection '{collection_name_for_doc}' created ---")
        chunk_ids = [f"chunk_{i}" for i in range(len(substantive_chunks_list))]
        batch_size = 100; num_batches = (len(substantive_chunks_list) + batch_size - 1) // batch_size
        print(f"--- Adding {len(substantive_chunks_list)} chunks to ChromaDB in {num_batches} batches... ---")
        progress_bar_embed = st.progress(0, text="Embedding document content...")
        for i in range(0, len(substantive_chunks_list), batch_size):
            batch_texts = substantive_chunks_list[i:i+batch_size]; batch_ids = chunk_ids[i:i+batch_size]
            print(f"--- Embedding Batch {i//batch_size + 1}/{num_batches} ({len(batch_texts)} chunks) ---")
            collection.add(ids=batch_ids, documents=batch_texts)
            print(f"--- Batch {i//batch_size + 1} added to Chroma ---")
            current_progress = min(1.0, (i + len(batch_texts)) / len(substantive_chunks_list)) if len(substantive_chunks_list) > 0 else 1.0
            progress_bar_embed.progress(current_progress, text=f"Embedding content... (Batch {i//batch_size+1}/{num_batches})")
            if num_batches > 1 and (i//batch_size + 1) < num_batches : time.sleep(1)
        progress_bar_embed.empty() 
        final_count = collection.count(); print(f"--- Docs added. Collection now has {final_count} items. ---")
        if final_count != len(substantive_chunks_list): print(f"--- WARNING: ChromaDB count != substantive chunks! ---")
        return collection
    except Exception as e: print(f"--- Error in setup_vector_store (embedding): {e} ---"); traceback.print_exc(); st.error(f"Vector store embedding error: {e}"); return None

# --- Question Generation Function ---
# <<< Replace ONLY this function in your app.py >>>
def generate_quiz_question(model, subject="Document Content", difficulty="average", vector_collection=None, previous_question_text=None, all_doc_chunks=None):
    print(f"--- Generating question. Difficulty requested: {difficulty}. Subject: {subject}. Prev Q: {'Yes' if previous_question_text else 'No'} ---")
    if not model: st.error("Q Gen: AI Model not configured."); return None
    if not all_doc_chunks: st.error("Q Gen: No document chunks provided for context."); return None
    
    context_text_list = []
    source_of_context = "" 

    if not previous_question_text and all_doc_chunks: # Strategy for the FIRST question (Q1)
        source_of_context = "Q1 - From Available Shuffled (Longest if not shuffled/empty)"
        print(f"--- Context for Q1: Attempting to use 'available_chunk_indices'. ---")
        if st.session_state.available_chunk_indices:
            indices_to_use = []
            for _ in range(NUM_CONTEXT_CHUNKS_TO_USE):
                if st.session_state.available_chunk_indices:
                    indices_to_use.append(st.session_state.available_chunk_indices.pop(0)) 
                else: break
            if indices_to_use:
                context_text_list = [all_doc_chunks[i] for i in indices_to_use if i < len(all_doc_chunks)]
                print(f"--- Selected {len(context_text_list)} chunks from new area for Q1 using indices: {indices_to_use}. Remaining available: {len(st.session_state.available_chunk_indices)} ---")
        
        if not context_text_list: # Fallback for Q1 if available_chunks was empty
            print(f"--- Q1 Fallback: Selecting {NUM_CONTEXT_CHUNKS_TO_USE} longest chunks from {len(all_doc_chunks)}. ---")
            if len(all_doc_chunks) == 0: st.error("No substantive chunks available for Q1 context."); return None
            sorted_chunks = sorted(all_doc_chunks, key=len, reverse=True)
            context_text_list = sorted_chunks[:NUM_CONTEXT_CHUNKS_TO_USE]
            source_of_context = "Q1 - Longest Chunks (Fallback)"
        print(f"--- Selected {len(context_text_list)} chunks for Q1 context. ---")


    elif difficulty == "harder" and all_doc_chunks: # User answered correctly, move to a new section
        source_of_context = "New Section (Correct Answer)"
        print(f"--- Context for New Section (Correct Answer): Attempting to use 'available_chunk_indices'. ---")
        if st.session_state.available_chunk_indices:
            indices_to_use = []
            for _ in range(NUM_CONTEXT_CHUNKS_TO_USE): 
                if st.session_state.available_chunk_indices: 
                    indices_to_use.append(st.session_state.available_chunk_indices.pop(0)) 
                else: break
            if indices_to_use:
                context_text_list = [all_doc_chunks[i] for i in indices_to_use if i < len(all_doc_chunks)]
                print(f"--- Selected {len(context_text_list)} chunks from new area using indices: {indices_to_use}. Remaining available: {len(st.session_state.available_chunk_indices)} ---")
        
        if not context_text_list: 
            print("--- No more new unvisited chunks for New Section. Falling back to semantic search on prev_Q for 'harder'. ---")
            if vector_collection and previous_question_text:
                query_text_for_vector_search = previous_question_text
                try:
                    results = vector_collection.query(query_texts=[query_text_for_vector_search], n_results=NUM_CHUNKS_TO_FETCH_SEMANTICALLY) # Use new constant
                    if results and results.get('documents') and results['documents'][0]:
                        retrieved_chunks = results['documents'][0]
                        context_text_list = [chunk for chunk in retrieved_chunks if not is_likely_title_or_toc(chunk)][:NUM_CONTEXT_CHUNKS_TO_USE] # Use standard N chunks
                        print(f"--- Fallback 'harder': Retrieved {len(retrieved_chunks)}, kept {len(context_text_list)} after post-filter. ---")
                except Exception as e: print(f"--- Error in fallback VS query for 'harder': {e} ---")
            source_of_context = "New Section Fallback (Semantic on Prev Q for Harder)"

    elif difficulty == "simpler" and previous_question_text and vector_collection: # Answered incorrectly
        source_of_context = "Same Topic (Incorrect Answer - Semantic)"
        query_text_for_vector_search = previous_question_text 
        print(f"--- Querying VS for 'simpler' question using previous question: '{previous_question_text[:100]}...' ---")
        try:
            # <<< FIXED NameError: Use NUM_CHUNKS_TO_FETCH_SEMANTICALLY >>>
            results = vector_collection.query(query_texts=[query_text_for_vector_search], n_results=NUM_CHUNKS_TO_FETCH_SEMANTICALLY)
            if results and results.get('documents') and results['documents'][0]:
                retrieved_chunks = results['documents'][0]
                context_text_list = [chunk for chunk in retrieved_chunks if not is_likely_title_or_toc(chunk)][:NUM_CONTEXT_CHUNKS_TO_USE] 
                print(f"--- Retrieved {len(retrieved_chunks)} chunks, kept {len(context_text_list)} after post-filter for 'simpler'. ---")
                if not context_text_list and retrieved_chunks: context_text_list = retrieved_chunks[:NUM_CONTEXT_CHUNKS_TO_USE]
        except Exception as e: print(f"--- Error querying VS for 'simpler': {e} ---")
    
    if not context_text_list and all_doc_chunks:
        source_of_context += " + Final Random Fallback" if source_of_context else "Final Random Fallback"
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
    for i_chunk, chunk_ctx in enumerate(context_text_list): 
        print(f"CTX CHUNK {i_chunk+1} (Length: {len(chunk_ctx.split())} words):\n'{chunk_ctx}'\n---")
    print("--- END OF FULL CONTEXT ---")
    
    difficulty_prompt_instruction = "Generate a question of average difficulty based on the provided context." 
    if difficulty == "harder": 
        difficulty_prompt_instruction = "The user answered the previous question correctly. You are now being provided context from a new, different section of the document. Generate a question of average difficulty that tests understanding of the core concepts presented in this new context. Aim to explore a different aspect or principle if the context allows."
    elif difficulty == "simpler" and previous_question_text:
        difficulty_prompt_instruction = "The user answered the previous question incorrectly. Generate another question of average difficulty that targets the core concept of the previous question, using straightforward language based on the provided context (which is related to the failed question) to help reinforce understanding."
    
    prompt = f"""
    You are an expert quiz generator specializing in '{subject}'.
    {difficulty_prompt_instruction}
    Guidelines:
    1. The question must test understanding of '{subject}' principles directly covered in the 'Provided Text Context'.
    2. NO METADATA QUESTIONS (e.g., about section numbers, document structure, "based on the text", "according to the document"). Focus strictly on the substance of insurance principles.
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
    
    # (LLM call and Parsing logic unchanged from response #157)
    response = None; response_text = None; max_retries = 3; retry_delay = 5; llm_response_obj = None
    for attempt in range(max_retries):
        try:
            print(f"--- Sending prompt to Gemini AI (Attempt {attempt + 1}/{max_retries}) ---")
            safety_settings = { gp: gpt.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE for gp, gpt in [(genai.types.HarmCategory.HARM_CATEGORY_HATE_SPEECH, genai.types), (genai.types.HarmCategory.HARM_CATEGORY_HARASSMENT, genai.types), (genai.types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, genai.types), (genai.types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, genai.types)] }
            llm_response_obj = model.generate_content(prompt, safety_settings=safety_settings, request_options={'timeout': 60}) 
            print("--- Received response from Gemini AI ---")
            if llm_response_obj and llm_response_obj.candidates and hasattr(llm_response_obj.candidates[0].content, 'parts') and llm_response_obj.candidates[0].content.parts:
                response_text = llm_response_obj.candidates[0].content.parts[0].text.strip()
                if response_text: response = llm_response_obj; break 
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
    try:
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
            if match: return match.group(1).strip()
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
    except ValueError as ve_parsing: print(f"Parsing Error: {ve_parsing}"); st.error("AI response format issue."); traceback.print_exc(); return None
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
# <<< MODIFIED: Added available_chunk_indices and potentially used_chunk_indices >>>
st.session_state.setdefault('uploaded_file_key', None) 
st.session_state.setdefault('substantive_chunks_for_quiz', None) 
st.session_state.setdefault('chroma_collection', None)
st.session_state.setdefault('vector_store_setup_done', False)
st.session_state.setdefault('available_chunk_indices', []) # For tracking unvisited chunks
# st.session_state.setdefault('used_chunk_indices', set()) # Alternative tracking

st.session_state.setdefault('quiz_started', False); st.session_state.setdefault('current_question_data', None)
st.session_state.setdefault('question_number', 0); st.session_state.setdefault('user_answer', None)
st.session_state.setdefault('feedback_message', None); st.session_state.setdefault('show_explanation', False)
st.session_state.setdefault('last_answer_correct', None); st.session_state.setdefault('incorrectly_answered_questions', [])
st.session_state.setdefault('total_questions_answered', 0); st.session_state.setdefault('show_summary', False)
st.session_state.setdefault('current_doc_subject', CORE_SUBJECT) 

# --- File Uploader ---
uploaded_file = st.file_uploader(
    "Upload your document (DOCX, PDF, PPTX, or TXT)",
    type=["docx", "pdf", "pptx", "txt"], key="file_uploader" 
)

# --- Document Processing Triggered by File Upload ---
if uploaded_file is not None:
    current_file_key = f"{uploaded_file.name}_{uploaded_file.size}" 
    if st.session_state.uploaded_file_key != current_file_key or not st.session_state.vector_store_setup_done:
        print(f"--- File '{uploaded_file.name}' detected. Processing... ---"); st.session_state.uploaded_file_key = current_file_key
        # Reset ALL relevant states for a new file
        st.session_state.substantive_chunks_for_quiz = None; st.session_state.chroma_collection = None; st.session_state.vector_store_setup_done = False
        st.session_state.quiz_started = False; st.session_state.current_question_data = None; st.session_state.question_number = 0
        st.session_state.incorrectly_answered_questions = []; st.session_state.total_questions_answered = 0
        st.session_state.show_summary = False; st.session_state.feedback_message = None; st.session_state.show_explanation = False
        st.session_state.current_doc_subject = CORE_SUBJECT # Default to CORE_SUBJECT for now
        
        substantive_chunks = load_clean_filter_paragraphs(uploaded_file) 
        st.session_state.substantive_chunks_for_quiz = substantive_chunks 

        if st.session_state.substantive_chunks_for_quiz and st.session_state.llm_configured:
            print("--- Preparing to setup vector store for filtered chunks... ---")
            with st.spinner("Analyzing document... Please wait."):
                 collection = setup_vector_store( st.session_state.substantive_chunks_for_quiz, st.session_state.gemini_api_key, uploaded_file.name )
                 if collection is not None:
                     st.session_state.chroma_collection = collection; st.session_state.vector_store_setup_done = True
                     print(f"--- VS setup OK for {uploaded_file.name} with {len(st.session_state.substantive_chunks_for_quiz)} chunks. ---")
                     # <<< MODIFIED: Initialize and shuffle available_chunk_indices >>>
                     st.session_state.available_chunk_indices = list(range(len(st.session_state.substantive_chunks_for_quiz)))
                     random.shuffle(st.session_state.available_chunk_indices)
                     print(f"--- Initialized and shuffled {len(st.session_state.available_chunk_indices)} available_chunk_indices ---")
                 else: print(f"--- VS setup failed for {uploaded_file.name}. ---"); st.session_state.vector_store_setup_done = False
        
        if st.session_state.vector_store_setup_done: st.success(f"Doc '{uploaded_file.name}' analyzed!")
        elif st.session_state.substantive_chunks_for_quiz: st.warning(f"Doc '{uploaded_file.name}' loaded, vector analysis failed/skipped.")
        else: st.error(f"Could not process '{uploaded_file.name}'.")

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
# (Unchanged)
elif st.session_state.substantive_chunks_for_quiz is not None and st.session_state.llm_configured and not st.session_state.quiz_started and uploaded_file is not None:
    st.info(f"Ready to test your knowledge on the uploaded document: '{uploaded_file.name}' (Topic: {st.session_state.current_doc_subject})")
    if not st.session_state.get('vector_store_setup_done', False): st.warning("Note: Advanced context analysis failed. Quiz will use basic random context selection.")
    if st.button("Start Quiz!", type="primary"):
        print(f"--- Start Quiz Clicked for: {uploaded_file.name} ---"); st.session_state.quiz_started = True; st.session_state.question_number = 1; st.session_state.feedback_message = None; st.session_state.show_explanation = False; st.session_state.last_answer_correct = None; st.session_state.user_answer = None; st.session_state.current_question_data = None; st.session_state.incorrectly_answered_questions = []; st.session_state.total_questions_answered = 0
        doc_subject = st.session_state.current_doc_subject 
        with st.spinner("Generating first question..."):
             q_data = generate_quiz_question(model=st.session_state.gemini_model, subject=doc_subject, difficulty="average", vector_collection=st.session_state.chroma_collection, all_doc_chunks=st.session_state.substantive_chunks_for_quiz)
        st.session_state.current_question_data = q_data
        if st.session_state.current_question_data is None: st.error("Failed to generate Q1."); st.session_state.quiz_started = False; st.session_state.question_number = 0
        else: st.rerun()

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
                     if st.button("Next Question"):
                         # <<< MODIFIED: Spinner text & difficulty based on new logic >>>
                         spinner_message = ""
                         difficulty_for_next_q = ""
                         if st.session_state.last_answer_correct:
                             spinner_message = "Finding a new section and generating question..."
                             difficulty_for_next_q = "harder" # Internal signal to fetch new area context
                         else:
                             spinner_message = "Generating simpler question on this topic..."
                             difficulty_for_next_q = "simpler"
                         print(f"--- Next Q Clicked --- Type: {difficulty_for_next_q} ---")
                         # <<< End Modification >>>
                         st.session_state.feedback_message = None; st.session_state.show_explanation = False; st.session_state.user_answer = None; st.session_state.last_answer_correct = None
                         with st.spinner(spinner_message):
                              next_q = generate_quiz_question(model=st.session_state.gemini_model, subject=doc_subject, difficulty=difficulty_for_next_q, vector_collection=st.session_state.chroma_collection, previous_question_text=q_data['question'], all_doc_chunks=st.session_state.substantive_chunks_for_quiz)
                         if next_q: st.session_state.current_question_data = next_q; st.session_state.question_number += 1; print(f"New Q generated. Q{st.session_state.question_number}"); st.rerun()
                         else: st.error(f"Failed to generate {difficulty_for_next_q} q."); st.stop()
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