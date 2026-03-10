#!/usr/bin/env python3
"""
SBMWD Case 2600093 — Legal File Organizer
==========================================
Customized for San Bernardino Municipal Water Department litigation.
Classifies files by filename keywords first, then falls back to
OCR/text extraction for content-based classification.

Dependencies:
    pip install pytesseract Pillow pdf2image pypdf

System requirements:
    sudo apt install tesseract-ocr poppler-utils

Usage:
    python3 sbmwd_organizer.py
    python3 sbmwd_organizer.py --source ./my_files --target ./SBMWD_Case_2600093
    python3 sbmwd_organizer.py --dry-run
"""

import os
import sys
import shutil
import re
import csv
import hashlib
import argparse
import logging
from pathlib import Path
from datetime import datetime

# ---------------------------------------------------------------------------
# Optional imports — degrade gracefully
# ---------------------------------------------------------------------------
try:
    import pytesseract
    from PIL import Image
    HAS_OCR = True
except ImportError:
    HAS_OCR = False

try:
    from pdf2image import convert_from_path
    HAS_PDF2IMAGE = True
except ImportError:
    HAS_PDF2IMAGE = False

try:
    import pypdf
    HAS_PYPDF = True
except ImportError:
    HAS_PYPDF = False

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("sbmwd_organizer")

# ---------------------------------------------------------------------------
# Configuration — Tailored for SBMWD Case 2600093
# ---------------------------------------------------------------------------
DEFAULT_SOURCE = "./my_messy_files"
DEFAULT_TARGET = "./SBMWD_Case_2600093"

SUPPORTED_EXTENSIONS = {
    ".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif",
    ".txt", ".doc", ".docx", ".rtf",
}

# Category keywords — order matters (first match wins on filename;
# highest-score match wins on content)
CATEGORIES = {
    "01_Pleadings": [
        "complaint", "petition", "claim form", "summons", "government claim",
        "proof of service", "declaration", "cross-complaint", "amended complaint",
    ],
    "02_Opposition_and_Defense_Filings": [
        "opposition", "defense", "answer", "demurrer", "sbmwd response",
        "city response", "rejection of claim", "rejection", "municipal water department",
        "motion to dismiss", "anti-slapp", "defendant's", "responding party",
    ],
    "03_Transcripts_and_Recordings": [
        "transcript", "recording", "audio", "video", "voicemail", "call log",
        "dispatch", "body cam", "bodycam", "deposition transcript",
    ],
    "04_Medical_and_Harm_Evidence": [
        "medical", "doctor", "hospital", "diagnosis", "treatment", "prescription",
        "therapy", "damages", "emotional distress", "injury", "health record",
    ],
    "05_Tenancy_and_Residency_Proof": [
        "lease", "rent", "utility bill", "water bill", "shutoff notice",
        "tenant", "residency", "occupancy", "property management",
        "service address", "account holder",
    ],
    "06_Statutes_and_Caselaw": [
        "statute", "civil code", "case law", "precedent", "water code",
        "municipal code", "ordinance", "health and safety code",
        "government code", "ccp", "code of civil procedure",
    ],
    "07_Exhibits_for_Hearing": [
        "exhibit", "hearing", "evidence submission", "appendix",
        "index of exhibits", "exhibit list", "proposed exhibit",
    ],
}

UNSORTED = "08_Unsorted_Review"


# ---------------------------------------------------------------------------
# Utility Functions
# ---------------------------------------------------------------------------

def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def sha256_file(file_path: str) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Date Extraction
# ---------------------------------------------------------------------------

def extract_date_from_text(text: str) -> str:
    """
    Extract the first recognizable date from a string.
    Returns '[YYYY-MM-DD]_' prefix or empty string.
    """
    name = text
    patterns = [
        # YYYY-MM-DD / YYYY_MM_DD / YYYY.MM.DD
        (r"(?P<y>20\d{2})[-_.](?P<m>\d{1,2})[-_.](?P<d>\d{1,2})", None),
        # MM-DD-YYYY / MM_DD_YYYY
        (r"(?P<m>\d{1,2})[-_.](?P<d>\d{1,2})[-_.](?P<y>20\d{2})", None),
        # MM-DD-YY (short year)
        (r"(?P<m>\d{1,2})[-_.](?P<d>\d{1,2})[-_.](?P<y>\d{2})", "short"),
    ]

    for pattern, kind in patterns:
        match = re.search(pattern, name)
        if match:
            try:
                y = int(match.group("y"))
                m = int(match.group("m"))
                d = int(match.group("d"))
                if kind == "short":
                    y += 2000 if y < 50 else 1900
                dt = datetime(y, m, d)
                return f"[{dt.strftime('%Y-%m-%d')}]_"
            except ValueError:
                pass
    return ""


