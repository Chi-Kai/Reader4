"""
Parses an EPUB file into a structured object that can be used to serve the book via a web interface.
"""

import os
import pickle
import shutil
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
from datetime import datetime
from urllib.parse import unquote
import html

from charset_normalizer import from_path

import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup, Comment

DEFAULT_LIBRARY_DIR = os.environ.get("READER_LIBRARY_DIR", "data")

# --- Data structures ---

@dataclass
class ChapterContent:
    """
    Represents a physical file in the EPUB (Spine Item).
    A single file might contain multiple logical chapters (TOC entries).
    """
    id: str           # Internal ID (e.g., 'item_1')
    href: str         # Filename (e.g., 'part01.html')
    title: str        # Best guess title from file
    content: str      # Cleaned HTML with rewritten image paths
    text: str         # Plain text for search/LLM context
    order: int        # Linear reading order


@dataclass
class TOCEntry:
    """Represents a logical entry in the navigation sidebar."""
    title: str
    href: str         # original href (e.g., 'part01.html#chapter1')
    file_href: str    # just the filename (e.g., 'part01.html')
    anchor: str       # just the anchor (e.g., 'chapter1'), empty if none
    children: List['TOCEntry'] = field(default_factory=list)


@dataclass
class BookMetadata:
    """Metadata"""
    title: str
    language: str
    authors: List[str] = field(default_factory=list)
    description: Optional[str] = None
    publisher: Optional[str] = None
    date: Optional[str] = None
    identifiers: List[str] = field(default_factory=list)
    subjects: List[str] = field(default_factory=list)


@dataclass
class Book:
    """The Master Object to be pickled."""
    metadata: BookMetadata
    spine: List[ChapterContent]  # The actual content (linear files)
    toc: List[TOCEntry]          # The navigation tree
    images: Dict[str, str]       # Map: original_path -> local_path

    # Meta info
    source_file: str
    processed_at: str
    version: str = "3.0"


# --- Utilities ---

def clean_html_content(soup: BeautifulSoup) -> BeautifulSoup:

    # Remove dangerous/useless tags
    for tag in soup(['script', 'style', 'iframe', 'video', 'nav', 'form', 'button']):
        tag.decompose()

    # Remove HTML comments
    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()

    # Remove input tags
    for tag in soup.find_all('input'):
        tag.decompose()

    return soup


def extract_plain_text(soup: BeautifulSoup) -> str:
    """Extract clean text for LLM/Search usage."""
    text = soup.get_text(separator=' ')
    # Collapse whitespace
    return ' '.join(text.split())


def parse_toc_recursive(toc_list, depth=0) -> List[TOCEntry]:
    """
    Recursively parses the TOC structure from ebooklib.
    """
    result = []

    for item in toc_list:
        # ebooklib TOC items are either `Link` objects or tuples (Section, [Children])
        if isinstance(item, tuple):
            section, children = item
            entry = TOCEntry(
                title=section.title,
                href=section.href,
                file_href=section.href.split('#')[0],
                anchor=section.href.split('#')[1] if '#' in section.href else "",
                children=parse_toc_recursive(children, depth + 1)
            )
            result.append(entry)
        elif isinstance(item, epub.Link):
            entry = TOCEntry(
                title=item.title,
                href=item.href,
                file_href=item.href.split('#')[0],
                anchor=item.href.split('#')[1] if '#' in item.href else ""
            )
            result.append(entry)
        # Note: ebooklib sometimes returns direct Section objects without children
        elif isinstance(item, epub.Section):
             entry = TOCEntry(
                title=item.title,
                href=item.href,
                file_href=item.href.split('#')[0],
                anchor=item.href.split('#')[1] if '#' in item.href else ""
            )
             result.append(entry)

    return result


def get_fallback_toc(book_obj) -> List[TOCEntry]:
    """
    If TOC is missing, build a flat one from the Spine.
    """
    toc = []
    for item in book_obj.get_items():
        if item.get_type() == ebooklib.ITEM_DOCUMENT:
            name = item.get_name()
            # Try to guess a title from the content or ID
            title = item.get_name().replace('.html', '').replace('.xhtml', '').replace('_', ' ').title()
            toc.append(TOCEntry(title=title, href=name, file_href=name, anchor=""))
    return toc


