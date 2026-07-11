import asyncio
import os
from winrt.windows.storage import StorageFile
from winrt.windows.data.pdf import PdfDocument

async def test():
    pdf_path = os.path.abspath("data/Legislativas/Legislativas 1975/PS 1975.pdf")
    print(f"Loading {pdf_path}...")
    try:
        file = await StorageFile.get_file_from_path_async(pdf_path)
        pdf_doc = await PdfDocument.load_from_file_async(file)
        print(f"Success! Page count: {pdf_doc.page_count}")
    except Exception as e:
        print(f"Error: {e}")

asyncio.run(test())