# ---------------------------------------------------------------------------
# Text Extraction
# ---------------------------------------------------------------------------

def extract_pdf_text_native(file_path: str, max_pages: int = 3) -> str:
    """Extract text from a native (non-scanned) PDF using pypdf."""
    if not HAS_PYPDF:
        return ""
    try:
        reader = pypdf.PdfReader(file_path)
        texts = []
        for page in reader.pages[:max_pages]:
            texts.append(page.extract_text() or "")
        return normalize_whitespace(" ".join(texts))
    except Exception as e:
        log.debug(f"pypdf failed for {file_path}: {e}")
        return ""


def extract_text_with_ocr(file_path: str) -> str:
    """Extract text via OCR (Tesseract). Handles PDFs and images."""
    ext = Path(file_path).suffix.lower()
    try:
        if ext == ".pdf":
            if not HAS_PDF2IMAGE:
                log.debug("pdf2image not available, skipping OCR for PDF")
                return ""
            pages = convert_from_path(file_path, first_page=1, last_page=2)
            text_parts = [pytesseract.image_to_string(page) for page in pages]
            return normalize_whitespace(" ".join(text_parts))

        if ext in {".png", ".jpg", ".jpeg", ".tiff", ".tif"}:
            if not HAS_OCR:
                return ""
            img = Image.open(file_path)
            return normalize_whitespace(pytesseract.image_to_string(img))
    except Exception as e:
        log.warning(f"OCR failed for {os.path.basename(file_path)}: {e}")
    return ""


def extract_text(file_path: str) -> tuple[str, str]:
    """
    Best-effort text extraction. Returns (text, source_method).
    source_method is one of: 'PDF Native Text', 'OCR', 'Plain Text', 'Unsupported'
    """
    ext = Path(file_path).suffix.lower()

    if ext == ".pdf":
        native_text = extract_pdf_text_native(file_path)
        if native_text.strip():
            return native_text, "PDF Native Text"
        ocr_text = extract_text_with_ocr(file_path)
        if ocr_text.strip():
            return ocr_text, "OCR"
        return "", "No Text Extracted"

    if ext in {".png", ".jpg", ".jpeg", ".tiff", ".tif"}:
        ocr_text = extract_text_with_ocr(file_path)
        return ocr_text, "OCR" if ocr_text.strip() else "No Text Extracted"

    if ext in {".txt", ".md", ".csv", ".rtf"}:
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                return normalize_whitespace(f.read(50_000)), "Plain Text"
        except Exception:
            return "", "Read Error"

    return "", "Unsupported"


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def classify_by_keywords(text: str, source_label: str = "Text") -> tuple[str, str]:
    """
    Score text against all category keywords. Returns (category, reason).
    Uses highest-score match to handle ambiguous documents.
    """
    text_lower = normalize_whitespace(text) if text == text.lower() else text.lower()
    best_category = UNSORTED
    best_score = 0
    best_keyword = ""

    for category, keywords in CATEGORIES.items():
        for keyword in keywords:
            if keyword.lower() in text_lower:
                # Count occurrences for scoring
                score = text_lower.count(keyword.lower())
                if score > best_score:
                    best_score = score
                    best_category = category
                    best_keyword = keyword

    if best_score > 0:
        return best_category, f"{source_label} Match: '{best_keyword}' (score: {best_score})"
    return UNSORTED, "No Match — Manual Review Needed"


def classify_file(filename: str, file_path: str) -> tuple[str, str, str]:
    """
    Two-pass classification:
      1. Try filename keywords (fast)
      2. If unsorted, extract text content and try again (slower, uses OCR)
    Returns (category, classification_reason, text_source).
    """
    # Pass 1: Filename
    category, reason = classify_by_keywords(Path(filename).stem, source_label="Filename")
    if category != UNSORTED:
        return category, reason, "Filename Only"

    # Pass 2: Content extraction
    log.info(f"    Filename inconclusive — scanning content of {filename}...")
    content, text_source = extract_text(file_path)
    if content.strip():
        category, reason = classify_by_keywords(content, source_label="Content")
        return category, reason, text_source

    return UNSORTED, "No Match — Manual Review Needed", text_source


