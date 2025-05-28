from docling.document_converter import DocumentConverter
from docling.datamodel.base_models import DocumentStream
import io
import traceback

pdf_file_path = "/Users/jonathanimpens/Downloads/Hello.pdf" # IMPORTANT: Change this to your actual PDF path

print(f"--- Attempting to process: {pdf_file_path} ---")
try:
    with open(pdf_file_path, "rb") as f:
        file_bytes = f.read()

    buf = io.BytesIO(file_bytes)
    source = DocumentStream(name="test.pdf", stream=buf)

    print("--- Initializing DocumentConverter... ---")
    converter = DocumentConverter()

    print("--- Converting document... ---")
    convert_result = converter.convert(source)

    if convert_result and convert_result.document:
        docling_doc_obj = convert_result.document
        print(f"--- Document converted successfully by Docling. Number of text elements found: {len(docling_doc_obj.texts if hasattr(docling_doc_obj, 'texts') else 'N/A')} ---")
        # You could try to access some content if you want, e.g.:
        # if docling_doc_obj.texts:
        #     print(f"First text element content (first 100 chars): {docling_doc_obj.texts[0].text[:100]}")
    else:
        print("--- Docling conversion did not return a document object or result was None. ---")

except Exception as e:
    print(f"--- An error occurred during Docling processing ---")
    print(f"Error type: {type(e).__name__}")
    print(f"Error message: {e}")
    print("Traceback:")
    traceback.print_exc()

print("--- Script finished. ---")