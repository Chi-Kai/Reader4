import os
import pickle
import shutil
from functools import lru_cache
from typing import Optional

from fastapi import FastAPI, Request, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from reader3 import (
    Book,
    BookMetadata,
    ChapterContent,
    TOCEntry,
    DEFAULT_LIBRARY_DIR,
    process_epub,
    process_text_file,
    save_to_pickle,
)

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# Where are the book folders located?
BOOKS_DIR = os.environ.get("READER_LIBRARY_DIR", DEFAULT_LIBRARY_DIR)
BOOK_UPLOAD_DIR = os.environ.get("READER_UPLOAD_DIR", "books")
SUPPORTED_IMPORTS = (".epub", ".txt")
os.makedirs(BOOKS_DIR, exist_ok=True)
os.makedirs(BOOK_UPLOAD_DIR, exist_ok=True)


def _slug_from_path(epub_path: str) -> str:
    return os.path.splitext(os.path.basename(epub_path))[0]


def ingest_book(file_path: str):
    slug = _slug_from_path(file_path)
    target_dir = os.path.join(BOOKS_DIR, f"{slug}_data")
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".txt":
        book_obj = process_text_file(file_path, target_dir)
    else:
        book_obj = process_epub(file_path, target_dir)
    save_to_pickle(book_obj, target_dir)
    load_book_cached.cache_clear()
    return slug, target_dir


def select_cover_image(book: Book) -> Optional[str]:
    # Prefer images that look like covers
    for key, path in book.images.items():
        name = os.path.basename(key).lower()
        if "cover" in name or "front" in name:
            return path
    # Otherwise just grab the first available image path
    return next(iter(book.images.values()), None)


def ingest_pending_files():
    """
    Allow users to drop EPUBs into BOOK_UPLOAD_DIR and process
    anything that doesn't yet have a data folder.
    """
    for filename in os.listdir(BOOK_UPLOAD_DIR):
        if not filename.lower().endswith(SUPPORTED_IMPORTS):
            continue
        epub_path = os.path.join(BOOK_UPLOAD_DIR, filename)
        slug = _slug_from_path(epub_path)
        target_dir = os.path.join(BOOKS_DIR, f"{slug}_data")
        if os.path.exists(target_dir):
            continue
        try:
            print(f"Ingesting {epub_path} -> {target_dir}")
            ingest_book(epub_path)
        except Exception as exc:
            print(f"Failed to ingest {filename}: {exc}")

@lru_cache(maxsize=10)
def load_book_cached(folder_name: str) -> Optional[Book]:
    """
    Loads the book from the pickle file.
    Cached so we don't re-read the disk on every click.
    """
    file_path = os.path.join(BOOKS_DIR, folder_name, "book.pkl")
    if not os.path.exists(file_path):
        return None

    try:
        with open(file_path, "rb") as f:
            book = pickle.load(f)
        return book
    except Exception as e:
        print(f"Error loading book {folder_name}: {e}")
        return None

@app.get("/", response_class=HTMLResponse)
async def library_view(request: Request):
    """Lists all available processed books."""
    books = []
    ingest_pending_files()

    # Scan directory for folders ending in '_data' that have a book.pkl
    if os.path.exists(BOOKS_DIR):
        for item in os.listdir(BOOKS_DIR):
            item_path = os.path.join(BOOKS_DIR, item)
            if item.endswith("_data") and os.path.isdir(item_path):
                # Try to load it to get the title
                book = load_book_cached(item)
                if book:
                    cover_image = select_cover_image(book)
                    books.append({
                        "id": item,
                        "title": book.metadata.title,
                        "author": ", ".join(book.metadata.authors),
                        "chapters": len(book.spine),
                        "cover_image": cover_image
                    })

    return templates.TemplateResponse("library.html", {"request": request, "books": books})

@app.get("/read/{book_id}", response_class=HTMLResponse)
async def redirect_to_first_chapter(book_id: str):
    """Helper to just go to chapter 0."""
    return await read_chapter(book_id=book_id, chapter_index=0)

@app.get("/read/{book_id}/{chapter_index}", response_class=HTMLResponse)
async def read_chapter(request: Request, book_id: str, chapter_index: int):
    """The main reader interface."""
    book = load_book_cached(book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    if chapter_index < 0 or chapter_index >= len(book.spine):
        raise HTTPException(status_code=404, detail="Chapter not found")

    current_chapter = book.spine[chapter_index]

    # Calculate Prev/Next links
    prev_idx = chapter_index - 1 if chapter_index > 0 else None
    next_idx = chapter_index + 1 if chapter_index < len(book.spine) - 1 else None

    return templates.TemplateResponse("reader.html", {
        "request": request,
        "book": book,
        "current_chapter": current_chapter,
        "chapter_index": chapter_index,
        "book_id": book_id,
        "prev_idx": prev_idx,
        "next_idx": next_idx
    })

@app.get("/read/{book_id}/images/{image_name}")
async def serve_image(book_id: str, image_name: str):
    """
    Serves images specifically for a book.
    The HTML contains <img src="images/pic.jpg">.
    The browser resolves this to /read/{book_id}/images/pic.jpg.
    """
    # Security check: ensure book_id is clean
    safe_book_id = os.path.basename(book_id)
    safe_image_name = os.path.basename(image_name)

    img_path = os.path.join(BOOKS_DIR, safe_book_id, "images", safe_image_name)

    if not os.path.exists(img_path):
        raise HTTPException(status_code=404, detail="Image not found")

    return FileResponse(img_path)


@app.post("/upload")
async def upload_book(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(SUPPORTED_IMPORTS):
        raise HTTPException(status_code=400, detail="Only EPUB or TXT files are supported")

    safe_name = os.path.basename(file.filename)
    dest_path = os.path.join(BOOK_UPLOAD_DIR, safe_name)

    with open(dest_path, "wb") as buffer:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            buffer.write(chunk)

    slug, _ = ingest_book(dest_path)
    return JSONResponse({"status": "ok", "book_id": f"{slug}_data"})


@app.delete("/books/{book_id}")
async def delete_book(book_id: str):
    safe_id = os.path.basename(book_id)
    target_dir = os.path.join(BOOKS_DIR, safe_id)
    if not os.path.isdir(target_dir):
        raise HTTPException(status_code=404, detail="Book not found")

    try:
        shutil.rmtree(target_dir)
        remove_source_file(safe_id)
        load_book_cached.cache_clear()
        return JSONResponse({"status": "deleted"})
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Unable to delete book: {exc}")


def remove_source_file(book_id: str):
    slug = book_id[:-5] if book_id.endswith("_data") else book_id
    try:
        for filename in os.listdir(BOOK_UPLOAD_DIR):
            base, ext = os.path.splitext(filename)
            if base == slug and ext.lower() in SUPPORTED_IMPORTS:
                os.remove(os.path.join(BOOK_UPLOAD_DIR, filename))
    except FileNotFoundError:
        pass
    except Exception as exc:
        print(f"Warning: failed to remove source file for {book_id}: {exc}")

if __name__ == "__main__":
    import uvicorn
    print("Starting server at http://127.0.0.1:8123")
    uvicorn.run(app, host="127.0.0.1", port=8123)