def extract_metadata_robust(book_obj) -> BookMetadata:
    """
    Extracts metadata handling both single and list values.
    """
    def get_list(key):
        data = book_obj.get_metadata('DC', key)
        return [x[0] for x in data] if data else []

    def get_one(key):
        data = book_obj.get_metadata('DC', key)
        return data[0][0] if data else None

    return BookMetadata(
        title=get_one('title') or "Untitled",
        language=get_one('language') or "en",
        authors=get_list('creator'),
        description=get_one('description'),
        publisher=get_one('publisher'),
        date=get_one('date'),
        identifiers=get_list('identifier'),
        subjects=get_list('subject')
    )


# --- Main Conversion Logic ---

def process_epub(epub_path: str, output_dir: str) -> Book:

    # 1. Load Book
    print(f"Loading {epub_path}...")
    book = epub.read_epub(epub_path)

    # 2. Extract Metadata
    metadata = extract_metadata_robust(book)

    # 3. Prepare Output Directories
    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)
    images_dir = os.path.join(output_dir, 'images')
    os.makedirs(images_dir, exist_ok=True)

    # 4. Extract Images & Build Map
    print("Extracting images...")
    image_map = {} # Key: internal_path, Value: local_relative_path

    for item in book.get_items():
        if item.get_type() == ebooklib.ITEM_IMAGE:
            # Normalize filename
            original_fname = os.path.basename(item.get_name())
            # Sanitize filename for OS
            safe_fname = "".join([c for c in original_fname if c.isalpha() or c.isdigit() or c in '._-']).strip()

            # Save to disk
            local_path = os.path.join(images_dir, safe_fname)
            with open(local_path, 'wb') as f:
                f.write(item.get_content())

            # Map keys: We try both the full internal path and just the basename
            # to be robust against messy HTML src attributes
            rel_path = f"images/{safe_fname}"
            image_map[item.get_name()] = rel_path
            image_map[original_fname] = rel_path

    # 5. Process TOC
    print("Parsing Table of Contents...")
    toc_structure = parse_toc_recursive(book.toc)
    if not toc_structure:
        print("Warning: Empty TOC, building fallback from Spine...")
        toc_structure = get_fallback_toc(book)

    # 6. Process Content (Spine-based to preserve HTML validity)
    print("Processing chapters...")
    spine_chapters = []

    # We iterate over the spine (linear reading order)
    for i, spine_item in enumerate(book.spine):
        item_id, linear = spine_item
        item = book.get_item_with_id(item_id)

        if not item:
            continue

        if item.get_type() == ebooklib.ITEM_DOCUMENT:
            # Raw content
            raw_content = item.get_content().decode('utf-8', errors='ignore')
            soup = BeautifulSoup(raw_content, 'html.parser')

            # A. Fix Images
            for img in soup.find_all('img'):
                src = img.get('src', '')
                if not src: continue

                # Decode URL (part01/image%201.jpg -> part01/image 1.jpg)
                src_decoded = unquote(src)
                filename = os.path.basename(src_decoded)

                # Try to find in map
                if src_decoded in image_map:
                    img['src'] = image_map[src_decoded]
                elif filename in image_map:
                    img['src'] = image_map[filename]

            # B. Clean HTML
            soup = clean_html_content(soup)

            # C. Extract Body Content only
            body = soup.find('body')
            if body:
                # Extract inner HTML of body
                final_html = "".join([str(x) for x in body.contents])
            else:
                final_html = str(soup)

            # D. Create Object
            chapter = ChapterContent(
                id=item_id,
                href=item.get_name(), # Important: This links TOC to Content
                title=f"Section {i+1}", # Fallback, real titles come from TOC
                content=final_html,
                text=extract_plain_text(soup),
                order=i
            )
            spine_chapters.append(chapter)

    # 7. Final Assembly
    final_book = Book(
        metadata=metadata,
        spine=spine_chapters,
        toc=toc_structure,
        images=image_map,
        source_file=os.path.basename(epub_path),
        processed_at=datetime.now().isoformat()
    )

    return final_book