# ---------------------------------------------------------------------------
# File Collision Handler
# ---------------------------------------------------------------------------

def safe_target_path(target_dir: str, category: str, new_filename: str) -> str:
    target_path = os.path.join(target_dir, category, new_filename)
    counter = 1
    stem = Path(new_filename).stem
    suffix = Path(new_filename).suffix

    while os.path.exists(target_path):
        # Remove prior _v## to avoid stacking: file_v1_v2.pdf
        stem_clean = re.sub(r'_v\d+$', '', stem)
        target_path = os.path.join(
            target_dir, category, f"{stem_clean}_v{counter}{suffix}"
        )
        counter += 1
    return target_path


# ---------------------------------------------------------------------------
# Main Organizer
# ---------------------------------------------------------------------------

def organize_files(source_dir: str, target_dir: str, dry_run: bool = False):
    source = Path(source_dir).resolve()
    target = Path(target_dir).resolve()

    if not source.exists():
        log.error(f"Source directory not found: {source}")
        sys.exit(1)

    # Create category directories
    all_categories = list(CATEGORIES.keys()) + [UNSORTED]
    if not dry_run:
        for cat in all_categories:
            (target / cat).mkdir(parents=True, exist_ok=True)

    # Track hashes for duplicate detection
    seen_hashes: dict[str, str] = {}

    # Manifest data
    manifest_rows = []

    # Collect all files recursively
    all_files = sorted(source.rglob("*"))
    total = sum(1 for f in all_files if f.is_file() and not f.name.startswith("."))
    log.info(f"Found {total} files to process in {source}")

    processed = 0
    duplicates = 0
    skipped = 0

    for filepath in all_files:
        if not filepath.is_file() or filepath.name.startswith("."):
            continue

        processed += 1
        rel_path = filepath.relative_to(source)
        ext = filepath.suffix.lower()

        # Check supported extensions
        if ext not in SUPPORTED_EXTENSIONS:
            skipped += 1
            log.info(f"  [{processed}/{total}] SKIPPED (unsupported): {rel_path}")
            manifest_rows.append({
                "original_name": filepath.name,
                "new_name": "",
                "category": UNSORTED,
                "original_path": str(rel_path),
                "new_path": "",
                "sha256": "",
                "classified_by": "Unsupported File Type",
                "text_source": "N/A",
                "date_found": "",
            })
            continue

        # Duplicate detection via SHA-256
        fhash = sha256_file(str(filepath))
        if fhash in seen_hashes:
            duplicates += 1
            log.info(f"  [{processed}/{total}] DUPLICATE: {rel_path}  ==  {seen_hashes[fhash]}")
            manifest_rows.append({
                "original_name": filepath.name,
                "new_name": "",
                "category": "DUPLICATE",
                "original_path": str(rel_path),
                "new_path": f"Duplicate of: {seen_hashes[fhash]}",
                "sha256": fhash,
                "classified_by": "SHA-256 Match",
                "text_source": "N/A",
                "date_found": "",
            })
            continue
        seen_hashes[fhash] = str(rel_path)

        # Classify
        category, classified_by, text_source = classify_file(filepath.name, str(filepath))

        # Date prefix
        date_prefix = extract_date_from_text(filepath.name)
        if not date_prefix:
            # Try extracting date from content if filename had none
            content_text, _ = extract_text(str(filepath))
            date_prefix = extract_date_from_text(content_text[:2000])

        new_filename = f"{date_prefix}{filepath.name}"
        # Sanitize for filesystem
        new_filename = re.sub(r'[<>:"|?*]', '_', new_filename)

        dest_path = safe_target_path(str(target), category, new_filename)

        if dry_run:
            log.info(f"  [{processed}/{total}] {rel_path}  →  {category}/{os.path.basename(dest_path)}")
        else:
            shutil.copy2(str(filepath), dest_path)
            log.info(f"  [{processed}/{total}] {rel_path}  →  {category}/{os.path.basename(dest_path)}")

        manifest_rows.append({
            "original_name": filepath.name,
            "new_name": os.path.basename(dest_path),
            "category": category,
            "original_path": str(rel_path),
            "new_path": os.path.relpath(dest_path, str(target)) if not dry_run else f"{category}/{os.path.basename(dest_path)}",
            "sha256": fhash,
            "classified_by": classified_by,
            "text_source": text_source,
            "date_found": date_prefix.strip("[]_") if date_prefix else "",
        })

    # --- Write Manifests ---
    if not dry_run:
        # CSV manifest
        manifest_csv = target / "Table_of_Contents_2600093.csv"
        fieldnames = [
            "original_name", "new_name", "category", "original_path",
            "new_path", "sha256", "classified_by", "text_source", "date_found",
        ]
        with open(manifest_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            # Case header row
            f.write(f"# SBMWD Case 2600093 — Master File Index — Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            writer.writeheader()
            writer.writerows(manifest_rows)

        # JSON manifest
        manifest_json = target / "Table_of_Contents_2600093.json"
        import json
        with open(manifest_json, "w", encoding="utf-8") as f:
            json.dump({
                "case": "SBMWD Case 2600093",
                "generated": datetime.now().isoformat(),
                "total_files": processed,
                "duplicates": duplicates,
                "skipped": skipped,
                "files": manifest_rows,
            }, f, indent=2, ensure_ascii=False)

    # --- Summary ---
    cat_counts: dict[str, int] = {}
    for row in manifest_rows:
        c = row["category"]
        cat_counts[c] = cat_counts.get(c, 0) + 1

    log.info("")
    log.info("=" * 65)
    log.info(f"  SBMWD Case 2600093 — {'DRY RUN ' if dry_run else ''}ORGANIZATION COMPLETE")
    log.info(f"  Total files scanned   : {processed}")
    log.info(f"  Duplicates skipped    : {duplicates}")
    log.info(f"  Unsupported skipped   : {skipped}")
    log.info(f"  OCR available         : {'Yes' if HAS_OCR else 'No (pip install pytesseract Pillow)'}")
    log.info(f"  PDF native reader     : {'Yes' if HAS_PYPDF else 'No (pip install pypdf)'}")
    log.info(f"  PDF-to-image (OCR)    : {'Yes' if HAS_PDF2IMAGE else 'No (pip install pdf2image; apt install poppler-utils)'}")
    log.info("-" * 65)
    for cat in all_categories + (["DUPLICATE"] if duplicates else []):
        if cat in cat_counts:
            log.info(f"  {cat:45s} {cat_counts[cat]:>4} files")
    log.info("=" * 65)
    if not dry_run:
        log.info(f"  CSV Manifest  : {target / 'Table_of_Contents_2600093.csv'}")
        log.info(f"  JSON Manifest : {target / 'Table_of_Contents_2600093.json'}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="SBMWD Case 2600093 — Legal File Organizer with OCR",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 sbmwd_organizer.py
  python3 sbmwd_organizer.py --source ./my_files --target ./SBMWD_Case_2600093
  python3 sbmwd_organizer.py --dry-run

Folder structure:
  01_Pleadings/
  02_Opposition_and_Defense_Filings/
  03_Transcripts_and_Recordings/
  04_Medical_and_Harm_Evidence/
  05_Tenancy_and_Residency_Proof/
  06_Statutes_and_Caselaw/
  07_Exhibits_for_Hearing/
  08_Unsorted_Review/
  Table_of_Contents_2600093.csv
  Table_of_Contents_2600093.json
""",
    )
    parser.add_argument("--source", "-s", default=DEFAULT_SOURCE,
                        help=f"Source directory (default: {DEFAULT_SOURCE})")
    parser.add_argument("--target", "-t", default=DEFAULT_TARGET,
                        help=f"Target directory (default: {DEFAULT_TARGET})")
    parser.add_argument("--dry-run", "-n", action="store_true",
                        help="Preview classification without copying files")
    args = parser.parse_args()

    log.info("SBMWD Case 2600093 — Legal File Organizer")
    log.info(f"  Source : {Path(args.source).resolve()}")
    log.info(f"  Target : {Path(args.target).resolve()}")
    log.info(f"  Mode   : {'DRY RUN' if args.dry_run else 'LIVE'}")
    log.info(f"  OCR    : {'Yes' if HAS_OCR else 'No'}")
    log.info(f"  pypdf  : {'Yes' if HAS_PYPDF else 'No'}")
    log.info(f"  pdf2img: {'Yes' if HAS_PDF2IMAGE else 'No'}")
    log.info("")

    organize_files(args.source, args.target, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