def save_to_pickle(book: Book, output_dir: str):
    p_path = os.path.join(output_dir, 'book.pkl')
    with open(p_path, 'wb') as f:
        pickle.dump(book, f)
    print(f"Saved structured data to {p_path}")


def split_plain_text_sections(text: str, max_chars: int = 8000) -> List[str]:
    """
    Breaks a text file into pseudo chapters by grouping paragraphs together.
    """
    blocks = [block.strip() for block in text.replace('\r', '').split('\n\n') if block.strip()]
    if not blocks:
        return [text.strip()]

    sections = []
    current: List[str] = []
    length = 0

    for block in blocks:
        block_len = len(block)
        if current and length + block_len > max_chars:
            sections.append('\n\n'.join(current))
            current = [block]
            length = block_len
        else:
            current.append(block)
            length += block_len

    if current:
        sections.append('\n\n'.join(current))

    return sections


def plain_text_to_html(section: str) -> str:
    paragraphs = [html.escape(p.strip()) for p in section.split('\n') if p.strip()]
    if not paragraphs:
        return "<p></p>"
    return "".join(f"<p>{p}</p>" for p in paragraphs)


def process_text_file(txt_path: str, output_dir: str) -> Book:
    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)
    os.makedirs(os.path.join(output_dir, 'images'), exist_ok=True)

    raw_text = load_text_file(txt_path)

    sections = split_plain_text_sections(raw_text)
    spine_chapters: List[ChapterContent] = []

    for idx, section in enumerate(sections):
        chapter_html = plain_text_to_html(section)
        chapter = ChapterContent(
            id=f"section-{idx}",
            href=f"section-{idx}",
            title=f"Section {idx + 1}",
            content=chapter_html,
            text=' '.join(section.split()),
            order=idx
        )
        spine_chapters.append(chapter)

    toc_entries = [
        TOCEntry(
            title=chapter.title,
            href=chapter.href,
            file_href=chapter.href,
            anchor=""
        ) for chapter in spine_chapters
    ]

    title = os.path.splitext(os.path.basename(txt_path))[0] or "Untitled Text"
    metadata = BookMetadata(
        title=title,
        language="en",
        authors=[],
        description="Imported plain text file"
    )

    book = Book(
        metadata=metadata,
        spine=spine_chapters,
        toc=toc_entries,
        images={},
        source_file=os.path.basename(txt_path),
        processed_at=datetime.now().isoformat()
    )

    return book


def load_text_file(path: str) -> str:
    """Detect encoding with charset_normalizer and fallback to utf-8."""
    try:
        matches = from_path(path)
        best = matches.best()
        if best:
            return str(best)
    except Exception as exc:
        print(f"Warning: charset detection failed for {path}: {exc}")

    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
        return f.read()


# --- CLI ---

if __name__ == "__main__":

    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Process an EPUB or TXT file into the local library.")
    parser.add_argument("input_file", help="Path to the EPUB/TXT file to ingest.")
    parser.add_argument(
        "-l",
        "--library",
        default=DEFAULT_LIBRARY_DIR,
        help=f"Directory to store processed books (default: {DEFAULT_LIBRARY_DIR})",
    )
    args = parser.parse_args()

    input_file = args.input_file
    if not os.path.exists(input_file):
        parser.error(f"File not found: {input_file}")

    library_dir = args.library
    os.makedirs(library_dir, exist_ok=True)

    book_slug = os.path.splitext(os.path.basename(input_file))[0]
    out_dir = os.path.join(library_dir, f"{book_slug}_data")

    ext = os.path.splitext(input_file)[1].lower()
    if ext == ".epub":
        book_obj = process_epub(input_file, out_dir)
    elif ext == ".txt":
        book_obj = process_text_file(input_file, out_dir)
    else:
        parser.error("Unsupported file type. Provide an .epub or .txt file.")

    save_to_pickle(book_obj, out_dir)
    print("\n--- Summary ---")
    print(f"Title: {book_obj.metadata.title}")
    print(f"Authors: {', '.join(book_obj.metadata.authors)}")
    print(f"Physical Files (Spine): {len(book_obj.spine)}")
    print(f"TOC Root Items: {len(book_obj.toc)}")
    print(f"Images extracted: {len(book_obj.images)}")
