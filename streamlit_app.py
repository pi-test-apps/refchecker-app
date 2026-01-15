from io import BytesIO
import json
from pathlib import Path
import streamlit as st
from docx import Document
from app.extractors import extract_apa_citations, extract_reference_entries
from app.matcher import build_report
import sys
import os
import pypdf
import re
import datetime
import time
import arxiv

# Add the src directory to Python path so refchecker package can be found
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

# from refchecker.core.refchecker import main
from refchecker.core.refchecker import ArxivReferenceChecker

from refchecker.utils.text_utils import (clean_author_name, clean_title, clean_title_basic,
                       extract_arxiv_id_from_url, normalize_text as common_normalize_text,
                       detect_latex_bibliography_format, extract_latex_references, 
                       detect_standard_acm_natbib_format, strip_latex_commands, 
                       format_corrected_reference, is_name_match, enhanced_name_match,
                       calculate_title_similarity, normalize_arxiv_url, deduplicate_urls,
                       compare_authors)
                       
from refchecker.checkers.enhanced_hybrid_checker import EnhancedHybridReferenceChecker

debug_mode = False

ALLOWED_SUFFIXES = {".tex", ".pdf", ".txt"}

def add_error_to_dataset(source_paper, reference, errors, reference_url=None, verified_data=None):
    """
    Add an error entry to the consolidated dataset
    
    Args:
        source_paper: The source paper object
        reference: The reference object
        errors: List of error dictionaries
        reference_url: URL of the verified paper (from verification service)
        verified_data: The verified data from the verification service (for corrected formatting)
    """
    if not errors:
        return
        
    # Consolidate all errors for this reference into a single entry
    if len(errors) > 1:
        # Multiple errors - consolidate them
        error_types = []
        error_details = []
        consolidated_entry = None
        
        for error in errors:
            error_type = error.get('error_type') or error.get('warning_type', 'unknown')
            error_detail = error.get('error_details') or error.get('warning_details', '')
            error_types.append(error_type)
            error_details.append(error_detail)
            
            # Use the first error as the base for consolidated entry
            if consolidated_entry is None:
                consolidated_entry = {
                    # Source paper metadata
                    # 'source_paper_id': source_paper.get_short_id(),
                    # 'source_title': source_paper.title,
                    # 'source_authors': ', '.join([author.name for author in source_paper.authors]),
                    # 'source_year': source_paper.published.year,
                    # 'source_url': f"https://arxiv.org/abs/{source_paper.get_short_id()}",
                    # 'source_paper_id': source_paper.get_short_id(),
                    'source_title': source_paper,
                    'source_authors': None,
                    'source_year': None,
                    'source_url': None,
                    
                    # Reference metadata as cited
                    'ref_paper_id': extract_arxiv_id_from_url(reference['url']),
                    'ref_title': reference.get('title', ''),
                    'ref_authors_cited': ', '.join(reference['authors']),
                    'ref_year_cited': reference['year'],
                    'ref_url_cited': reference['url'],
                    'ref_raw_text': reference.get('raw_text', ''),
                    
                    # Store original reference for formatting corrections
                    'original_reference': reference
                }
            
            # Collect correct information from all errors
            if error.get('ref_authors_correct'):
                consolidated_entry['ref_authors_correct'] = error['ref_authors_correct']
            if error.get('ref_year_correct'):
                consolidated_entry['ref_year_correct'] = error['ref_year_correct']
            if error.get('ref_title_correct'):
                consolidated_entry['ref_title_correct'] = error['ref_title_correct']
            if error.get('ref_url_correct'):
                consolidated_entry['ref_url_correct'] = error['ref_url_correct']
            if error.get('ref_venue_correct'):
                consolidated_entry['ref_venue_correct'] = error['ref_venue_correct']
        
        # Set consolidated error information
        consolidated_entry['error_type'] = 'multiple'
        consolidated_entry['error_details'] = '\n'.join([f"- {detail}" for detail in error_details])
        
        # Add verified URL if available
        if reference_url:
            consolidated_entry['ref_verified_url'] = reference_url
        
        # Generate corrected reference using all available corrections
        corrected_data = _extract_corrected_data_from_error(consolidated_entry, verified_data)
        
        # Generate all three formats for user convenience
        from refchecker.utils.text_utils import format_corrected_plaintext, format_corrected_bibtex, format_corrected_bibitem
        plaintext_format = format_corrected_plaintext(reference, corrected_data, consolidated_entry)
        bibtex_format = format_corrected_bibtex(reference, corrected_data, consolidated_entry)
        bibitem_format = format_corrected_bibitem(reference, corrected_data, consolidated_entry)
        
        if plaintext_format:
            consolidated_entry['ref_corrected_plaintext'] = plaintext_format
        if bibtex_format:
            consolidated_entry['ref_corrected_bibtex'] = bibtex_format
        if bibitem_format:
            consolidated_entry['ref_corrected_bibitem'] = bibitem_format
        
        # Store the consolidated entry (write to file at end of run)
        # errors.append(consolidated_entry)
        
    else:
        # Single error - handle as before
        error = errors[0]
        error_type = error.get('error_type') or error.get('warning_type') or error.get('info_type', 'unknown')
        error_details = error.get('error_details') or error.get('warning_details') or error.get('info_details', '')
        
        error_entry = {
            # Source paper metadata
            # 'source_paper_id': source_paper.get_short_id(),
            # 'source_title': source_paper.title,
            # 'source_authors': ', '.join([author.name for author in source_paper.authors]),
            # 'source_year': source_paper.published.year,
            # 'source_url': f"https://arxiv.org/abs/{source_paper.get_short_id()}",
            'source_title': source_paper,
            'source_authors': None,
            'source_year': None,
            'source_url': None,
            
            # Reference metadata as cited
            'ref_paper_id': extract_arxiv_id_from_url(reference['url']),
            'ref_title': reference.get('title', ''),
            'ref_authors_cited': ', '.join(reference['authors']),
            'ref_year_cited': reference['year'],
            'ref_url_cited': reference['url'],
            'ref_raw_text': reference.get('raw_text', ''),
            
            # Error information
            'error_type': error_type,
            'error_details': error_details,
            
            # Store original reference for formatting corrections
            'original_reference': reference
        }
        
        # Add correct information based on error type
        if error_type == 'author':
            error_entry['ref_authors_correct'] = error.get('ref_authors_correct', '')
        elif error_type == 'year':
            error_entry['ref_year_correct'] = error.get('ref_year_correct', '')
        elif error_type == 'title':
            error_entry['ref_title_correct'] = error.get('ref_title_correct', '')
        elif error_type == 'url':
            error_entry['ref_url_correct'] = error.get('ref_url_correct', '')
        elif error_type == 'arxiv_id':
            error_entry['ref_url_correct'] = error.get('ref_url_correct', '')
        elif error_type == 'venue':
            error_entry['ref_venue_correct'] = error.get('ref_venue_correct', '')
        
        # Add verified URL if available (from verification service)
        if reference_url:
            error_entry['ref_verified_url'] = reference_url
        
        # Add standard format using the correct information (only for non-unverified errors)
        if error_type != 'unverified':
            error_entry['ref_standard_format'] = format_standard_reference(error)
            
            # Generate corrected reference in all formats for user convenience
            corrected_data = _extract_corrected_data_from_error(error, verified_data)
            
            # Generate all three formats
            from refchecker.utils.text_utils import format_corrected_plaintext, format_corrected_bibtex, format_corrected_bibitem
            plaintext_format = format_corrected_plaintext(reference, corrected_data, error_entry)
            bibtex_format = format_corrected_bibtex(reference, corrected_data, error_entry)
            bibitem_format = format_corrected_bibitem(reference, corrected_data, error_entry)
            
            if plaintext_format:
                error_entry['ref_corrected_plaintext'] = plaintext_format
            if bibtex_format:
                error_entry['ref_corrected_bibtex'] = bibtex_format
            if bibitem_format:
                error_entry['ref_corrected_bibitem'] = bibitem_format
        else:
            error_entry['ref_standard_format'] = None
        
        # Store error in memory (write to file at end of run)
        # errors.append(error_entry)

def extract_bibliography_app(text, suffix='.pdf'):
    """
    Extract bibliography from LaTeX, or text file)
    """
    debug_mode = False
    paper_id = False
   
    # Check if this is a text file containing references
    if suffix == '.txt' :       
        # Parse references directly from the text
        bibliography_text = text
    
    # Check if this is a LaTeX file
    elif suffix == '.tex' :
        bibliography_text == text
        # Try programmatic LaTeX extraction first
        latex_format = detect_latex_bibliography_format(text)
        if latex_format['is_latex']:
            latex_references = extract_latex_references(text)
            if latex_references:
                bibliography_text = latex_references
    
    else :    
        # PDF -> Find bibliography section
        bibliography_text = find_bibliography_section(text)
        
    if not bibliography_text:
        return []
                
    # Parse references
    references = parse_references(bibliography_text)
    
    return references

def extract_authors_title_from_academic_format(ref_text):
    """
    Improved function to extract authors and title from academic paper reference format.
    Handles various formats including cases with periods in author names.
    
    Args:
        ref_text: The reference text to parse
        
    Returns:
        Tuple of (authors list, title) or None if extraction failed
    """
    # First, normalize the text - replace newlines with spaces
    cleaned_ref = re.sub(r'\s+', ' ', ref_text).strip()
    
    # Fix common hyphenation issues from line breaks BEFORE pattern matching
    # This handles cases like "Fredrik- son" -> "Fredrikson"
    cleaned_ref = re.sub(r'([a-z])- ([a-z])', r'\1\2', cleaned_ref, flags=re.IGNORECASE)
    
    # Remove any leading reference numbers like [1]
    cleaned_ref = re.sub(r'^\s*\[\d+\]\s*', '', cleaned_ref)
    
    # Handle specific problematic cases from the bibliography
    # Case 1: Legal cases like "[1]1976. Tarasoff v. Regents of University of California - 17 Cal.3d 425"
    legal_case_match = re.search(r'^(\d{4})\.\s+([^.]+?)\s+https?://', cleaned_ref)
    if legal_case_match:
        year = legal_case_match.group(1)
        title = clean_title_basic(legal_case_match.group(2))
        return [year], title
        
    # Case 2: References with year at start like "2022. Title AuthorName1, AuthorName2, AuthorName3 2022"
    # Look for pattern: YEAR. Title followed by authors ending with the same year
    year_title_authors_match = re.search(r'^(\d{4})\.\s+(.+?)\s+([A-Z][a-z]+.*?)\s+\1\s*$', cleaned_ref)
    if year_title_authors_match:
        year = year_title_authors_match.group(1)
        potential_title = year_title_authors_match.group(2).strip()
        potential_authors = year_title_authors_match.group(3).strip()
        
        # Check if potential_authors looks like a list of authors (contains comma-separated names)
        # and potential_title looks like a title (longer, has multiple words)
        if ',' in potential_authors and len(potential_title.split()) > 3:
            # Extract authors from the authors text
            authors = extract_authors_list(potential_authors)
            return authors, clean_title_basic(potential_title)
    
    # Case 2b: References with year at start like "2021. Title Author1, Author2, Author3"
    # More flexible pattern to handle various formats
    year_start_match = re.search(r'^(\d{4})\.\s+(.+?)(?:\s+([A-Z][a-z]+(?:\s+[A-Z]\.?\s*)*[A-Z][a-z]+(?:,\s*[A-Z][a-z]+(?:\s+[A-Z]\.?\s*)*[A-Z][a-z]+)*(?:\s+and\s+[A-Z][a-z]+(?:\s+[A-Z]\.?\s*)*[A-Z][a-z]+)?)\s*(?:\d{4})?\s*$)', cleaned_ref)
    if year_start_match:
        year = year_start_match.group(1)
        title = year_start_match.group(2).strip()
        authors_text = year_start_match.group(3) if year_start_match.group(3) else None
        
        if authors_text:
            # Extract authors from the authors text
            authors = extract_authors_list(authors_text)
            return authors, clean_title_basic(title)
        else:
            # If we can't extract authors, fall back to using year as author
            return [year], clean_title_basic(title)
    
    # Case 2c: Simple year at start like "1976. Title"
    simple_year_start_match = re.search(r'^(\d{4})\.\s+([^.]+?)(?:\.\s+https?://|\.\s*$)', cleaned_ref)
    if simple_year_start_match:
        year = simple_year_start_match.group(1)
        title = clean_title_basic(simple_year_start_match.group(2))
        return [year], title
    
    # Case 3: Legal cases with reference number and year like "[1]1976. Title"
    legal_case_with_ref_match = re.search(r'^\[\d+\](\d{4})\.\s+([^.]+?)(?:\.\s+https?://|\.\s*$)', cleaned_ref)
    if legal_case_with_ref_match:
        year = legal_case_with_ref_match.group(1)
        title = clean_title_basic(legal_case_with_ref_match.group(2))
        return [year], title
    
    # Normalize spacing around periods
    cleaned_ref = re.sub(r'([A-Z])\s+\.\s+', r'\1. ', cleaned_ref)
    cleaned_ref = re.sub(r'([A-Z])\s+\.([A-Za-z])', r'\1. \2', cleaned_ref)

    # Check if this is a URL-based reference (common in some papers)
    if re.search(r'https?://', cleaned_ref):
        # This is likely a URL reference, not a standard academic citation
        # Handle multi-line URLs by removing newlines and reconstructing
        url_pattern = r'(https?://[^\s]*(?:\n[^\s\[\]]*)*)'
        url_match = re.search(url_pattern, cleaned_ref)
        if url_match:
            # Extract and reconstruct the URL
            raw_url = url_match.group(1).strip()
            # Remove newlines and spaces within the URL
            url = re.sub(r'\s+', '', raw_url)
            
            # For URL references, extract any remaining text as title
            remaining_text = cleaned_ref.replace(raw_url, '').strip()
            # Remove trailing periods and clean up
            remaining_text = re.sub(r'^\s*[.\s]*|[.\s]*$', '', remaining_text)
            
            # Return a special marker to indicate this is a URL reference
            return [{"is_url_reference": True}], remaining_text if remaining_text else url
    
    # Also check if the reference contains only a URL (possibly with some ID)
    if re.search(r'^https?://', cleaned_ref) and not re.search(r'[A-Z][a-z]+ [A-Z][a-z]+', cleaned_ref):
        # This is likely just a URL with maybe some ID
        url_pattern = r'(https?://[^\s]*(?:\n[^\s\[\]]*)*)'
        url_match = re.search(url_pattern, cleaned_ref)
        if url_match:
            raw_url = url_match.group(1).strip()
            url = re.sub(r'\s+', '', raw_url)
            remaining_text = cleaned_ref.replace(raw_url, '').strip()
            # Remove trailing periods and clean up
            remaining_text = re.sub(r'^\s*[.\s]*|[.\s]*$', '', remaining_text)
            
            return [{"is_url_reference": True}], remaining_text if remaining_text else url
        
    # Special case for authors with last names that end right before title
    # Handle patterns like "... and Quoc V. Le. Multi-task ..." 
    # Be more careful to avoid splitting names like "Le" from "Quoc V. Le"
    
    # Handle references with year between authors and title
    # Pattern: "Authors. YEAR. Title: Subtitle. URL" - for cases like the Hashimoto reference
    year_between_authors_title_match = re.search(r'(.*?)\.\s+(19|20)\d{2}\.\s+([^:]+:[^.]*?)\.\s+(https?://[^\s]+)', cleaned_ref)
    if year_between_authors_title_match:
        authors_text = year_between_authors_title_match.group(1).strip()
        title = year_between_authors_title_match.group(3).strip()
        
        # Extract authors
        authors = extract_authors_list(authors_text)
        
        # Clean the title
        title = clean_title(title)
        
        if authors and title:
            return authors, title
    
    # First try: Look for arXiv format specifically - most reliable
    arxiv_specific_match = re.search(r'(.*?)\.\s+([A-Z][^.]{1,100}?[.!?]?)\s+arXiv\s+preprint\s+arXiv:', cleaned_ref)
    if arxiv_specific_match:
        authors_text = arxiv_specific_match.group(1).strip()
        title = arxiv_specific_match.group(2).strip()
        
        # Extract authors
        authors = extract_authors_list(authors_text)
        
        # Clean the title
        title = clean_title(title)
        
        if authors and title:
            return authors, title
    
    # Try to find the pattern for references with years at the end
    # Pattern: "Authors. Title, YEAR." - but NOT "Authors. Title. Journal, Volume:Pages, YEAR." 
    # and NOT "Authors. Title. In Conference, pages X-Y, YEAR."
    # Make sure we don't match references that have journal volume info or conference proceedings
    year_at_end_match = re.search(r'(.*?)\.\s+([^.]+?),\s+(19|20)\d{2}\.?\s*$', cleaned_ref)
    if year_at_end_match:
        # Check if the "title" contains patterns that indicate this is actually venue/journal info
        potential_title = year_at_end_match.group(2).strip()
        authors_and_title = year_at_end_match.group(1).strip()
        
        # Skip if the "title" looks like journal volume info: "Journal Name , Volume:Pages"
        if re.search(r'.+\s*,\s*\d+(\(\d+\))?:\d+', potential_title):
            pass  # Skip this pattern
        # Skip if the "title" looks like conference proceedings: "In Conference", "InConference", or "In Conference, pages X-Y"
        elif re.match(r'^In[A-Z]', potential_title) or potential_title.startswith('In '):
            pass  # Skip this pattern - it's clearly a venue/conference name
        # Skip if the authors+title part contains obvious venue indicators that suggest wrong parsing
        elif re.search(r'\.\s+(In\s+.*|Proceedings\s+of|Conference\s+on)\s*$', authors_and_title):
            pass  # Skip this pattern
        else:
            # This looks like a legitimate "Authors. Title, Year." pattern
            authors_text = authors_and_title
            title = potential_title
            
            # Extract authors
            authors = extract_authors_list(authors_text)
            
            # Clean the title
            title = clean_title(title)
            
            if authors and title:
                return authors, title
    
    # Try pattern for references where title ends with period and year is at end
    # Pattern: "Authors. Title. YEAR." 
    year_at_end_with_period_match = re.search(r'(.*?)\.\s+([^.]+?)\.\s+(19|20)\d{2}\.?\s*$', cleaned_ref)
    if year_at_end_with_period_match:
        authors_text = year_at_end_with_period_match.group(1).strip()
        title = year_at_end_with_period_match.group(2).strip()
        
        # Extract authors
        authors = extract_authors_list(authors_text)
        
        # Clean the title
        title = clean_title(title)
        
        if authors and title:
            return authors, title
    
    # Second try: Look for patterns with common academic reference formats
    # Pattern 1: Authors ending with initials and common last names before title
    author_name_patterns = [
        # Pattern for "... and FirstName LastInitial. LastName. Title."
        r'(.*\s+and\s+[A-Z][a-z]+\s+[A-Z]\.\s+[A-Z][a-z]{1,10})\.\s+(.*?)(?:\.\s+(?:In|CoRR|arXiv|Journal|Proceedings))',
        # Pattern for "... and FirstName LastName. Title."
        r'(.*\s+and\s+[A-Z][a-z]+\s+[A-Z][a-z]+)\.\s+(.*?)(?:\.\s+(?:In|CoRR|arXiv|Journal|Proceedings))',
    ]
    
    for pattern in author_name_patterns:
        author_name_at_title_match = re.search(pattern, cleaned_ref)
        if author_name_at_title_match:
            authors_text = author_name_at_title_match.group(1).strip()
            title = author_name_at_title_match.group(2).strip()
            
            # Extract authors
            authors = extract_authors_list(authors_text)
            
            # Clean the title
            title = clean_title(title)
            
            if authors and title:
                return authors, title
    
    # Special cases: check for common patterns where the title is incorrectly extracted
    # Check for arXiv preprint format that might confuse the parser
    arxiv_preprint_match = re.search(r'(.*?)\.\s+(.*?[.!?]?)\s+arXiv\s+preprint\s+arXiv:', cleaned_ref)
    if arxiv_preprint_match:
        authors_text = arxiv_preprint_match.group(1).strip()
        title = arxiv_preprint_match.group(2).strip()
        
        # Extract authors
        authors = extract_authors_list(authors_text)
        
        # Clean the title
        title = clean_title(title)
        
        if authors and title:
            return authors, title
    
    # Handle conference proceedings format with improved pattern matching
    # Handle both "In Conference" and cases where "In" is attached to conference name like "InInternational"
    # Be more careful about author name parsing - look for full name patterns
    conference_match = re.search(r'(.*?(?:\s+[A-Z][a-z]*\.?\s*)*)\.\s+([^.]+?)\.\s+In(?:\s+|(?=[A-Z]))(.*?)(?:,|\s+\(|\s+\d{4})', cleaned_ref)
    if conference_match:
        authors_text = conference_match.group(1).strip()
        title = conference_match.group(2).strip()
        
        # Additional check: if the title starts with what looks like a last name, 
        # it's probably part of the author list that got misplaced
        if re.match(r'^[A-Z][a-z]+\.?\s+', title):
            # Try a different approach - look for common author ending patterns
            author_ending_patterns = [
                r'(.*?\s+and\s+[A-Z][a-z]+\s+[A-Z]\.?\s+[A-Z][a-z]+)\.\s+([^.]+?)\.\s+In(?:\s+|(?=[A-Z]))',
                r'(.*?\s+[A-Z][a-z]+\s+[A-Z]\.?\s+[A-Z][a-z]+)\.\s+([^.]+?)\.\s+In(?:\s+|(?=[A-Z]))',
            ]
            
            for pattern in author_ending_patterns:
                alt_match = re.search(pattern, cleaned_ref)
                if alt_match:
                    authors_text = alt_match.group(1).strip()
                    title = alt_match.group(2).strip()
                    break
        
        # Extract authors
        authors = extract_authors_list(authors_text)
        
        # Clean the title
        title = clean_title(title)
        
        if authors and title:
            return authors, title

    # Handle specific problematic cases from the bibliography
    # Case 3: Alexander Street Press references with incomplete titles
    alexander_street_match = re.search(r'Alexander Street Press \(Ed\.\)\.\s+(\d{4})\.\s+([^.]+?)(?:\.\s+Alexander Street Press|\.\s*$)', cleaned_ref)
    if alexander_street_match:
        year = alexander_street_match.group(1)
        title = clean_title_basic(alexander_street_match.group(2))
        return ["Alexander Street Press (Ed.)"], title
        
    # Case 4: References with incomplete author names like "Alan S." and "Tara F."
    incomplete_author_match = re.search(r'([A-Z][a-z]+ [A-Z]\.)\s+(\d{4})\.\s+([^.]+?)(?:\.\s+[A-Z][a-z]+|\.\s*$)', cleaned_ref)
    if incomplete_author_match:
        author = incomplete_author_match.group(1).strip()
        year = incomplete_author_match.group(2)
        title = clean_title_basic(incomplete_author_match.group(3))
        return [author], title
        
    # Case 5: References with complete author lists but incomplete titles
    complete_author_incomplete_title_match = re.search(r'([^.]+?)\.\s+(\d{4})\.\s+([^.]+?)(?:\.\s+[A-Z][a-z]+|\.\s*$)', cleaned_ref)
    if complete_author_incomplete_title_match:
        authors_text = complete_author_incomplete_title_match.group(1).strip()
        year = complete_author_incomplete_title_match.group(2)
        title = clean_title_basic(complete_author_incomplete_title_match.group(3))
        authors = extract_authors_list(authors_text)
        if authors and title:
            return authors, title

    # Handle CoRR format specifically - very common in CS papers
    # Pattern: "Authors. Title. CoRR abs/ID, YEAR." - handle titles with question marks
    corr_match = re.search(r'(.*?)\.\s+([^?]+\?)\s*CoRR\s+abs/([^,\s]+)\s*,?\s+(19|20)\d{2}', cleaned_ref)
    if not corr_match:
        # Fallback pattern for titles without question marks
        corr_match = re.search(r'(.*?)\.\s+([^.]+?)\.\s+CoRR\s+abs/([^,\s]+)\s*,?\s+(19|20)\d{2}', cleaned_ref)
    
    if corr_match:
        authors_text = corr_match.group(1).strip()
        title = corr_match.group(2).strip()
        
        # Extract authors
        authors = extract_authors_list(authors_text)
        
        # Clean the title
        title = clean_title(title)

        if authors and title:
            return authors, title
    
    # Handle references with titles that start with colons and URLs at the end
    # Pattern: "Authors. Title: Subtitle. URL" - specifically for cases like "Stanford Alpaca: An Instruction-following LLaMA model"
    colon_title_url_match = re.search(r'(.*?)\.\s+([^:]+:[^.]*?)\.\s+(https?://[^\s]+)', cleaned_ref)
    if colon_title_url_match:
        authors_text = colon_title_url_match.group(1).strip()
        title = colon_title_url_match.group(2).strip()
        
        # Extract authors
        authors = extract_authors_list(authors_text)
        
        # Clean the title
        title = clean_title(title)
        
        if authors and title:
            return authors, title
    
    # Handle journal format with volume:pages - Pattern: "Authors. Title. Journal, Volume:Pages, Year"
    journal_volume_match = re.search(r'(.*?)\.\s+([^.]+?)\.\s+([^,]+)\s*,\s*\d+(\(\d+\))?:\d+[^,]*,\s+(19|20)\d{2}', cleaned_ref)
    if journal_volume_match:
        authors_text = journal_volume_match.group(1).strip()
        title = journal_volume_match.group(2).strip()
        
        # Extract authors
        authors = extract_authors_list(authors_text)
        
        # Clean the title
        title = clean_title(title)
        
        if authors and title:
            return authors, title
    
    # Handle journal format with venue information
    # Pattern: "Authors. Title. Journal/Venue info, Year."
    journal_match = re.search(r'(.*?)\.\s+([^.]+?)\.\s+([^,]+),\s+(19|20)\d{2}', cleaned_ref)
    if journal_match:
        authors_text = journal_match.group(1).strip()
        title = journal_match.group(2).strip()
        venue = journal_match.group(3).strip()
        
        # Check if the venue contains volume/page info - this is a good sign that we have the right split
        # Pattern like "Journal Name , Volume:Pages" or "Journal Name, Volume(Issue):Pages"
        if re.search(r'.+\s*,\s*\d+(\(\d+\))?:\d+', venue):
            # This looks like "Journal Name , Volume:Pages" - this is correct
            # Extract authors
            authors = extract_authors_list(authors_text)
            
            # Clean the title
            title = clean_title(title)
            
            if authors and title:
                return authors, title
        
        # Check if what we think is the title is actually venue information
        # Common venue patterns that shouldn't be titles: "CoRR abs/...", but not things like "Nature Machine Intelligence"
        venue_indicators_in_title = ['CoRR abs/', 'arXiv:', 'IEEE Transactions', 'ACM Transactions']
        if any(indicator in title for indicator in venue_indicators_in_title):
            # The "title" is likely venue info, this pattern doesn't apply
            return None
        
        # For normal journal references, the extraction should be correct
        # Extract authors
        authors = extract_authors_list(authors_text)
        
        # Clean the title
        title = clean_title(title)
        
        if authors and title:
            return authors, title
    
    # Handle journal format
    journal_match = re.search(r'(.*?)\.\s+(.*?)\.\s+(?:Journal|Proceedings|IEEE|ACM)', cleaned_ref)
    if journal_match:
        authors_text = journal_match.group(1).strip()
        title = journal_match.group(2).strip()
        
        # Extract authors
        authors = extract_authors_list(authors_text)
        
        # Clean the title
        title = clean_title(title)
        
        if authors and title:
            return authors, title
    
    # Pattern to find title after authors in standard academic format
    # Authors. Title. Venue, Year.
    # Improved to handle author names with initials like "J. Zico Kolter"
    # Look for patterns where authors end and title begins
    
    # Strategy: Look for a period that's likely to separate authors from title
    # This should be after a complete author name, not after an initial
    author_title_patterns = [
        # Pattern 1: Look for author lists ending with "and FirstName LastName." followed by title
        r'(.*\s+and\s+[A-Z][a-z]+\s+[A-Z][a-z]+)\.\s+([A-Z][^.]+?)\.\s+',
        # Pattern 2: Look for author lists ending with "FirstName LastName." followed by title  
        r'(.*[A-Z][a-z]+\s+[A-Z][a-z]+)\.\s+([A-Z][^.]+?)\.\s+',
        # Pattern 3: Look for author lists with initials ending with "Initial LastName." followed by title
        r'(.*[A-Z]\.\s+[A-Z][a-z]+)\.\s+([A-Z][^.]+?)\.\s+',
    ]
    
    authors_text = None
    title = None
    
    for pattern in author_title_patterns:
        pattern_match = re.search(pattern, cleaned_ref)
        if pattern_match:
            authors_text = pattern_match.group(1).strip()
            title = pattern_match.group(2).strip()
            break
    
    # If no specific pattern matched, fall back to the original simple pattern but with validation
    if not authors_text or not title:
        simple_pattern = re.search(r'([^\.]+)\.([^\.]+)\.', cleaned_ref)
        if simple_pattern:
            potential_authors = simple_pattern.group(1).strip()
            potential_title = simple_pattern.group(2).strip()
            # Only use this if the potential_title doesn't look like part of author names
            if not re.match(r'^\s*[A-Z][a-z]*(?:\s+[A-Z][a-z]*)*(?:,\s*and\s+)?', potential_title):
                authors_text = potential_authors
                title = potential_title
    
    # Fallback: if the reference is just a comma-separated list of names, treat as authors
    if not title and not authors_text:
        # Try to detect a list of names
        if re.match(r'^[A-Z][a-zA-Z\-\.]+(,\s*[A-Z][a-zA-Z\-\.]+)+$', cleaned_ref):
            from refchecker.utils.text_utils import parse_authors_with_initials
            authors = parse_authors_with_initials(cleaned_ref)
            return authors, ""
    
    if authors_text and title:
        # Extract authors
        authors = extract_authors_list(authors_text)
        # Clean the title
        title = clean_title(title)
        if authors and title:
            return authors, title
    
    # Final fallback: if the reference is just a list of names, return as authors
    if not title and cleaned_ref and re.match(r'^[A-Z][a-zA-Z\-\.]+(,\s*[A-Z][a-zA-Z\-\.]+)+$', cleaned_ref):
        from refchecker.utils.text_utils import parse_authors_with_initials
        authors = parse_authors_with_initials(cleaned_ref)
        return authors, ""
    
    # Fallback: if the reference is just a list of author names (with initials, and 'and' before last author), treat as authors
    if not title and not authors_text:
        # Match patterns like 'Tara F. Bishop, Matthew J. Press, Salomeh Keyhani, and Harold Alan Pincus'
        author_list_pattern = r'^(?:[A-Z][a-zA-Z\-]+(?:\s+[A-Z]\.)?(?:\s+[A-Z][a-zA-Z\-]+)?(?:,\s+)?)+(?:and\s+[A-Z][a-zA-Z\-]+(?:\s+[A-Z]\.)?(?:\s+[A-Z][a-zA-Z\-]+)?)?$'
        if re.match(author_list_pattern, cleaned_ref.replace(' and ', ', and ')):
            # Split on ', ' and ' and ' for the last author
            authors = re.split(r',\s+|\s+and\s+', cleaned_ref)
            cleaned_authors = []
            for a in authors:
                a = a.strip()
                # Remove leading "and" from author names (handles cases like "and Krishnamoorthy, S")
                a = re.sub(r'^and\s+', '', a)
                if a:
                    cleaned_authors.append(a)
            authors = cleaned_authors
            return authors, ""
    
    return None

def extract_authors_title_fallback(ref_text):
    """
    Fallback method to extract authors and title when the main method fails.
    
    Args:
        ref_text: The reference text to parse
        
    Returns:
        Tuple of (authors list, title)
    """
    # Normalize the text
    cleaned_ref = re.sub(r'\s+', ' ', ref_text).strip()
    
    # Remove any reference number
    cleaned_ref = re.sub(r'^\s*\[\d+\]\s*', '', cleaned_ref)
    
    # Check if this is a URL reference
    if re.match(r'^https?://', cleaned_ref):
        url_match = re.search(r'(https?://[^\s]+)', cleaned_ref)
        if url_match:
            url = url_match.group(1).strip()
            return [{"is_url_reference": True}], cleaned_ref.replace(url, '').strip()
    
    # Try to find anything that looks like a title (text between quotes)
    title_match = re.search(r'[""]([^""]+)[""]', cleaned_ref)
    if title_match:
        title = title_match.group(1).strip()
        # If we found a title in quotes, try to extract authors before it
        before_title = cleaned_ref[:title_match.start()].strip()
        # Process authors text
        authors = extract_authors_list(before_title)
        
        # Clean the title
        title = clean_title(title)
        
        return authors, title
    
    # Look for common patterns that indicate the end of authors and beginning of title
    # This is typically a period followed by a capitalized word
    
    # Check for specific keywords that often appear after title
    title_end_markers = [
        r'\.\s+arXiv',
        r'\.\s+In\s+',
        r'\.\s+CoRR',
        r'\.\s+Proceedings',
        r'\.\s+Journal',
        r'\.\s+IEEE',
        r'\.\s+ACM',
    ]
    
    for marker in title_end_markers:
        match = re.search(marker, cleaned_ref)
        if match:
            # Found a marker, now find the period before it that separates authors and title
            text_before_marker = cleaned_ref[:match.start()]
            period_match = re.search(r'\.', text_before_marker)
            
            if period_match:
                # We found a period that likely separates authors and title
                authors_text = cleaned_ref[:period_match.start()].strip()
                title_text = text_before_marker[period_match.end():].strip()
                
                # Extract authors
                authors = extract_authors_list(authors_text)
                
                # Clean the title
                title_text = clean_title(title_text)                    
                return authors, title_text
    
    # Look for pattern with publication indicator (e.g., "CoRR abs/...")
    corr_match = re.search(r'(CoRR\s+abs\/[\d\.]+)', cleaned_ref)
    if corr_match:
        corr_pos = corr_match.start()
        # Now find the periods before this point
        periods_before = [m.start() for m in re.finditer(r'\.', cleaned_ref[:corr_pos])]
        
        if len(periods_before) >= 2:
            # First period likely separates authors from title
            first_period = periods_before[0]
            # Second period likely ends the title
            second_period = periods_before[1]
            
            authors_text = cleaned_ref[:first_period].strip()
            title_text = cleaned_ref[first_period+1:second_period].strip()
            
            # Extract authors
            authors = extract_authors_list(authors_text)
            
            # Clean the title
            title_text = clean_title(title_text)
            return authors, title_text
    
    # If we get here, try a simple split by the first period
    parts = cleaned_ref.split('.', 1)
    
    if len(parts) > 1:
        authors_text = parts[0].strip()
        title = parts[1].strip()
        
        # Extract authors
        authors = extract_authors_list(authors_text)
        
        # Clean the title
        title = clean_title(title)            
        return authors, title
    
    # If nothing else worked, try to find year and use it as a separator
    year_match = re.search(r'\b(19|20)\d{2}\b', cleaned_ref)
    if year_match:
        year_pos = year_match.start()
        # Everything before the year might be authors
        authors_text = cleaned_ref[:year_pos].strip()
        # Everything after could be title
        title = cleaned_ref[year_pos:].strip()
        
        # Extract authors
        authors = extract_authors_list(authors_text)
        
        # Clean the title
        title = clean_title(title)
        return authors, title
    
    # If all else fails, return placeholder values
    return ["Unknown Author"], "Untitled Reference"
   
def extract_arxiv_id_from_url_app(url):
    """
    Extract ArXiv ID from a URL or text containing ArXiv reference.
    Uses the common extraction function from refchecker.utils.url_utils.
    """
    return extract_arxiv_id_from_url(url)

def extract_authors_list(authors_text):
    """
    Extract a list of authors from text.
    Handles various formats including names with initials.
    
    Args:
        authors_text: Text containing only the author names
        
    Returns:
        List of author names
    """
    # Check if the text is a URL
    if re.match(r'^https?://', authors_text):
        # This is a URL, not an author list
        return [{"is_url_reference": True}]
    
    # Normalize whitespace and fix line breaks in names
    authors_text = re.sub(r'\s+', ' ', authors_text).strip()
    
    # Handle cases like "Vinyals & Kaiser" -> "Vinyals, Kaiser"
    authors_text = re.sub(r'([A-Za-z]+)\s*&\s*([A-Za-z]+)', r'\1, \2', authors_text)
    
    # Fix common hyphenation issues from line breaks (e.g., "Fredrik- son" -> "Fredrikson")
    authors_text = re.sub(r'([a-z])- ([a-z])', r'\1\2', authors_text, flags=re.IGNORECASE)
    
    # Normalize spacing around periods
    authors_text = re.sub(r'([A-Z])\s+\.\s+', r'\1. ', authors_text)
    
    # Fix issues with spaces between initials (e.g., "V . Le" -> "V. Le")
    authors_text = re.sub(r'([A-Z])\s+\.\s*([A-Z])', r'\1. \2', authors_text)
    authors_text = re.sub(r'([A-Z])\s+\.\s*([a-z])', r'\1. \2', authors_text)
    
    # Check if we potentially have a full reference instead of just authors
    # Look for patterns that indicate this might include the title
    # Be more specific: look for period followed by what looks like a title (multiple words, starting with capital)
    # This should match title patterns but not author name patterns like "J. Zico"
    title_pattern = r'\.\s+([A-Z]\w+(?:\s+\w+){2,})'  # Capital word followed by at least 2 more words
    if re.search(title_pattern, authors_text) and ',' in authors_text:
        # This appears to be a complete reference, not just authors
        # Only take the part before the title
        match = re.search(title_pattern, authors_text)
        if match:
            title_start = match.start()
            authors_text = authors_text[:title_start].strip()
    
    # Check if the author list follows the pattern: "Author1, Author2, and Author3"
    # This is the most common format in academic citations
    
    # First, handle the case where "and" appears before the last author
    and_parts = re.split(r'\s+and\s+', authors_text, 1)
    
    if len(and_parts) > 1:
        # We have a list with "and" (e.g., "Author1, Author2, and Author3")
        main_list = and_parts[0].strip()
        last_author = and_parts[1].strip()
        
        # Split the main list by commas, handling initials properly
        from refchecker.utils.text_utils import parse_authors_with_initials
        authors = parse_authors_with_initials(main_list)
        
        # Add the last author
        if last_author:
            authors.append(last_author)
    else:
        # No "and" found, use smart comma parsing for initials
        from refchecker.utils.text_utils import parse_authors_with_initials
        authors = parse_authors_with_initials(authors_text)
    
    # Clean up each author name
    cleaned_authors = []
    for author in authors:
        cleaned_author = clean_author_name(author)
        if cleaned_author:
            cleaned_authors.append(cleaned_author)
    
    return cleaned_authors

def find_bibliography_section(text):
    """
    Find the bibliography section in the text
    """
    global logger_debug
    global logger_warning
    global logger_info
    if not text:
        logger_warning += ("\nNo text provided to find_bibliography_section")
        return None
    
    # Log a sample of the text for debugging
    text_sample = text[:500] + "..." if len(text) > 500 else text
    logger_debug += (f"\nText sample: {text_sample}")
    
    # Common section titles for bibliography
    section_patterns = [
        # Patterns for numbered sections with potential spacing issues from PDF extraction
        r'(?i)\d+\s*ref\s*er\s*ences\s*\n',  # "12 Refer ences" with spaces
        r'(?i)\d+\s*references\s*\n',  # "12References" or "12 References"
        r'(?i)^\s*\d+\.\s*references\s*$',  # Numbered section: "7. References"
        r'(?i)\d+\s+references\s*\.',  # "9 References." format used in Georgia Tech paper
        # Standard reference patterns
        r'(?i)references\s*\n',
        r'(?i)bibliography\s*\n',
        r'(?i)works cited\s*\n',
        r'(?i)literature cited\s*\n',
        r'(?i)references\s*$',  # End of document
        r'(?i)\[\s*references\s*\]',  # [References]
        r'(?i)^\s*references\s*$',  # References as a standalone line
        r'(?i)^\s*bibliography\s*$',  # Bibliography as a standalone line
        r'(?i)references\s*and\s*citations',  # References and Citations
        r'(?i)cited\s*references',  # Cited References
        r'(?i)reference\s*list',  # Reference List
        r'(?i)references\s*cited',  # References Cited
        r'(?i)sources\s*cited',  # Sources Cited
        r'(?i)references\s*and\s*notes',  # References and Notes
        r'\\begin\{thebibliography\}',  # LaTeX bibliography environment
        r'\\bibliography\{[^}]+\}',  # BibTeX \bibliography{} command
        # Roman numeral patterns
        r'(?i)^\s*[IVX]+\.\s*references\s*$',  # "IX. References"
        r'(?i)^\s*[IVX]+\s*references\s*$',   # "IX References"
        # Generic patterns that might match false positives - put at end
        r'(?i)^\s*sources\s*$',  # Sources as section header only
    ]
    
    # Try to find the bibliography section
    bibliography_text = None
    
    # Collect all potential matches from all patterns
    all_matches = []
    for pattern in section_patterns:
        matches = list(re.finditer(pattern, text))
        for match in matches:
            all_matches.append((pattern, match))
    
    if all_matches:
        # Find the match that has [1] following it (indicating start of references)
        best_match = None
        best_pattern = None
        
        for pattern, match in all_matches:
            test_start = match.end()
            # Look for [1] within reasonable distance after the match
            test_text = text[test_start:test_start + 100]
            if '[1]' in test_text:
                best_match = match
                best_pattern = pattern
                break
        
        # If no match has [1] following it, fall back to the last match
        if not best_match:
            best_pattern, best_match = all_matches[-1]
        
        match = best_match
        start_pos = match.end()
        
        logger_debug += (f"\nFound bibliography section with pattern: {best_pattern}")
        logger_debug += (f"\nMatch: {match.group(0)}")
        
        # Find the next section heading or end of document
        # Look for common section endings that come after references
        next_section_patterns = [
            # HIGHEST PRIORITY: Table/Data patterns that immediately follow references
            r'\n\s*(?:Relation|Table|Figure)\s*#?\s*(?:Samples|[0-9]+[:\.]?)\s+.*\n',  # "Relation # Samples", "Table 1:", "Figure 1:"
            r'\n\s*[A-Za-z\s]+\s+#\s+[A-Za-z\s]+\s+[A-Za-z\s]+\s+[A-Za-z\s]+\n',  # Structured table headers like "Relation # Samples Context Templates Query Templates"
            # HIGH PRIORITY: Appendix patterns that appear at the end of bibliography
            # General pattern for single letter appendix sections - must come FIRST to catch earliest occurrence
            r'\n\s*[A-Z]\s+(?:[A-Z]{2,}|[A-Z][a-z]+)(?:\s+(?:[A-Z]{2,}|[A-Z][a-z]+))*\s*\n',  # "A LRE Dataset", "A Theoretical Analysis", "B Implementation Details"
            # Specific patterns (kept for backwards compatibility but won't be reached in most cases)
            r'\n\s*[A-Z]\s+Evaluation\s+Details\s*\n',  # Specific "C Evaluation Details"
            # Note: Removed problematic pattern that was matching page numbers in bibliography
            r'\n\s*\d+\.\d+\s+[A-Z][A-Za-z\s]+\n',  # "3.1 Subsection Title"
            # High priority: Common supplementary material patterns
            r'\n\s*SUPPLEMENTARY\s+MATERIAL\s*\n',
            r'\n\s*Supplementary\s+Material\s*\n',  
            r'\n\s*SUPPLEMENTAL\s+MATERIAL\s*\n',
            r'\n\s*Supplemental\s+Material\s*\n',
            r'\n\s*APPENDIX\s*[A-Z]?\s*\n',
            r'\n\s*Appendix\s*[A-Z]?\s*\n',
            r'\n\s*ACKNOWLEDGMENTS?\s*\n',
            r'\n\s*Acknowledgments?\s*\n',
            r'\n\s*AUTHOR\s+CONTRIBUTIONS?\s*\n',
            r'\n\s*Author\s+Contributions?\s*\n',
            r'\n\s*DATA\s+AVAILABILITY\s*\n',
            r'\n\s*Data\s+Availability\s*\n',
            r'\n\s*CODE\s+AVAILABILITY\s*\n',
            r'\n\s*Code\s+Availability\s*\n',
            r'\n\s*SUPPORTING\s+INFORMATION\s*\n',
            r'\n\s*Supporting\s+Information\s*\n',
            r'\n\s*SUPPLEMENTARY\s+INFORMATION\s*\n',
            r'\n\s*Supplementary\s+Information\s*\n',
            r'\n\s*ETHICS\s+STATEMENT\s*\n',
            r'\n\s*Ethics\s+Statement\s*\n',
            r'\n\s*COMPETING\s+INTERESTS\s*\n',
            r'\n\s*Competing\s+Interests\s*\n',
            r'\n\s*FUNDING\s+INFORMATION\s*\n',
            r'\n\s*Funding\s+Information\s*\n',
            # Pattern for "A Additional...", "B Supplementary...", etc.
            r'\n\s*[A-Z]\s+(?:Additional|Supplementary|Appendix|Extended|Extra|Further)\b[A-Za-z\s\-]*',
            # Pattern for appendix sections like "A Proofs for Section 2", "B Details", etc.
            r'\n\s*[A-Z]\s+(?:Proofs?|Details?|Derivations?|Calculations?|Algorithms?|Examples?|Experiments?|Implementation|Results?)\b[A-Za-z\s\-\d]*',
            # Original patterns
            r'\n\s*[A-Z]\s+[A-Z][A-Za-z\s]*\n',  # A APPENDIX, B RESULTS, etc.
            r'\nA\.\s+Related\s+Work\n',  # Exact match for "A. Related Work"
            r'\n\s*[A-Z]\.\s+(?:ADDITIONAL|SUPPLEMENTARY|CONCLUSION|DISCUSSION|APPENDIX|NOTATION|PROOF|ALGORITHM|ACKNOWLEDGMENT|FUNDING|AUTHOR|CONFLICT|ETHICS|EXPERIMENTAL|THEORETICAL|IMPLEMENTATION|COMPARISON|EVALUATION|RESULTS|ANALYSIS|METHODOLOGY|INTRODUCTION|BACKGROUND|LITERATURE|SURVEY|REVIEW|FUTURE|LIMITATION|CONTRIBUTION|INNOVATION|TECHNICAL|DETAILED|COMPLETE|EXTENDED)\b',  # Other section patterns
            r'\n\s*[A-Z]\.\s+Implementation\s+Details',  # Specific pattern for "A. Implementation Details"
            # More specific pattern for numbered sections - only match section headers, not bibliography entries
            # Look for common section headers like "8. Appendix", "9. Conclusion" but not "8. Smith, J."
            r'\n\s*\d+\.\s+(?:APPENDIX|CONCLUSION|SUPPLEMENTARY|ADDITIONAL|NOTATION|PROOF|ALGORITHM|ACKNOWLEDGMENT|FUNDING|AUTHOR|CONFLICT|ETHICS|DATA|CODE|SUPPORTING|COMPETING|AVAILABILITY|INFORMATION|STATEMENT|CONTRIBUTIONS?)\b[A-Za-z\s]*\n',
            r'\n\s*Appendix\s+[A-Z]',  # Appendix A
            # More restrictive pattern for bracketed sections - only match actual section headers
            # like [APPENDIX], [CONCLUSIONS] but not reference metadata like [Online], [cs], [PDF]
            r'\n\s*\[\s*(?:APPENDIX|CONCLUSIONS?|ACKNOWLEDGMENTS?|SUPPLEMENTARY|ADDITIONAL|NOTATION|PROOF|ALGORITHM)\s*\]',
            # Pattern for consecutive capitalized lines that are clearly section headers (short and uppercase)
            r'\n\s*[A-Z]{3,}\s*\n\s*[A-Z]{3,}\s*\n',  # All caps sections like "APPENDIX\nALGORITHM"
            r'\\end\{thebibliography\}',  # LaTeX bibliography environment end
            r'\\end\{document\}',  # LaTeX document end
        ]
        
        end_pos = len(text)  # Default to end of document
        
        for i, next_pattern in enumerate(next_section_patterns, 1):
            next_match = re.search(next_pattern, text[start_pos:])
            if next_match:
                section_end = start_pos + next_match.start()
                logger_debug += (f"\nPATTERN {i} MATCHED: {next_pattern}")
                logger_debug += (f"\nMATCHED TEXT: {repr(next_match.group(0))}")
                logger_debug += (f"\nCONTEXT: {repr(text[section_end-30:section_end+30])}")
                
                # For table/data patterns, make sure we don't cut off mid-reference
                # Look backwards to find the end of the previous complete reference
                if i <= 2:  # First two patterns are table/data patterns
                    # Look backwards from the match to find the end of the last reference
                    # References typically end with a period, year, or arXiv ID
                    search_back = 500  # Look back up to 500 chars
                    search_start = max(start_pos, section_end - search_back)
                    text_before = text[search_start:section_end]
                    
                    # Look for patterns that indicate end of a reference
                    ref_end_patterns = [
                        r'arXiv:\d+\.\d+[v\d]*\.\s*',  # arXiv ID with period
                        r'Preprint[,\.]?\s*arXiv:\d+\.\d+[v\d]*\.\s*',  # Preprint with arXiv ID
                        r'20\d{2}[.\n]\s*',  # Year (2000-2099) with period or newline
                        r'[A-Za-z]+\.\s*$',  # Word ending with period
                    ]
                    
                    best_end = section_end
                    for pattern in ref_end_patterns:
                        matches = list(re.finditer(pattern, text_before, re.MULTILINE))
                        if matches:
                            # Get the last match (closest to section_end)
                            last_match = matches[-1]
                            potential_end = search_start + last_match.end()
                            logger_debug += (f"\nFound reference end pattern: {pattern} at position {potential_end}")
                            best_end = potential_end
                            break
                    
                    section_end = best_end
                    logger_debug += (f"\nAdjusted end position to: {section_end}")
                
                # Only use this end position if it's reasonable (not too close to start)
                if section_end > start_pos + 100 and section_end < end_pos:
                    end_pos = section_end
                    logger_debug += (f"\nACCEPTED: End position set to {section_end}")
                    break
                else:
                    logger_debug += (f"\nREJECTED: section_end={section_end}, start_pos={start_pos}, current_end={end_pos}")
        
        bibliography_text = text[start_pos:end_pos]
        logger_debug += (f"\nFINAL BIBLIOGRAPHY: start_pos={start_pos}, end_pos={end_pos}, length={len(bibliography_text)}")
        
        # Check if we have a reasonable amount of text
        if len(bibliography_text.strip()) < 50:
            logger_warning += (f"\nBibliography section seems too short ({len(bibliography_text)} chars)")
        
        logger_debug += (f"\nBibliography section length: {len(bibliography_text)} chars")
        logger_debug += (f"\nBibliography sample: {bibliography_text[:200]}...")
    
    if bibliography_text is None:
        logger_warning += ("\nCould not find bibliography section with standard patterns")
        
        # Last resort: look for patterns that might indicate references
        reference_indicators = [
            r'\[\d+\]',  # [1], [2], etc.
            r'\d+\.\s+[A-Z]',  # 1. Author
            r'[A-Z][a-z]+,\s+[A-Z]\.',  # Smith, J.
        ]
        
        for indicator in reference_indicators:
            matches = list(re.finditer(indicator, text))
            if len(matches) > 5:  # If we find multiple matches, it might be a reference section
                # Find the first match
                first_match = matches[0]
                # Look for the beginning of the line
                line_start = text.rfind('\n', 0, first_match.start())
                if line_start == -1:
                    line_start = 0
                else:
                    line_start += 1  # Skip the newline
                
                # Take from there to the end
                bibliography_text = text[line_start:]
                logger_info += (f"\nFound potential bibliography section using indicator: {indicator}")
                break
    
    return bibliography_text

def format_standard_reference(error):
    """
    Format a reference in standard ArXiv format
    
    Args:
        error: Error dictionary containing correct reference information
        
    Returns:
        String in standard ArXiv format
    """
    try:
        # Use correct information if available, otherwise fall back to cited information
        authors = error.get('ref_authors_correct') or error.get('ref_authors_cited', '')
        year = error.get('ref_year_correct') or error.get('ref_year_cited', '')
        title = error.get('ref_title', '')
        url = error.get('ref_url_correct') or error.get('ref_url_cited', '')
        
        # Format in standard academic format
        formatted = ""
        
        if authors:
            # Limit to first 3 authors for readability
            from refchecker.utils.text_utils import parse_authors_with_initials
            author_list = parse_authors_with_initials(authors)
            if len(author_list) > 3:
                formatted += ", ".join(author_list[:3]) + " et al."
            else:
                formatted += authors
            formatted += ". "
        
        if title:
            formatted += f'"{title}". '
        
        if url and 'arxiv.org' in url:
            # Extract ArXiv ID
            arxiv_match = re.search(r'(\d+\.\d+(?:v\d+)?)', url)
            if arxiv_match:
                arxiv_id = arxiv_match.group(1)
                formatted += f"arXiv preprint arXiv:{arxiv_id}. "
        
        if year:
            formatted += f"({year})"
        
        return formatted.strip()
        
    except Exception as e:
        logger.error(f"Error formatting standard reference: {str(e)}")
        return ""
    
def parse_references(bibliography_text):
    """
    Parse references from bibliography text
    """
    global logger_debug
    global logger_warning
    global logger_info
    global logger_error
    llm_extractor = False
    if not bibliography_text:
        logger_warning += ("\nNo bibliography text provided to parse_references")
        return []
    
    # Log a sample of the bibliography text for debugging
    bib_sample = bibliography_text[:500] + "..." if len(bibliography_text) > 500 else bibliography_text
    logger_debug += (f"\nBibliography sample: {bib_sample}")

    # Check if this is a standard ACM/natbib format first
    if detect_standard_acm_natbib_format(bibliography_text):
        logger_info += ("\nDetected standard ACM/natbib format, using regex-based parsing")
        used_regex_extraction = True
        # Note: ACM/natbib parsing is also quite robust for standard formats
        return _parse_standard_acm_natbib_references(bibliography_text)
    
    # Check if this is BibTeX format
    from refchecker.utils.bibtex_parser import detect_bibtex_format
    if detect_bibtex_format(bibliography_text):
        logger_info += ("\nDetected BibTeX format, using BibTeX parser")
        used_regex_extraction = True
        # Note: BibTeX parsing is robust, so we don't set used_unreliable_extraction
        return _parse_bibtex_references(bibliography_text)
    
    # Check if this is biblatex format  
    from refchecker.utils.biblatex_parser import detect_biblatex_format
    if detect_biblatex_format(bibliography_text):
        logger_debug += ("\nDetected biblatex format")
        used_regex_extraction = True
        # Note: biblatex parsing is also robust, so we don't set used_unreliable_extraction
        biblatex_refs = _parse_biblatex_references(bibliography_text)
        
        # If biblatex parsing returned empty results (due to quality validation),
        # fallback to LLM if available
        if not biblatex_refs and llm_extractor:
            logger_debug += ("\nBiblatex is incompatible with parser")
            try:
                references = llm_extractor.extract_references(bibliography_text)
                if references:
                    logger_debug += (f"\nLLM fallback extracted {len(references)} references")
                    return _process_llm_extracted_references(references)
                else:
                    logger_warning += ("\nLLM fallback also returned no results")
                    return []
            except Exception as e:
                logger_error += (f"\nLLM fallback failed: {e}")
                return []
        if len(biblatex_refs) > 0:
            logger_debug += ("\nUsing biblatex file")
            return biblatex_refs
    
    # For non-standard formats, try LLM-based extraction if available
    if llm_extractor:
        try:
            logger_info += ("\nNon-standard bibliography format detected, using LLM-based extraction")
            references = llm_extractor.extract_references(bibliography_text)
            if references:
                logger_debug += (f"\nParsed {len(references)} references")
                return _process_llm_extracted_references(references)
            else:
                # LLM was specified but failed - this is terminal
                logger_error += ("\nLLM reference extraction returned no results. Terminating.")
                fatal_error = True
                return []
        except Exception as e:
            logger_error += (f"\nLLM reference extraction failed: {e}")
            fatal_error = True
            return []
    
    # Fallback to regex-based parsing only if LLM was not specified
    logger_info += ("\nNo LLM available, falling back to regex-based parsing")
    used_regex_extraction = True
    used_unreliable_extraction = True  # This is the unreliable fallback parsing
    return _parse_references_regex(bibliography_text)

def parse_arxiv_entry(entry):
    """Parse a single ArXiv entry from XML response"""
    global logger_debug
    global logger_warning
    global logger_info
    global logger_error
    try:
        # Find the namespace
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        
        # Extract basic information
        title_elem = entry.find('.//atom:title', ns)
        title = title_elem.text.strip() if title_elem is not None else ''
        
        # Extract ArXiv ID from the id field
        id_elem = entry.find('.//atom:id', ns)
        if id_elem is not None:
            arxiv_url = id_elem.text.strip()
            arxiv_id = arxiv_url.split('/')[-1]  # Extract ID from URL
        else:
            return None
        
        # Extract authors
        authors = []
        for author in entry.findall('.//atom:author', ns):
            name_elem = author.find('.//atom:name', ns)
            if name_elem is not None:
                authors.append(name_elem.text.strip())
        
        # Extract year from published date
        published_elem = entry.find('.//atom:published', ns)
        year = ''
        if published_elem is not None:
            published_date = published_elem.text.strip()
            year = published_date[:4]  # Extract year
        
        # Extract abstract
        summary_elem = entry.find('.//atom:summary', ns)
        abstract = summary_elem.text.strip() if summary_elem is not None else ''
        
        return {
            'arxiv_id': arxiv_id,
            'title': title,
            'authors': authors,
            'year': year,
            'abstract': abstract,
            'url': arxiv_url
        }
        
    except Exception as e:
        logger_debug += (f"\nFailed to parse ArXiv entry: {e}")
        return None

def remove_urls_from_title(title):
    """
    Remove URLs and DOIs from titles.
    
    Args:
        title: The title string to clean
        
    Returns:
        Title string with URLs and DOIs removed
    """
    if not title:
        return ""
    
    # Remove DOI URLs
    title = re.sub(r'\s*https?://doi\.org/[^\s]+', '', title, flags=re.IGNORECASE)
    
    # Remove other URLs
    title = re.sub(r'\s*https?://[^\s]+', '', title, flags=re.IGNORECASE)
    
    # Remove arXiv IDs that might be in titles
    title = re.sub(r'\s*arXiv:\d+\.\d+(?:v\d+)?', '', title, flags=re.IGNORECASE)
    
    # Clean up any trailing punctuation and whitespace
    title = re.sub(r'\s*[.,;:]+\s*$', '', title)
    title = title.strip()
    
    return title

def batch_prefetch_arxiv_references(bibliography):
    """Pre-fetch all ArXiv references in batches to improve performance"""
    global logger_debug
    global logger_warning
    global logger_info
    global logger_error
    if not bibliography:
        return
        
    # Initialize cache if not exists
    _metadata_cache = {}
    
    # Collect all ArXiv IDs that need to be fetched
    arxiv_ids_to_fetch = []
    for reference in bibliography:
        if reference.get('type') == 'arxiv':
            arxiv_id = extract_arxiv_id_from_url(reference.get('url', ''))
            if arxiv_id and arxiv_id not in _metadata_cache:
                arxiv_ids_to_fetch.append(arxiv_id)
    
    if not arxiv_ids_to_fetch:
        return
        
    logger_debug += (f"\nPre-fetching {len(arxiv_ids_to_fetch)} ArXiv references in batches...")
    
    # Process in batches to avoid overwhelming the APIs
    batch_size = 10
    for i in range(0, len(arxiv_ids_to_fetch), batch_size):
        batch = arxiv_ids_to_fetch[i:i+batch_size]
        logger_debug += (f"\nProcessing batch {i//batch_size + 1}/{(len(arxiv_ids_to_fetch) + batch_size - 1)//batch_size}")
        
        # Try to batch fetch from arXiv API (supports multiple IDs)
        try:
            batch_results = batch_fetch_from_arxiv(batch)
            for arxiv_id, metadata in batch_results.items():
                _metadata_cache[arxiv_id] = metadata
        except Exception as e:
            logger_warning += (f"\nBatch fetch failed, falling back to individual fetches: {e}")
            # Fallback to individual fetches for this batch
            for arxiv_id in batch:
                try:
                    metadata = get_paper_metadata(arxiv_id)
                    if metadata:
                        _metadata_cache[arxiv_id] = metadata
                except Exception as e:
                    logger_debug += (f"\nFailed to fetch {arxiv_id}: {e}")
                    
    logger_debug += (f"\nPre-fetched {len(_metadata_cache)} ArXiv references")

def batch_fetch_from_arxiv(arxiv_ids):
    """Fetch multiple ArXiv papers in a single API call"""
    global logger_debug
    global logger_warning
    global logger_info
    global logger_error
    if not arxiv_ids:
        return {}
        
    # ArXiv API supports multiple IDs in a single request
    id_list = ','.join(arxiv_ids)
    search_query = f"id_list={id_list}"
    
    url = f"https://export.arxiv.org/api/query?{search_query}&max_results={len(arxiv_ids)}"
    
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        
        # Parse the XML response
        import xml.etree.ElementTree as ET
        root = ET.fromstring(response.text)
        
        results = {}
        for entry in root.findall('.//{http://www.w3.org/2005/Atom}entry'):
            # Extract metadata from each entry
            metadata = parse_arxiv_entry(entry)
            if metadata and metadata.get('arxiv_id'):
                results[metadata['arxiv_id']] = metadata
                
        return results
        
    except Exception as e:
        logger_warning += (f"\nBatch ArXiv fetch failed: {e}")
        return {}

def get_paper_metadata(arxiv_id):
    """
    Get metadata for a paper using its ArXiv ID with intelligent API switching.
    Priority: Local DB > Semantic Scholar API > arXiv API, with fallback switching.
    """
    global logger_debug
    global logger_warning
    global logger_info
    global logger_error
    db_path = None
    # First, try to get the paper from local Semantic Scholar database
    logger_debug += (f"\nAttempting to fetch {arxiv_id} from local database first")
    # local_result = get_arxiv_paper_from_local_db(arxiv_id)
    local_result = None
    
    if local_result:
        logger_debug += (f"\nSuccessfully found {arxiv_id} in local database")
        return local_result
    
    # Check cache before making API calls
    # if arxiv_id in _metadata_cache:
    #     logger_debug += (f"\nSuccessfully found {arxiv_id} in cache")
    #     return _metadata_cache[arxiv_id]
    
    # If not found in local database but we have a local DB, try ArXiv API as fallback
    if db_path:
        logger_debug += (f"\nPaper {arxiv_id} not found in local database, trying ArXiv API fallback")
        return get_paper_metadata_with_api_switching(arxiv_id)
    
    # If no local database, try both APIs with intelligent switching
    return get_paper_metadata_with_api_switching(arxiv_id)

def get_paper_metadata_with_api_switching(arxiv_id):
    """
    Get paper metadata with intelligent API switching between Semantic Scholar and arXiv APIs
    
    Args:
        arxiv_id: arXiv ID of the paper
        
    Returns:
        Paper object or None if not found
    """
    global logger_debug
    global logger_warning
    global logger_info
    global logger_error
    # Track API performance for this session
    _api_performance = {
        'semantic_scholar': {'success': 0, 'rate_limited': 0, 'failed': 0},
        'arxiv': {'success': 0, 'rate_limited': 0, 'failed': 0}
    }

    # Try arXiv API
    logger_debug += (f"\nSemantic Scholar API failed for {arxiv_id}, trying arXiv API")
    arxiv_result = get_paper_metadata_from_arxiv(arxiv_id)
    
    if arxiv_result:
        _api_performance['arxiv']['success'] += 1
        logger_debug += (f"\nSuccessfully fetched {arxiv_id} from arXiv API")
        return arxiv_result
    
    # Try Semantic Scholar API 
    logger_debug += (f"\nTrying Semantic Scholar API for {arxiv_id}")
    semantic_result = get_paper_metadata_from_semantic_scholar(arxiv_id)
    
    if semantic_result:
        _api_performance['semantic_scholar']['success'] += 1
        logger_debug += (f"\nSuccessfully fetched {arxiv_id} from Semantic Scholar API")
        return semantic_result
    
    # If both failed, try reverse order (sometimes one API works when the other doesn't)
    logger_debug += (f"\nBoth APIs failed for {arxiv_id}, trying reverse order")
    
    # Try arXiv API first this time
    arxiv_result = get_paper_metadata_from_arxiv(arxiv_id)
    if arxiv_result:
        _api_performance['arxiv']['success'] += 1
        logger_debug += (f"\nSuccessfully fetched {arxiv_id} from arXiv API (reverse order)")
        return arxiv_result
    
    # Try Semantic Scholar API again
    semantic_result = get_paper_metadata_from_semantic_scholar(arxiv_id)
    if semantic_result:
        _api_performance['semantic_scholar']['success'] += 1
        logger_debug += (f"\nSuccessfully fetched {arxiv_id} from Semantic Scholar API (reverse order)")
        return semantic_result
    
    # Both APIs failed
    logger_debug += (f"\nPaper {arxiv_id} not found in any source")
    return None

def get_paper_metadata_from_semantic_scholar(arxiv_id):
    """
    Get paper metadata from Semantic Scholar API
    
    Args:
        arxiv_id: arXiv ID of the paper
        
    Returns:
        MockArxivPaper object or None if not found
    """
    global logger_debug
    global logger_warning
    global logger_info
    global logger_error
    _api_performance = {
        'semantic_scholar': {'success': 0, 'rate_limited': 0, 'failed': 0},
        'arxiv': {'success': 0, 'rate_limited': 0, 'failed': 0}
    }
    try:
        import requests
        
        url = f"https://api.semanticscholar.org/graph/v1/paper/arXiv:{arxiv_id}"
        params = {
            'fields': 'title,authors,year,externalIds,abstract,url'
        }
        
        response = requests.get(url, params=params, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            
            # Create a mock arXiv paper object from Semantic Scholar data
            class MockArxivPaper:
                def __init__(data, arxiv_id):
                    title = data.get('title', 'Unknown Title')
                    
                    # Create a proper published object with year attribute
                    class MockPublished:
                        def __init__(year):
                            year = year
                    
                    published = MockPublished(data.get('year', 0))
                    
                    # Convert authors to the format expected by the rest of the code
                    authors_data = data.get('authors', [])
                    authors = []
                    for author in authors_data:
                        class MockAuthor:
                            def __init__(name):
                                name = name
                            def __str__(self):
                                return name
                            def __repr__(self):
                                return f"MockAuthor('{name}')"
                        authors.append(MockAuthor(author.get('name', 'Unknown Author')))
                    
                    arxiv_id = arxiv_id
                    external_ids = data.get('externalIds', {})
                    abstract = data.get('abstract', '')
                    url = data.get('url', '')
                    
                    # Add pdf_url for compatibility with the rest of the code
                    pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
                
                def get_short_id(self):
                    return arxiv_id
                
                def __str__(self):
                    return f"MockArxivPaper('{title}', {len(authors)} authors, {published.year})"
                
                def __repr__(self):
                    return __str__()
            
            return MockArxivPaper(data, arxiv_id)
            
        elif response.status_code == 429:
            _api_performance['semantic_scholar']['rate_limited'] += 1
            logger_debug += (f"\nRate limited by Semantic Scholar API for {arxiv_id}")
            return None
        else:
            _api_performance['semantic_scholar']['failed'] += 1
            logger_debug += (f"\nSemantic Scholar API returned status {response.status_code} for {arxiv_id}")
            return None
            
    except requests.exceptions.RequestException as e:
        _api_performance['semantic_scholar']['failed'] += 1
        logger_warning += (f"\nError fetching from Semantic Scholar API for {arxiv_id}: {str(e)}")
        return None
    except Exception as e:
        _api_performance['semantic_scholar']['failed'] += 1
        logger_warning += (f"\nUnexpected error fetching from Semantic Scholar API for {arxiv_id}: {str(e)}")
        return None

def get_paper_metadata_from_arxiv(arxiv_id):
    """
    Get paper metadata from arXiv API
    
    Args:
        arxiv_id: arXiv ID of the paper
        
    Returns:
        ArXiv paper object or None if not found
    """
    global logger_debug
    global logger_warning
    global logger_info
    global logger_error
    _api_performance = {
        'semantic_scholar': {'success': 0, 'rate_limited': 0, 'failed': 0},
        'arxiv': {'success': 0, 'rate_limited': 0, 'failed': 0}
    }
    client = arxiv.Client(
            page_size=100,
            delay_seconds=3,  # Rate limiting to avoid overloading the API
            num_retries=5
    )
    try:
        search = arxiv.Search(id_list=[arxiv_id])
        results = list(client.results(search))
        
        if results:
            logger_info += ('\nAPI arxiv OK')
            return results[0]
        else:
            _api_performance['arxiv']['failed'] += 1
            logger_debug += (f"\nPaper {arxiv_id} not found in arXiv API")
            return None
            
    except Exception as e:
        _api_performance['arxiv']['failed'] += 1
        logger_error += (f"\nError fetching metadata from arXiv API for {arxiv_id}: {str(e)}")
        return None

def verify_reference(source_paper, reference):
    """
    Verify if a reference is accurate
    
    Args:
        source_paper: The paper containing the reference
        reference: The reference to verify
            
    Returns:
        Tuple of (errors, url, verified_data) where:
        - errors: List of errors or None if no errors found
        - url: URL of the paper if found, None otherwise
        - verified_data: The verified paper data from the verification service, None if not found
    """
    global logger_debug
    global logger_warning
    global logger_info
    global logger_error
    # Check if reference authors contains "URL Reference" marker
    if reference.get('authors') and "URL Reference" in reference.get('authors', []):
        # Skip verification for URL references
        return None, None, None
    
    # Route all references through the same non-arxiv path for consistent verification
            
    # For non-database mode, use the standard reference verification
    errors, paper_url, verified_data = verify_reference_standard(source_paper, reference)
    
    # If standard verification failed and the reference has a URL, try raw URL verification
    if errors and verified_data is None:
        # Check if there's an unverified error
        unverified_errors = [e for e in errors if e.get('error_type') == 'unverified']
        if unverified_errors and reference.get('url', '').strip():
            # Use raw URL verifier to check if it can be verified or get specific reason
            url_verified_data, url_errors, url_checked = verify_raw_url_reference(reference)
            if url_verified_data:
                # URL verification succeeded - return as verified
                logger_debug += (f"\nNon-database mode: URL verification succeeded for unverified reference")
                return None, url_checked, url_verified_data
            else:
                # URL verification failed - use specific error reason
                url_error_details = url_errors[0].get('error_details', 'Reference could not be verified') if url_errors else 'Reference could not be verified'
                # Update the unverified error with the specific reason
                for error in errors:
                    if error.get('error_type') == 'unverified':
                        error['error_details'] = url_error_details
                        break
    
    return errors, paper_url, verified_data

def verify_github_reference(reference):
    """
    Verify if a reference is a GitHub repository reference
    
    Args:
        reference: The reference to verify
        
    Returns:
        Tuple of (errors, url, verified_data) if this is a GitHub reference,
        None if this is not a GitHub reference
    """
    global logger_debug
    global logger_warning
    global logger_info
    global logger_error
    # Check if this is a GitHub repository reference
    github_url = None
    if reference.get('url') and 'github.com' in reference['url']:
        github_url = reference['url']
    elif reference.get('venue') and 'github.com' in reference.get('venue', ''):
        # Sometimes GitHub URLs are in the venue field
        venue_parts = reference['venue'].split()
        for part in venue_parts:
            if 'github.com' in part:
                github_url = part
                break
    
    if not github_url:
        return None  # Not a GitHub reference
    
    logger_debug += (f"\nDetected GitHub URL, using GitHub verification: {github_url}")
    
    # Import and use GitHub checker
    from refchecker.checkers.github_checker import GitHubChecker
    github_checker = GitHubChecker()
    verified_data, errors, paper_url = github_checker.verify_reference(reference)
    
    if verified_data:
        logger_debug += (f"\nGitHub verification successful for: {reference.get('title', 'Untitled')}")
        # Convert errors to our format if needed
        formatted_errors = []
        for error in errors:
            formatted_error = {}
            
            # Handle error_type, warning_type, and info_type properly
            if 'error_type' in error:
                formatted_error['error_type'] = error['error_type']
                formatted_error['error_details'] = error['error_details']
            elif 'warning_type' in error:
                formatted_error['warning_type'] = error['warning_type']
                formatted_error['warning_details'] = error['warning_details']
            elif 'info_type' in error:
                formatted_error['info_type'] = error['info_type']
                formatted_error['info_details'] = error['info_details']
            
            # Add correct information based on error type
            if error.get('warning_type') == 'year':
                formatted_error['ref_year_correct'] = error.get('ref_year_correct', '')
            elif error.get('info_type') == 'url':
                formatted_error['ref_url_correct'] = error.get('ref_url_correct', '')
            
            formatted_errors.append(formatted_error)
        
        return formatted_errors if formatted_errors else None, paper_url, verified_data
    else:
        logger_debug += (f"\nGitHub verification failed for: {reference.get('title', 'Untitled')}")
        # Return GitHub verification errors
        formatted_errors = []
        for error in errors:
            formatted_error = {}
            if 'error_type' in error:
                formatted_error['error_type'] = error['error_type']
                formatted_error['error_details'] = error['error_details']
            formatted_errors.append(formatted_error)
        return formatted_errors if formatted_errors else [{"error_type": "unverified", "error_details": "GitHub repository could not be verified"}], paper_url, None

def verify_webpage_reference(reference):
    """
    Verify if a reference is a web page reference
    
    Args:
        reference: The reference to verify
        
    Returns:
        Tuple of (errors, url, verified_data) if this is a web page reference,
        None if this is not a web page reference
    """
    global logger_debug
    global logger_warning
    global logger_info
    global logger_error
    # Check if this is a web page reference
    web_url = reference.get('url', '').strip()
    if not web_url:
        return None  # No URL to check
    
    # Import and use web page checker
    from refchecker.checkers.webpage_checker import WebPageChecker
    webpage_checker = WebPageChecker()
    
    if not webpage_checker.is_web_page_url(web_url):
        return None  # Not a web page reference
    
    logger_debug += (f"\nDetected web page URL, using web page verification: {web_url}")
    
    verified_data, errors, page_url = webpage_checker.verify_reference(reference)
    
    if verified_data:
        logger_debug += (f"\nWeb page verification successful for: {reference.get('title', 'Untitled')}")
        # Convert errors to our format if needed
        formatted_errors = []
        for error in errors:
            formatted_error = {}
            
            # Handle error_type, warning_type, and info_type properly
            if 'error_type' in error:
                formatted_error['error_type'] = error['error_type']
                formatted_error['error_details'] = error['error_details']
            elif 'warning_type' in error:
                formatted_error['warning_type'] = error['warning_type']
                formatted_error['warning_details'] = error['warning_details']
            elif 'info_type' in error:
                formatted_error['info_type'] = error['info_type']
                formatted_error['info_details'] = error['info_details']
            
            formatted_errors.append(formatted_error)
        
        return formatted_errors if formatted_errors else None, page_url, verified_data
    else:
        logger_debug += (f"\nWeb page verification failed for: {reference.get('title', 'Untitled')}")
        # Return web page verification errors
        formatted_errors = []
        for error in errors:
            formatted_error = {}
            if 'error_type' in error:
                formatted_error['error_type'] = error['error_type']
                formatted_error['error_details'] = error['error_details']
            formatted_errors.append(formatted_error)
        return formatted_errors if formatted_errors else [{"error_type": "unverified", "error_details": "Web page could not be verified"}], page_url, None

def verify_raw_url_reference(reference):
    """
    Verify a raw URL from an unverified reference - can return verified data if appropriate
    
    Args:
        reference: The reference to verify (already determined to be unverified by paper validators)
        
    Returns:
        Tuple of (verified_data, errors, url) where:
        - verified_data: Dict with verified data if URL should be considered verified, None otherwise
        - errors: List of error dictionaries
        - url: The URL that was checked
    """
    global logger_debug
    global logger_warning
    global logger_info
    global logger_error
    logger_debug += (f"\nChecking raw URL for unverified reference: {reference.get('title', 'Untitled')}")
    
    # Extract URL from reference
    web_url = reference.get('url', '').strip()
    if not web_url:
        return None, [{"error_type": "unverified", "error_details": "Reference could not be verified"}], None
    
    # First try PDF paper checker if URL appears to be a PDF
    from refchecker.checkers.pdf_paper_checker import PDFPaperChecker
    pdf_checker = PDFPaperChecker()
    
    if pdf_checker.can_check_reference(reference):
        logger_debug += (f"\nURL appears to be PDF, trying PDF verification: {web_url}")
        try:
            verified_data, errors, url = pdf_checker.verify_reference(reference)
            if verified_data:
                logger_debug += (f"\nPDF verification successful for: {reference.get('title', 'Untitled')}")
                return verified_data, errors, url
            else:
                logger_debug += (f"\nPDF verification failed, falling back to web page verification")
        except Exception as e:
            logger_error += (f"\nError in PDF verification: {e}")
            logger_debug += (f"\nPDF verification error, falling back to web page verification")
    
    # Fall back to web page checker
    from refchecker.checkers.pdf_paper_checker import PDFPaperChecker
    pdf_checker = PDFPaperChecker()
    
    if pdf_checker.can_check_reference(reference):
        logger_debug += (f"\nURL appears to be PDF, trying PDF verification: {web_url}")
        try:
            verified_data, errors, url = pdf_checker.verify_reference(reference)
            if verified_data:
                logger_debug += (f"\nPDF verification successful for: {reference.get('title', 'Untitled')}")
                return verified_data, errors, url
            else:
                logger_debug += (f"\nPDF verification failed, falling back to web page verification")
        except Exception as e:
            logger_error += (f"\nError in PDF verification: {e}")
            logger_debug += (f"\nPDF verification error, falling back to web page verification")
    
    # Fall back to web page checker
    from refchecker.checkers.webpage_checker import WebPageChecker
    webpage_checker = WebPageChecker()
    
    try:
        verified_data, errors, url = webpage_checker.verify_raw_url_for_unverified_reference(reference)
        logger_debug += (f"\nRaw URL verification result: verified_data={verified_data is not None}, errors={len(errors)}, url={url}")
        return verified_data, errors, url
    except Exception as e:
        logger_error += (f"\nError checking raw URL: {e}")
        return None, [{"error_type": "unverified", "error_details": "Reference could not be verified"}], web_url

def verify_reference_standard(source_paper, reference):
    """
    Verify if a reference is accurate using GitHub, Semantic Scholar, or other checkers
    
    Args:
        source_paper: The paper containing the reference
        reference: The reference to verify
        
    Returns:
        Tuple of (errors, url, verified_data) where:
        - errors: List of errors or None if no errors found
        - url: URL of the paper if found, None otherwise
        - verified_data: The verified paper data from the verification service, None if not found
    """
    global logger_debug
    global logger_warning
    global logger_info
    global logger_error
    logger_debug += (f"\nVerifying non-arXiv reference: {reference.get('title', 'Untitled')}\n")

    # Create an enhanced hybrid checker with multiple reliable APIs
    non_arxiv_checker = EnhancedHybridReferenceChecker(
        semantic_scholar_api_key=None,
        db_path=None,  # No local DB in this branch
        contact_email=None,  # Could be added as parameter
        enable_openalex=True,  # Enable OpenAlex as reliable fallback
        enable_crossref=True,   # Enable CrossRef for DOI verification
        debug_mode=False  # Pass debug mode for conditional logging
    )
    service_order = "Semantic Scholar API → OpenAlex → CrossRef"

    # First, check if this is a GitHub repository reference
    github_result = verify_github_reference(reference)
    if github_result:
        return github_result
    
    # Use the Semantic Scholar client to verify the reference
    verified_data, errors, paper_url = non_arxiv_checker.verify_reference(reference)
    
    logger_debug += (f"\nNon-arXiv verification result: verified_data={verified_data is not None}, errors={len(errors) if errors else 0}, paper_url={paper_url}\n")
    
    # ALWAYS check for ArXiv ID mismatch first, regardless of verification status
    arxiv_errors = check_independent_arxiv_id_mismatch(reference, verified_data)
    
    if not verified_data:
        logger_debug += (f"\nCould not verify non-arXiv reference: {reference.get('title', 'Untitled')}\n")
        logger_debug += (f"\nRaw text: {reference['raw_text']}\n")
        
        # If there's also an ArXiv ID mismatch, report both errors
        if arxiv_errors:
            logger_debug += ("\nArXiv ID mismatch detected with unverified paper\n")
            combined_errors = [
                {"error_type": "unverified", "error_details": "Reference could not be verified"},
                *arxiv_errors
            ]
            return combined_errors, paper_url, verified_data
        else:
            # Only unverified error
            return [{"error_type": "unverified", "error_details": "Reference could not be verified"}], paper_url, verified_data
    
    # verified_data exists, check if there's an ArXiv ID mismatch
    if arxiv_errors:
        logger_debug += ("\nArXiv ID mismatch detected with verified paper\n")
        
        # Check if the verified paper has any displayable URLs after ArXiv ID filtering
        has_displayable_verified_url = False
        if verified_data:
            external_ids = verified_data.get('externalIds', {})
            # Check for non-ArXiv URLs that would be displayed
            if (external_ids.get('DOI') or 
                external_ids.get('CorpusId') or 
                (verified_data.get('url') and 'arxiv.org' not in verified_data.get('url', ''))):
                has_displayable_verified_url = True
        
        if has_displayable_verified_url:
            # Paper has other verification URLs to display, combine ArXiv ID error with original verification errors
            combined_errors = (errors or []) + arxiv_errors
            errors = combined_errors
            logger_debug += ("\nArXiv ID mismatch error for verified paper with displayable URLs\n")
        else:
            # Paper only has ArXiv URLs which will be filtered out, treat as unverified
            logger_debug += ("\nVerified paper has only ArXiv URLs - treating as unverified due to ArXiv ID mismatch\n")
            combined_errors = [
                {"error_type": "unverified", "error_details": "Reference could not be verified"},
                *arxiv_errors
            ]
            errors = combined_errors
        
        # Don't use the wrong paper's URL - return None to indicate no valid URL
        paper_url = None
        logger_debug += ("\nSetting paper_url to None due to ArXiv ID mismatch\n")
    elif errors:
        # Only keep other errors if there's no ArXiv ID mismatch
        logger_debug += ("\nNo ArXiv ID mismatch, keeping original verification errors\n")
        pass
    
    # If no errors were found by the Semantic Scholar client, we're done
    if not errors:
        return None, paper_url, verified_data
    
    # Convert the errors to our format
    formatted_errors = []
    
    logger_debug += (f"\nDEBUG: Converting {len(errors)} errors to formatted errors\n")
    for i, error in enumerate(errors):
        logger_debug += (f"\nDEBUG: Error {i}: {error}\n")
        formatted_error = {}
        
        # Handle error_type, warning_type, and info_type properly
        if 'error_type' in error:
            formatted_error['error_type'] = error['error_type']
            formatted_error['error_details'] = error['error_details']
        elif 'warning_type' in error:
            formatted_error['warning_type'] = error['warning_type']
            formatted_error['warning_details'] = error['warning_details']
        elif 'info_type' in error:
            formatted_error['info_type'] = error['info_type']
            formatted_error['info_details'] = error['info_details']
        
        # Add correct information based on error type
        if error.get('error_type') == 'author':
            formatted_error['ref_authors_correct'] = error.get('ref_authors_correct', '')
        elif error.get('error_type') == 'year' or error.get('warning_type') == 'year':
            formatted_error['ref_year_correct'] = error.get('ref_year_correct', '')
        elif error.get('error_type') == 'doi':
            from refchecker.utils.doi_utils import construct_doi_url
            formatted_error['ref_url_correct'] = construct_doi_url(error.get('ref_doi_correct', ''))
        
        formatted_errors.append(formatted_error)
    
    logger_debug += (f"\nDEBUG: Returning {len(formatted_errors)} formatted errors: {formatted_errors}\n")

    return formatted_errors if formatted_errors else None, paper_url, verified_data

def check_independent_arxiv_id_mismatch(reference, verified_data):
    """
    Check for ArXiv ID mismatch by comparing the cited paper's metadata 
    with what the ArXiv ID actually points to, independent of verification success.
    
    Args:
        reference: The reference dictionary
        verified_data: The verified paper data (may be None)
        
    Returns:
        List of errors if ArXiv ID points to wrong paper, empty list otherwise
    """
    global logger_debug
    global logger_warning
    global logger_info
    global logger_error
    # Extract ArXiv ID from URL or venue field
    ref_arxiv_id = None
    
    # Check for ArXiv ID in URL
    if reference.get('url') and 'arxiv.org/abs/' in reference['url']:
        ref_arxiv_id = extract_arxiv_id_from_url(reference['url'])
    
    # Check for ArXiv ID in venue field (e.g., "arXiv preprint arXiv:1234.5678")
    if not ref_arxiv_id and reference.get('venue'):
        venue_text = reference['venue']
        ref_arxiv_id = extract_arxiv_id_from_url(venue_text)
        
    # Check for ArXiv ID in disclaimer field (e.g., "arXiv preprint arXiv:1234.5678")
    if not ref_arxiv_id and reference.get('disclaimer'):
        disclaimer_text = reference['disclaimer']
        ref_arxiv_id = extract_arxiv_id_from_url(disclaimer_text)
    
    if not ref_arxiv_id:
        return []  # No ArXiv ID to check
    
    # Get what the ArXiv ID actually points to
    actual_arxiv_paper = get_paper_metadata(ref_arxiv_id)
    
    # If we have verified data, check for ArXiv ID mismatch
    if verified_data:
        # Check if verified data has an ArXiv ID
        correct_arxiv_id = None
        if verified_data.get('externalIds', {}).get('ArXiv'):
            correct_arxiv_id = verified_data['externalIds']['ArXiv']
        elif verified_data.get('arxivId'):
            correct_arxiv_id = verified_data['arxivId']
        
        if correct_arxiv_id and ref_arxiv_id != correct_arxiv_id:
            # Direct ArXiv ID mismatch - the paper was verified but has different ArXiv ID
            return [{
                'error_type': 'arxiv_id',
                'error_details': f"Incorrect ArXiv ID: ArXiv ID {ref_arxiv_id} should be {correct_arxiv_id}"
            }]
        elif correct_arxiv_id is None and not actual_arxiv_paper:
            # Verified paper has no ArXiv ID and cited ArXiv ID doesn't exist
            return [{
                'error_type': 'arxiv_id',
                'error_details': f"Invalid ArXiv ID: ArXiv ID {ref_arxiv_id} does not exist"
            }]
    
    # If the cited ArXiv ID doesn't exist and we have no verified data
    if not actual_arxiv_paper:
        logger_debug += (f"\nCould not fetch ArXiv paper metadata for ID: {ref_arxiv_id}")
        if not verified_data:
            # No verified data and invalid ArXiv ID - return error only if this is the primary verification method
            # For references with invalid ArXiv IDs, we should still allow title/author verification to proceed
            # Only return the invalid ArXiv ID error here if the reference appears to be purely ArXiv-based
            if not reference.get('title') and not reference.get('authors'):
                return [{
                    'error_type': 'arxiv_id',
                    'error_details': f"Invalid ArXiv ID: ArXiv ID {ref_arxiv_id} does not exist"
                }]
            else:
                # Let verification proceed, we'll report the invalid ArXiv ID later if verification succeeds
                return []
        else:
            # We have verified data but the cited ArXiv ID doesn't exist - report as error
            return [{
                'error_type': 'arxiv_id',
                'error_details': f"Invalid ArXiv ID: ArXiv ID {ref_arxiv_id} does not exist"
            }]
    
    # Get the expected paper metadata from the reference
    expected_title = reference.get('title', '').strip()
    expected_authors = reference.get('authors', [])
    
    if not expected_title:
        return []  # Can't check without expected title
    
    # Compare expected vs actual
    actual_title = actual_arxiv_paper.title.strip()
    actual_authors = getattr(actual_arxiv_paper, 'authors', [])
    
    # Calculate title similarity
    title_similarity = calculate_title_similarity(expected_title.lower(), actual_title.lower())
    
    logger_debug += (f"\nArXiv ID {ref_arxiv_id} independent check:\n")
    logger_debug += (f"\n  Expected title: '{expected_title}'\n")
    logger_debug += (f"\n  Actual ArXiv title: '{actual_title}'\n")
    logger_debug += (f"\n  Title similarity: {title_similarity:.3f}\n")
    # If titles are very different (less than 40% similarity), flag as ArXiv ID error
    if title_similarity < 0.4:
        # For ArXiv ID mismatch, we don't provide a correct URL here
        # The correct URL should be determined by finding the right paper by title/authors
        return [{
            'error_type': 'arxiv_id',
            'error_details': f"Incorrect ArXiv ID: ArXiv ID {ref_arxiv_id} points to '{actual_title}'"
        }]
    
    return []

def check_arxiv_id_mismatch(reference, verified_data, ref_arxiv_id):
    """
    Check if an ArXiv ID in the reference points to a different paper than the verified data.
    
    Args:
        reference: The reference with an ArXiv ID
        verified_data: The verified paper data from Semantic Scholar
        ref_arxiv_id: The ArXiv ID found in the reference
        
    Returns:
        List of errors if ArXiv ID points to wrong paper, empty list otherwise
    """
    global logger_debug
    global logger_warning
    global logger_info
    global logger_error
    if not verified_data or not ref_arxiv_id:
        return []
    
    # Get metadata for the ArXiv paper from the ID
    arxiv_paper = get_paper_metadata(ref_arxiv_id)
    if not arxiv_paper:
        logger_debug += (f"\nCould not fetch ArXiv paper metadata for ID: {ref_arxiv_id}")
        return []
    
    # Compare the ArXiv paper with the verified paper data
    # Check if they represent different papers by comparing titles and authors
    arxiv_title = arxiv_paper.title.strip()
    verified_title = verified_data.get('title', '').strip()
    
    # Calculate title similarity
    title_similarity = calculate_title_similarity(arxiv_title.lower(), verified_title.lower())
    
    logger_debug += (f"\nArXiv ID {ref_arxiv_id} title similarity: {title_similarity:.3f}")
    logger_debug += (f"\nArXiv paper title: '{arxiv_title}'")
    logger_debug += (f"\nVerified paper title: '{verified_title}'")
    
    # If titles are very different (less than 40% similarity), flag as ArXiv ID error
    if title_similarity < 0.4:
        # Try to find the correct ArXiv URL for the actual paper
        correct_arxiv_url = find_correct_arxiv_url(verified_data)
        correct_url = correct_arxiv_url if correct_arxiv_url else verified_data.get('url', '')
        
        return [{
            'error_type': 'arxiv_id',
            'error_details': f"ArXiv ID points to different paper: cited ArXiv ID {ref_arxiv_id} points to '{arxiv_title}' but reference is actually '{verified_title}'",
            'ref_url_correct': correct_url
        }]
    
    return []

def check_arxiv_url_mismatch(reference, verified_data):
    """
    Legacy function - now redirects to check_arxiv_id_mismatch
    
    Args:
        reference: The reference with an ArXiv URL
        verified_data: The verified paper data from Semantic Scholar
        
    Returns:
        List of errors if ArXiv URL points to wrong paper, empty list otherwise
    """
    if not verified_data or not reference.get('url'):
        return []
    
    # Extract ArXiv ID from the reference URL
    ref_arxiv_id = extract_arxiv_id_from_url(reference['url'])
    if not ref_arxiv_id:
        return []
        
    return check_arxiv_id_mismatch(reference, verified_data, ref_arxiv_id)

def find_correct_arxiv_url(verified_data):
    """
    Try to find the correct ArXiv URL for a paper based on verified data.
    
    Args:
        verified_data: The verified paper data from Semantic Scholar
        
    Returns:
        ArXiv URL string if found, None otherwise
    """
    if not verified_data:
        return None
    
    # Check if the verified paper has external IDs that include ArXiv
    external_ids = verified_data.get('externalIds', {})
    if external_ids and 'ArXiv' in external_ids:
        arxiv_id = external_ids['ArXiv']
        return f"https://arxiv.org/abs/{arxiv_id}"
    
    # Check if any of the URLs in the paper data point to ArXiv
    paper_url = verified_data.get('url', '')
    if paper_url and 'arxiv.org' in paper_url:
        return paper_url
    
    # Check openAccessPdf for ArXiv links
    open_access_pdf = verified_data.get('openAccessPdf')
    if open_access_pdf and open_access_pdf.get('url'):
        pdf_url = open_access_pdf['url']
        if 'arxiv.org' in pdf_url:
            # Convert PDF URL to abs URL
            if '/pdf/' in pdf_url:
                return pdf_url.replace('/pdf/', '/abs/').replace('.pdf', '')
            return pdf_url
    
    return None
   
def normalize_text(text):
    """
    Normalize text by removing diacritical marks and special characters.
    This is a wrapper method for backward compatibility with tests.
    """
    return common_normalize_text(text)

def is_valid_doi(doi):
    """
    Check if a DOI is well-formed (basic check: starts with '10.' and has at least one slash and more than 6 chars)
    """
    if not doi or not isinstance(doi, str):
        return False
    doi = doi.strip()
    # Must start with '10.' and contain at least one '/'
    if not doi.startswith('10.') or '/' not in doi:
        return False
    if len(doi) < 7:
        return False
    # Optionally, check for forbidden trailing chars
    if doi in ('10.', '10'):
        return False
    return True

def compare_authors(authors1, authors2):
    """
    Compare authors using the text_utils compare_authors function.
    
    Args:
        authors1: First list of authors
        authors2: Second list of authors
        
    Returns:
        Tuple of (match_result, error_message)
    """
    return compare_authors(authors1, authors2)

def _parse_standard_acm_natbib_references(bibliography_text):
    """
    Parse references using regex for standard ACM/natbib format (both ACM Reference Format and simple natbib)
    """
    global logger_debug
    global logger_warning
    references = []
    
    # Detect which format we're dealing with
    is_acm_format = re.search(r'\\bibfield\{author\}\{.*?\\bibinfo\{person\}', bibliography_text)
    
    # Pattern to extract \bibitem entries with the complete content
    bibitem_pattern = r'\\bibitem\[([^\]]*)\]\s*%?\s*\n?\s*\{([^}]+)\}\s*(.*?)(?=\\bibitem|\\end\{thebibliography\}|$)'
    
    matches = re.finditer(bibitem_pattern, bibliography_text, re.DOTALL | re.IGNORECASE)
    
    for match in matches:
        label = match.group(1)
        key = match.group(2)
        content = match.group(3).strip()
        
        ref = {
            'raw_text': f"\\bibitem[{label}]{{{key}}}\n{content}",
            'title': '',
            'authors': [],
            'year': None,
            'journal': '',
            'venue': '',
            'url': '',
            'doi': '',
            'arxiv_id': '',
            'bibitem_key': key,
            'bibitem_label': label
        }
        
        if is_acm_format:
            # Parse ACM Reference Format
            _parse_acm_reference_format(ref, content)
        else:
            # Parse simple natbib format
            _parse_simple_natbib_format(ref, content, label)
        
        # Only add if we have essential information
        if ref['title'] or ref['authors']:
            references.append(ref)
    
    format_name = "ACM Reference Format" if is_acm_format else "simple natbib format"
    logger_debug += (f"\nParsed {len(references)} references using {format_name}")
    return references

def _parse_acm_reference_format(ref, content):
    """Parse ACM Reference Format with \bibfield and \bibinfo commands"""
    global logger_debug
    global logger_warning
    # Extract year from \bibinfo{year}{YYYY}
    year_match = re.search(r'\\bibinfo\{year\}\{(\d{4})\}', content)
    if year_match:
        ref['year'] = int(year_match.group(1))
    
    # Extract authors from \bibfield{author}{\bibinfo{person}{Name1}, \bibinfo{person}{Name2}, ...}
    author_field_match = re.search(r'\\bibfield\{author\}\{(.*?)\}(?:\s*\\bibinfo\{year\}|\s*\\newblock|$)', content, re.DOTALL)
    if author_field_match:
        author_content = author_field_match.group(1)
        # Find all \bibinfo{person}{Name} entries using balanced brace extraction
        from refchecker.utils.text_utils import extract_bibinfo_person_content
        person_matches = extract_bibinfo_person_content(author_content)
        if person_matches:
            authors = []
            for person in person_matches:
                # Clean the author name and remove any remaining LaTeX commands
                clean_name = strip_latex_commands(person).strip()
                # Remove leading "and" that might be left over
                clean_name = re.sub(r'^and\s+', '', clean_name)
                if clean_name and clean_name not in ['and', '{and}']:
                    authors.append(clean_name)
            ref['authors'] = authors
    
    # Import balanced brace extraction function
    from refchecker.utils.text_utils import extract_bibinfo_field_content
    
    # Extract title from \bibinfo{title}{Title} using balanced brace extraction
    title_content = extract_bibinfo_field_content(content, 'title')
    if title_content:
        title = strip_latex_commands(title_content).strip()
        ref['title'] = title
    
    # Extract venue/journal from various fields using balanced brace extraction
    venue_field_types = ['booktitle', 'journal', 'series', 'note']
    
    for field_type in venue_field_types:
        venue_content = extract_bibinfo_field_content(content, field_type)
        if venue_content:
            venue = strip_latex_commands(venue_content).strip()
            if venue:
                ref['venue'] = venue
                ref['journal'] = venue  # For compatibility
                break
    
    # Extract DOI using balanced brace extraction
    doi_content = extract_bibinfo_field_content(content, 'doi')
    if doi_content:
        ref['doi'] = doi_content.strip()
    
    # Extract ArXiv ID from \showeprint[arxiv]{ID}
    arxiv_match = re.search(r'\\showeprint\[arxiv\]\{([^}]+)\}', content)
    if arxiv_match:
        ref['arxiv_id'] = arxiv_match.group(1).strip()
    
    # Extract URL
    url_match = re.search(r'\\bibinfo\{url\}\{([^}]+)\}', content)
    if url_match:
        ref['url'] = url_match.group(1).strip()

def _parse_simple_natbib_format(ref, content, label):
    """Parse simple natbib format with plain text content"""
    global logger_debug
    global logger_warning
    # Extract year from label like "Author(2023)" or from content
    year_match = re.search(r'\((\d{4})\)', label)
    if year_match:
        ref['year'] = int(year_match.group(1))
    else:
        # Try to find year in content
        year_match = re.search(r'\b(19|20)\d{2}\b', content)
        if year_match:
            ref['year'] = int(year_match.group())
    
    # Split content by \newblock to get different parts
    parts = re.split(r'\\newblock\s*', content)
    
    if len(parts) >= 1:
        # First part (before first \newblock) is usually authors
        author_part = parts[0].strip()
        if author_part:
            # Clean author part and extract authors
            author_part_clean = strip_latex_commands(author_part).strip()
            if author_part_clean and not author_part_clean.startswith('\\'):
                # Parse author names using the robust author parsing function
                from refchecker.utils.text_utils import parse_authors_with_initials
                author_names = parse_authors_with_initials(author_part_clean)
                
                # Clean up author names
                authors = []
                for name in author_names:
                    name = name.strip()
                    # Remove leading "and" from author names
                    name = re.sub(r'^and\s+', '', name)
                    if name and len(name) > 2 and name not in ['et~al', 'et al', 'et~al.']:
                        # Remove trailing dots
                        name = name.rstrip('.')
                        authors.append(name)
                if authors:
                    ref['authors'] = authors
    
    if len(parts) >= 2:
        # Second part is usually title
        title_part = parts[1].strip()
        if title_part:
            title_clean = strip_latex_commands(title_part).strip()
            # Remove trailing periods and clean up
            title_clean = title_clean.rstrip('.,')
            if title_clean:
                ref['title'] = title_clean
    
    if len(parts) >= 3:
        # Third part is usually venue/journal
        venue_part = parts[2].strip()
        if venue_part:
            venue_clean = strip_latex_commands(venue_part).strip()
            # Remove trailing periods and clean up
            venue_clean = venue_clean.rstrip('.,')
            if venue_clean:
                ref['venue'] = venue_clean
                ref['journal'] = venue_clean  # For compatibility
    
    # Extract DOI from \doi{...} commands
    doi_match = re.search(r'\\doi\{([^}]+)\}', content)
    if doi_match:
        ref['doi'] = doi_match.group(1).strip()
    
    # Extract URL from \url{...} commands
    url_match = re.search(r'\\url\{([^}]+)\}', content)
    if url_match:
        ref['url'] = url_match.group(1).strip()

def _parse_references_regex(bibliography_text):
    global logger_debug
    global logger_warning
    """
    Parse references using regex-based approach (original implementation)
    """
    used_regex_extraction = True
    
    # Check if this is BibTeX format first
    from refchecker.utils.bibtex_parser import detect_bibtex_format
    if detect_bibtex_format(bibliography_text):
        logger_debug += ("\nDetected BibTeX format, using BibTeX-specific parsing")
        # BibTeX parsing is robust, so we don't set used_unreliable_extraction
        return _parse_bibtex_references(bibliography_text)
    
    # Check if this is biblatex format
    from refchecker.utils.biblatex_parser import detect_biblatex_format  
    if detect_biblatex_format(bibliography_text):
        logger_debug += ("\nDetected biblatex format, using biblatex-specific parsing")
        # biblatex parsing is also robust, so we don't set used_unreliable_extraction
        biblatex_refs = _parse_biblatex_references(bibliography_text)
        
        # If biblatex parsing returned empty results (due to quality validation),
        # we'll continue with the unreliable fallback regex parsing
        if not biblatex_refs:
            logger_debug += ("\nBiblatex parser returned no results due to quality validation, falling back to regex parsing")
            st.write(f"⚠️  Biblatex parser found no valid references (failed quality validation) - falling back to regex parsing")
        else:
            return biblatex_refs
    
    # If we reach here, we're using the unreliable fallback regex parsing
    used_unreliable_extraction = True
    
    # --- IMPROVED SPLITTING: handle concatenated references like [3]... [4]... ---
    # First, normalize the bibliography text to handle multi-line references
    # This fixes the issue where years appear as separate lines
    normalized_bib = re.sub(r'\s+', ' ', bibliography_text).strip()
    
    # Ensure proper spacing after reference numbers - more comprehensive fix
    normalized_bib = re.sub(r'(\[\d+\])([A-Za-z])', r'\1 \2', normalized_bib)
    # Also handle cases where numbers directly follow reference numbers
    normalized_bib = re.sub(r'(\[\d+\])(\d)', r'\1 \2', normalized_bib)
    
    
    # Handle the case where the last reference might be incomplete
    # Check if the text ends with a reference number followed by content
    if re.search(r'\[\d+\][^[]*$', normalized_bib):
        # The last reference is incomplete, try to find a better ending
        # Look for the last complete sentence or period, but avoid truncating file extensions
        last_period = normalized_bib.rfind('.')
        if last_period > 0:
            # Check if this period is part of a file extension
            text_after_period = normalized_bib[last_period+1:last_period+5]  # Check next 4 chars
            if not re.match(r'^[a-zA-Z]{2,4}$', text_after_period):
                # Find the last reference number before this period
                last_ref_match = re.search(r'\[\d+\][^[]*?\.', normalized_bib[:last_period+1])
                if last_ref_match:
                    # Truncate at the last complete reference
                    normalized_bib = normalized_bib[:last_period+1]
    
    numbered_ref_pattern = r'(\[\d+\])'
    numbered_refs = re.split(numbered_ref_pattern, normalized_bib)
    references = []
    
    # Only process as numbered references if we actually have numbered patterns in the text
    has_numbered_refs = bool(re.search(r'\[\d+\]', normalized_bib))
    
    if len(numbered_refs) > 1 and has_numbered_refs:
        # Reconstruct references, as split removes the delimiter
        temp = []
        for part in numbered_refs:
            if re.match(r'^\[\d+\]$', part):
                if temp:
                    joined_ref = ''.join(temp).strip()
                    references.append(joined_ref)
                    temp = []
                temp.append(part)
            else:
                temp.append(part)
        if temp:
            joined_ref = ''.join(temp).strip()
            references.append(joined_ref)
        # Remove empty or very short entries, but be less aggressive to preserve order
        references = [r for r in references if len(r.strip()) > 10 and not re.match(r'^\[\d+\]$', r.strip())]
        # Ensure the last chunk is included if not already
        if numbered_refs[-1].strip() and not any(numbered_refs[-1].strip() in r for r in references):
            references.append(numbered_refs[-1].strip())
        # Additional defense: filter out numbered items that are clearly not references
        validated_references = []
        for ref in references:
            if _is_likely_reference(ref):
                validated_references.append(ref)
            else:
                logger_debug += (f"\nFiltered out non-reference item: {ref[:100]}...")
        
        logger_debug += (f"\nBefore validation: {len(references)} references")
        logger_debug += (f"\nAfter validation: {len(validated_references)} references")
        references = validated_references
        logger_debug += (f"\nFound {len(references)} numbered references")
    else:
        # Fallback to original logic if not numbered
        # Try different splitting strategies
        splitting_strategies = [
            (r'\[\d+\]', lambda x: [r.strip() for r in x if r.strip()]),
            (r'\n\s*\d+\.\s+', lambda x: x[1:] if not x[0].strip() else x),
            (r'\n\s*\([A-Za-z]+(?:\s+et\s+al\.)?(?:,\s+\d{4})\)\s+', lambda x: x),
            (r'\n\s*\n', lambda x: x),
        ]
        for pattern, processor in splitting_strategies:
            split_refs = re.split(pattern, normalized_bib)
            if len(split_refs) > 1:
                references = processor(split_refs)
                logger_debug += (f"\nSplit bibliography using pattern: {pattern}")
                logger_debug += (f"\nFound {len(references)} potential references")
                break
        
        # If no splitting strategy worked, try author-year format detection
        if not references:
            logger_debug += ("\nAttempting author-year format detection...")
            
            # For author-year format, use original bibliography_text (with newlines intact)
            # Enhanced pattern to detect author-year format
            # Look for year endings followed by new reference starts
            # Pattern: year (like 2024.) followed by newline and capital letter start
            year_boundary_pattern = r'(?<=\d{4}\.)\n(?=[A-Z])'
            split_refs = re.split(year_boundary_pattern, bibliography_text.strip())
            logger_debug += (f"\nYear boundary pattern split resulted in {len(split_refs)} parts")
            
            if len(split_refs) > 1:
                references = [ref.strip() for ref in split_refs if ref.strip() and len(ref.strip()) > 20]
                logger_debug += (f"\nFound {len(references)} potential references with year boundary pattern")
            else:
                # Fallback: simpler pattern - split on newlines followed by any capital letter
                simple_pattern = r'\n(?=[A-Z])'
                split_refs = re.split(simple_pattern, bibliography_text.strip())
                logger_debug += (f"\nSimple pattern split resulted in {len(split_refs)} parts")
                
                if len(split_refs) > 1:
                    references = [ref.strip() for ref in split_refs if ref.strip() and len(ref.strip()) > 20]
                    logger_debug += (f"\nFound {len(references)} potential references with simple pattern")
    if not references:
        references = [line.strip() for line in normalized_bib.split('\n') if line.strip()]
        logger_debug += (f"\nUsing line-by-line splitting, found {len(references)} potential references")
    references = [ref.strip() for ref in references if ref.strip()]

    # --- POST-PROCESSING: fix malformed DOIs/URLs and edge cases ---
    def clean_url(url):
        if not url:
            return url
        url = url.strip()
        # Remove trailing punctuation, but preserve file extensions
        # Only remove trailing punctuation if it's not part of a file extension
        if not re.search(r'\.[a-zA-Z]{2,4}$', url):
            url = re.sub(r'[\.,;:]+$', '', url)
        # Fix common malformed DOI/URL
        if url.startswith('https://doi') and not re.match(r'https://doi.org/\S+', url):
            url = ''
        if url == 'https://doi' or url == 'https://doi.org/10.':
            url = ''
        return url
    def clean_doi(doi):
        if not doi or doi == '10.':
            return None
        # Strip URL fragments (everything after #) from DOI
        doi = doi.split('#')[0]
        # Clean DOI: remove asterisk contamination (e.g., "10.1088/123*http://..." -> "10.1088/123")
        if '*' in doi:
            doi = doi.split('*')[0]
        return doi

    arxiv_refs = []
    non_arxiv_refs = []
    other_refs = []
    arxiv_patterns = [
        r'arxiv\.org/[^\s,\)]+',
        r'arxiv\.org/pdf/\d+\.\d+(?:v\d+)?',
        r'arxiv\.org/abs/\d+\.\d+(?:v\d+)?',
        r'arxiv:\s*(\d+\.\d+(?:v\d+)?)',
        r'arXiv preprint arXiv:(\d+\.\d+(?:v\d+)?)',
        r'CoRR\s*,?\s*abs[:/](\d+\.\d+(?:v\d+)?)',  # Fixed to handle "CoRR , abs/1409.0473" format
    ]
    doi_patterns = [
        r'doi\.org/([^\s,\)]+)',
        r'doi:([^\s,\)]+)',
        r'DOI:([^\s,\)]+)',
    ]
    url_patterns = [
        r'https?://(?!arxiv\.org)[^\s,\)]+',
    ]
    for i, ref in enumerate(references):
        logger_debug += (f"\nProcessing reference {i+1}: {ref[:100]}...")
        arxiv_id = None
        arxiv_url = None
        for pattern in arxiv_patterns:
            arxiv_match = re.search(pattern, ref, re.IGNORECASE)
            if arxiv_match:
                if 'arxiv.org' in arxiv_match.group(0).lower():
                    arxiv_url = arxiv_match.group(0)
                    if not arxiv_url.startswith('http'):
                        arxiv_url = 'https://' + arxiv_url
                else:
                    try:
                        arxiv_id = arxiv_match.group(1)
                        arxiv_url = f"https://arxiv.org/abs/{arxiv_id}"
                    except IndexError:
                        arxiv_url = f"https://arxiv.org/abs/{arxiv_match.group(0)}"
                break
        if arxiv_url:
            # ... existing arxiv extraction logic ...
            ref_without_arxiv_id = ref
            if arxiv_url:
                arxiv_id_match = re.search(r'\b\d{4}\.\d{4,5}(?:v\d+)?\b', ref)
                if arxiv_id_match:
                    ref_without_arxiv_id = ref.replace(arxiv_id_match.group(0), '')
            year = None
            end_year_match = re.search(r',\s+((19|20)\d{2})\s*\.?\s*$', ref_without_arxiv_id)
            if end_year_match:
                year = int(end_year_match.group(1))
            else:
                year_patterns = [
                    r'(?:preprint|abs/[^,]+),?\s+((19|20)\d{2})',
                    r'(?:CoRR|arXiv),?\s+[^,]*,?\s+((19|20)\d{2})',
                    r'(?:In|Proceedings)[^,]*,?\s+((19|20)\d{2})',
                ]
                for pattern in year_patterns:
                    pattern_match = re.search(pattern, ref_without_arxiv_id)
                    if pattern_match:
                        year = int(pattern_match.group(1))
                        break
                if year is None:
                    all_years = re.findall(r'\b((19|20)\d{2})\b', ref_without_arxiv_id)
                    if all_years:
                        valid_years = []
                        for potential_year, _ in all_years:
                            page_pattern = rf'\d+\([^)]*\):\d*{potential_year}'
                            if not re.search(page_pattern, ref_without_arxiv_id):
                                valid_years.append(int(potential_year))
                        if valid_years:
                            year = valid_years[-1]
            if year is None:
                year_match = re.search(r'\b(19|20)\d{2}\b', ref)
                year = int(year_match.group(0)) if year_match else None
            if year is None and arxiv_url:
                arxiv_id_match = re.search(r'\b(\d{4})\.\d{4,5}(?:v\d+)?\b', ref)
                if arxiv_id_match:
                    arxiv_year_month = arxiv_id_match.group(1)
                    if len(arxiv_year_month) == 4 and arxiv_year_month.startswith(('07', '08', '09')):
                        yy = int(arxiv_year_month[:2])
                        if yy >= 7:
                            year = 1992 + yy
                    elif len(arxiv_year_month) == 4 and arxiv_year_month.startswith(tuple(str(x).zfill(2) for x in range(10, 25))):
                        yy = int(arxiv_year_month[:2])
                        year = 2000 + yy
            # Additional year extraction for legal cases and other formats
            if year is None:
                # Look for year right after reference number like "[1]1976."
                legal_year_match = re.search(r'^\[\d+\](\d{4})\.', ref)
                if legal_year_match:
                    year = int(legal_year_match.group(1))
                else:
                    # Look for year at the beginning after any reference number
                    year_start_match = re.search(r'^.*?(\d{4})\.', ref)
                    if year_start_match:
                        potential_year = int(year_start_match.group(1))
                        # Validate that it's a reasonable year
                        if 1900 <= potential_year <= 2030:
                            year = potential_year
            extracted_data = extract_authors_title_from_academic_format(ref)
            if extracted_data:
                authors, title = extracted_data
            else:
                authors, title = extract_authors_title_fallback(ref)
            title = clean_title(title) if title else ""
            if not authors and arxiv_url:
                authors = ["Unknown Author"]
            final_authors = []
            for author in authors:
                if isinstance(author, dict) and author.get('is_url_reference', False):
                    final_authors = ["URL Reference"]
                    break
                else:
                    final_authors.append(author)
            if not final_authors:
                final_authors = ["Unknown Author"]
            structured_ref = {
                'url': clean_url(arxiv_url),
                'year': year if year else 0,
                'authors': final_authors,
                'title': title,
                'raw_text': ref,
                'type': 'arxiv'
            }
            logger_debug += (f"\nExtracted arXiv reference {i+1}: {structured_ref['title']}")
            arxiv_refs.append(structured_ref)
        else:
            doi = None
            url = None
            for pattern in doi_patterns:
                doi_match = re.search(pattern, ref, re.IGNORECASE)
                if doi_match:
                    doi = clean_doi(doi_match.group(1))
                    if doi:
                        from refchecker.utils.doi_utils import construct_doi_url
                        url = construct_doi_url(doi)
                    else:
                        url = ''
                    break
            if not url:
                for pattern in url_patterns:
                    url_match = re.search(pattern, ref)
                    if url_match:
                        raw_url = url_match.group(0)
                        url = clean_url(raw_url)
                        break
                
                # Handle multi-line URLs specifically
                if not url and re.search(r'https?://', ref):
                    # Try to reconstruct multi-line URLs
                    url_start_match = re.search(r'https?://[^\s\n]*', ref)
                    if url_start_match:
                        url_start = url_start_match.group(0)
                        # Look for continuation on the next line(s)
                        remaining_ref = ref[url_start_match.end():].strip()
                        # Remove leading whitespace and reference numbers
                        remaining_ref = re.sub(r'^\s*\[\d+\]?\s*', '', remaining_ref)
                        
                        # Check if the remaining part looks like a URL continuation
                        # (alphanumeric characters, hyphens, slashes, etc.)
                        if re.match(r'^[a-zA-Z0-9\-_/.=?&%\n\s]+\s*\.?\s*$', remaining_ref):
                            # Combine the URL parts, removing newlines and spaces
                            # Don't strip dots from URLs as they might be file extensions
                            url_continuation = re.sub(r'\s+', '', remaining_ref.strip())
                            # Only remove trailing dot if it's not part of a file extension
                            if url_continuation.endswith('.') and not re.search(r'\.[a-zA-Z]{2,4}\.?$', url_continuation):
                                url_continuation = url_continuation.rstrip('.')
                            url = url_start + url_continuation
            if url or doi:
                logger_debug += (f"\nFound non-arXiv reference {i+1}: {url or doi}")
                year = None
                end_year_match = re.search(r',\s+((19|20)\d{2})\s*\.?\s*$', ref)
                if end_year_match:
                    year = int(end_year_match.group(1))
                else:
                    year_patterns = [
                        r'(?:In|Proceedings)[^,]*,?\s+((19|20)\d{2})',
                        r'(?:Journal|IEEE|ACM)[^,]*,?\s+((19|20)\d{2})',
                        r'(?:CoRR|abs/)[^,]*,?\s+((19|20)\d{2})',
                    ]
                    for pattern in year_patterns:
                        pattern_match = re.search(pattern, ref)
                        if pattern_match:
                            year = int(pattern_match.group(1))
                            break
                    if year is None:
                        all_years = re.findall(r'\b((19|20)\d{2})\b', ref)
                        if all_years:
                            valid_years = []
                            for potential_year, _ in all_years:
                                page_pattern = rf'\d+\([^)]*\):\d*{potential_year}'
                                if not re.search(page_pattern, ref):
                                    valid_years.append(int(potential_year))
                            if valid_years:
                                year = valid_years[-1]
                extracted_data = extract_authors_title_from_academic_format(ref)
                if extracted_data:
                    authors, title = extracted_data
                else:
                    authors, title = extract_authors_title_fallback(ref)
                title = clean_title(title) if title else ""
                is_url_reference = False
                for author in authors:
                    if isinstance(author, dict) and author.get('is_url_reference', False):
                        is_url_reference = True
                        break
                if is_url_reference:
                    authors = ["URL Reference"]
                    # For URL references, use the cleaned URL as title if title looks like URL fragment
                    if title and (len(title) < 10 or re.match(r'^[a-zA-Z0-9\-_/.=?&%\s]+$', title)):
                        title = clean_url(url) if url else title
                elif not authors:
                    authors = ["Unknown Author"]
                structured_ref = {
                    'url': clean_url(url),
                    'doi': clean_doi(doi),
                    'year': year if year else 0,
                    'authors': authors,
                    'title': title,
                    'raw_text': ref,
                    'type': 'non-arxiv'
                }
                logger_debug += (f"\nExtracted non-arXiv reference: {structured_ref}")
                non_arxiv_refs.append(structured_ref)
            else:
                extracted_data = extract_authors_title_from_academic_format(ref)
                if extracted_data:
                    authors, title = extracted_data
                else:
                    authors, title = extract_authors_title_fallback(ref)
                title = clean_title(title) if title else ""
                year = None
                end_year_match = re.search(r',\s+((19|20)\d{2})\s*\.?\s*$', ref)
                if end_year_match:
                    year = int(end_year_match.group(1))
                else:
                    year_patterns = [
                        r'(?:In|Proceedings)[^,]*,?\s+((19|20)\d{2})',
                        r'(?:Journal|IEEE|ACM)[^,]*,?\s+((19|20)\d{2})',
                        r'(?:CoRR|abs/)[^,]*,?\s+((19|20)\d{2})',
                    ]
                    for pattern in year_patterns:
                        pattern_match = re.search(pattern, ref)
                        if pattern_match:
                            year = int(pattern_match.group(1))
                            break
                    if year is None:
                        all_years = re.findall(r'\b((19|20)\d{2})\b', ref)
                        if all_years:
                            valid_years = []
                            for potential_year, _ in all_years:
                                page_pattern = rf'\d+\([^)]*\):\d*{potential_year}'
                                if not re.search(page_pattern, ref):
                                    valid_years.append(int(potential_year))
                            if valid_years:
                                year = valid_years[-1]
                is_url_reference = False
                for author in authors:
                    if isinstance(author, dict) and author.get('is_url_reference', False):
                        is_url_reference = True
                        break
                if is_url_reference:
                    authors = ["URL Reference"]
                    # For URL references in other category, keep original title since no URL available
                elif not authors:
                    authors = ["Unknown Author"]
                structured_ref = {
                    'url': "",
                    'doi': None,
                    'year': year if year else 0,
                    'authors': authors,
                    'title': title,
                    'raw_text': ref,
                    'type': 'other'
                }
                logger_debug += (f"\nExtracted other reference {i+1}: {structured_ref['title']}")
                other_refs.append(structured_ref)
    logger_debug += (f"\nExtracted {len(arxiv_refs)} structured references with arxiv links")
    logger_debug += (f"\nExtracted {len(non_arxiv_refs)} structured references without arxiv links")
    logger_debug += (f"\nExtracted {len(other_refs)} structured references without URLs or DOIs")
    all_refs = arxiv_refs + non_arxiv_refs + other_refs
    return all_refs

def _parse_reference_segments(ref_str):
    global logger_debug
    global logger_warning
    """Parse reference into segments, normalizing for comparison"""
    # Strip trailing # and normalize
    clean_ref = ref_str.strip().rstrip('#').strip()
    
    # Split by # to get segments
    segments = [seg.strip().lower() for seg in clean_ref.split('#') if seg.strip()]
    
    return {
        'author': segments[0] if len(segments) > 0 else '',
        'title': segments[1] if len(segments) > 1 else '',
        'venue': segments[2] if len(segments) > 2 else '',
        'year': segments[3] if len(segments) > 3 else '',
        'raw_segments': segments
    }

def _parse_bibtex_references(bibliography_text):
    global logger_debug
    global logger_warning
    """
    Parse BibTeX formatted references like @inproceedings{...}, @article{...}, etc.
    
    Args:
        bibliography_text: String containing BibTeX entries
        
    Returns:
        List of structured reference dictionaries
    """
    # Use the dedicated BibTeX parser
    from refchecker.utils.bibtex_parser import parse_bibtex_references
    
    # Extract references using the BibTeX parser
    references = parse_bibtex_references(bibliography_text)
    
    logger_debug += (f"\nExtracted {len(references)} BibTeX references using dedicated parser")
    return references

def _parse_biblatex_references(bibliography_text):
    global logger_debug
    global logger_warning
    """
    Parse biblatex formatted references like [1] Author. "Title". In: Venue. Year.
    
    Args:
        bibliography_text: String containing biblatex .bbl entries
        
    Returns:
        List of structured reference dictionaries
    """
    # Use the dedicated biblatex parser
    from refchecker.utils.biblatex_parser import parse_biblatex_references
    
    # Extract references using the biblatex parser
    references = parse_biblatex_references(bibliography_text)
    
    logger_debug += (f"\nExtracted {len(references)} biblatex references using dedicated parser")
    return references

def _process_llm_extracted_references(references):
    global logger_debug
    global logger_warning
    """
    Process references extracted by LLM with simplified formatting assumptions
    """
    # Remove duplicates from LLM-extracted references using enhanced segment-based matching
    unique_references = _deduplicate_references_with_segment_matching(references)
    
    logger_debug += (f"\nDeduplicated {len(references)} references to {len(unique_references)} unique references")
    
    processed_refs = []
    
    for ref in unique_references:
        # Handle case where ref might be a dict or other object
        if isinstance(ref, dict):
            # Convert dict to string representation or extract relevant field
            ref_text = str(ref)
        elif isinstance(ref, str):
            ref_text = ref
        else:
            # Skip non-string, non-dict objects
            continue
            
        if not ref_text or len(ref_text.strip()) < 10:
            continue
            
        # Use LLM-specific structured reference creation
        structured_ref = _create_structured_llm_references(ref_text)
        if structured_ref:
            processed_refs.append(structured_ref)
    
    return processed_refs

def _deduplicate_references_with_segment_matching(references):
    global logger_debug
    global logger_warning
    """
    Enhanced deduplication using segment-based matching to handle chunk boundary issues.
    
    Treats references as duplicates if:
    1. Title segments match exactly (case-insensitive)
    2. Either author segments match exactly OR one author segment is a substring of the other
       (handles cases where chunking cuts through author lists)
    """
    unique_references = []
    seen_segments = []
    
    for ref in references:
        # Convert to string for comparison
        ref_str = str(ref) if not isinstance(ref, str) else ref
        
        # Skip very short references
        if not ref_str or len(ref_str.strip()) < 10:
            continue
            
        # Parse segments from reference (format: authors # title # venue # year)
        segments = _parse_reference_segments(ref_str)
        
        # Check if this reference is a duplicate of any previously seen reference
        is_duplicate = False
        for seen_ref, seen_segments_data in seen_segments:
            if _are_references_duplicates(segments, seen_segments_data):
                logger_debug += (f"\nDuplicate detected: '{ref_str[:80]}...' matches '{seen_ref[:80]}...'")
                is_duplicate = True
                break
        
        if not is_duplicate:
            unique_references.append(ref)
            seen_segments.append((ref_str, segments))
    
    return unique_references

def _deduplicate_bibliography_entries(bibliography):
    """
    Deduplicate bibliography entries using title and author comparison.
    
    This works with structured reference dictionaries from BibTeX/LaTeX parsing,
    as opposed to _deduplicate_references_with_segment_matching which works with raw text.
    
    Args:
        bibliography: List of reference dictionaries with 'title', 'authors', etc.
        
    Returns:
        List of unique reference dictionaries
    """
    global logger_debug
    global logger_warning
    if len(bibliography) <= 1:
        return bibliography
        
    unique_refs = []
    seen_titles = set()
    
    for ref in bibliography:
        title = ref.get('title', '').strip()
        if not title:
            # Keep references without titles (they can't be deduplicated)
            unique_refs.append(ref)
            continue
            
        # Normalize title for comparison (case-insensitive, basic cleanup)
        normalized_title = title.lower().strip()
        
        # Check if we've seen this title before (case-insensitive)
        if normalized_title in seen_titles:
            logger_debug += (f"\nSkipping duplicate reference: '{title}'")
        else:
            unique_refs.append(ref)
            seen_titles.add(normalized_title)
            
    return unique_refs

def _are_references_duplicates(seg1, seg2):
    """
    Check if two reference segments represent the same reference.
    
    Enhanced logic:
    - If titles match exactly, they are considered duplicates (primary criterion)
    - Special handling for author chunk boundary issues by checking substring/overlap
    """
    global logger_debug
    global logger_warning
    # Title must match exactly (case-insensitive) - primary criterion
    if not seg1['title'] or not seg2['title']:
        # If either has no title, can't reliably determine if duplicate
        return False
        
    # If titles match exactly (case-insensitive), consider them duplicates
    # This handles the case where the same paper appears multiple times with different capitalization
    if seg1['title'].lower() == seg2['title'].lower():
        return True
        
    # Special case: Check if one title is an arXiv identifier and the other is a real title
    # from the same paper (handles LLM extraction inconsistencies)
    if _is_arxiv_identifier_title_mismatch(seg1, seg2):
        return True
    
    # Alternative: Check if we have exact author match with different titles
    # (This is less common but handles cases where title extraction varies)
    author1 = seg1['author']
    author2 = seg2['author']
    
    if author1 and author2 and author1.lower() == author2.lower():
        # Same authors - check if one title is substring of other or significant similarity
        title1 = seg1['title'].lower()
        title2 = seg2['title'].lower()
        
        if (title1 in title2 or title2 in title1):
            return True
    
    return False

def _extract_text(uploaded_file) -> str:
    if not uploaded_file or not uploaded_file.name:
        raise ValueError("No file uploaded.")

    suffix = Path(uploaded_file.name).suffix.lower()
    # st.write(str(uploaded_file))
    
    if suffix not in ALLOWED_SUFFIXES:
        raise ValueError("Unsupported file type. Please upload a .pdf, .txt or .tex file.")

    content = uploaded_file.read()
    if suffix == ".txt":
        return content.decode("utf-8", errors="ignore"), suffix

    if suffix == ".docx":
        document = Document(BytesIO(content))
        return "\n".join(paragraph.text for paragraph in document.paragraphs), suffix
    
    if suffix == ".tex":
        return content.decode("utf-8", errors="ignore"), suffix
        
    if suffix == ".pdf":
        pdf_content = BytesIO(content)
        # Try with pypdf first
        text = ""
        pdf_content.seek(0)  # Reset file pointer
        pdf_reader = pypdf.PdfReader(pdf_content)
        
        for page_num in range(len(pdf_reader.pages)):
            page = pdf_reader.pages[page_num]
            text += page.extract_text() + "\n"
        
        return text, suffix

def _build_report(text: str) -> dict:
    citations = extract_apa_citations(text)
    references = extract_reference_entries(text)
    if not references:
        raise ValueError("No References section found. Add a 'References' heading and at least one entry.")

    citation_keys = [c["key"] for c in citations]
    reference_keys = [r["key"] or r["entry"] for r in references if r.get("key") or r.get("entry")]
    report = build_report(citation_keys, reference_keys)
    report["citations"] = citations
    report["references"] = references
    report["char_count"] = len(text)
    report["preview"] = text[:500]
    return report

def _is_likely_reference(text):
    """
    Check if a numbered item is likely a bibliographic reference
    and not section headers, figure captions, etc.
    
    Args:
        text: The text to check (including the [N] number)
        
    Returns:
        bool: True if it looks like a reference, False otherwise
    """
    # Remove the reference number for analysis
    content = re.sub(r'^\[\d+\]\s*', '', text).strip()
    
    # If too short, probably not a reference
    if len(content) < 20:
        return False
        
    # Check for clear non-reference patterns
    non_reference_patterns = [
        r'^[A-Z\s]+$',  # All caps (section headers like "PROMPT FOR MEDGPT")
        r'^[A-Z][a-z]*\s+[a-z][a-z\s]*$',  # Title case section headers
        r'^(Computation|Prompt|Example|Figure|Table|Algorithm)\s+',  # Common section prefixes
        r'^[A-Za-z\s]+:$',  # Section headers ending with colon
        r'^\d+\.\d+\s+[A-Z]',  # Subsection numbers like "3.1 Title"
    ]
    
    for pattern in non_reference_patterns:
        if re.match(pattern, content):
            return False
    
    # Check for positive reference indicators
    reference_indicators = [
        r'\b(19|20)\d{2}\b',  # Years
        r'\bet\s+al\.?\b',    # "et al."
        r'\bvol\.?\s*\d+\b',  # Volume numbers
        r'\bpp\.?\s*\d+',     # Page numbers
        r'\bdoi[:.]',         # DOI
        r'https?://',         # URLs
        r'\barXiv\b',         # arXiv preprints
        r'\bProc\.?\s+of\b',  # "Proceedings of"
        r'\bJ\.\s+[A-Z]',     # Journal abbreviations like "J. Med"
        r'[A-Z][a-z]+,\s*[A-Z]',  # Author names like "Smith, J"
    ]
    
    # Count positive indicators
    indicator_count = sum(1 for pattern in reference_indicators if re.search(pattern, content))
    
    # If it has multiple reference indicators, likely a reference
    if indicator_count >= 2:
        return True
    
    # If it has at least one indicator and reasonable length, probably a reference
    if indicator_count >= 1 and len(content) > 50:
        return True
        
    # If no clear indicators but contains author-like patterns and reasonable length
    author_patterns = [
        r'[A-Z][a-z]+,\s*[A-Z]',  # "Smith, J"
        r'[A-Z]\.\s*[A-Z][a-z]+',  # "J. Smith"
    ]
    
    has_author_pattern = any(re.search(pattern, content) for pattern in author_patterns)
    if has_author_pattern and len(content) > 30:
        return True
        
    # Default to False for safety
    return False

def _get_verified_url(verified_data, reference_url, errors):
    """Get the appropriate verified URL based on priority and ArXiv ID validation"""
    # If we have verified data, we should show a verified URL even if there's an ArXiv ID error
    # The ArXiv ID error is a separate issue from successful paper verification
    
    # First priority: Non-ArXiv URLs from verified_data (direct from API, most reliable)
    if verified_data and verified_data.get('url') and 'arxiv.org' not in verified_data['url']:
        return verified_data['url']
    
    # Second priority: Semantic Scholar URL from CorpusId (if no direct URL available)
    if verified_data and verified_data.get('externalIds', {}).get('CorpusId'):
        from refchecker.utils.url_utils import construct_semantic_scholar_url
        return construct_semantic_scholar_url(verified_data['externalIds']['CorpusId'])
    
    # Third priority: DOI URL from verified data (more reliable than potentially wrong ArXiv URLs)
    if verified_data and verified_data.get('externalIds', {}).get('DOI'):
        from refchecker.utils.doi_utils import construct_doi_url
        return construct_doi_url(verified_data['externalIds']['DOI'])
    
    # Fourth priority: ArXiv URL from verified data (but only if there's no ArXiv ID error)
    if verified_data and verified_data.get('externalIds', {}).get('ArXiv'):
        # Only show ArXiv URL as verified URL if there's no ArXiv ID mismatch
        if not _has_arxiv_id_error(errors):
            from refchecker.utils.url_utils import construct_arxiv_url
            correct_arxiv_id = verified_data['externalIds']['ArXiv']
            return construct_arxiv_url(correct_arxiv_id)
    
    # Fifth priority: Other URLs from verified_data
    if verified_data and verified_data.get('url'):
        return verified_data['url']
    
    # Last resort: Use the URL returned by the verification process (but be cautious with ArXiv URLs)
    if reference_url:
        return _validate_reference_url(reference_url, verified_data)
        
    return None

def _validate_reference_url(reference_url, verified_data):
    """Validate and potentially replace reference URL based on ArXiv ID matching"""
    # If it's an ArXiv URL and we have verified data, only use it if the ArXiv ID matches
    if 'arxiv.org' in reference_url and verified_data:
        external_ids = verified_data.get('externalIds', {})
        if external_ids.get('ArXiv'):
            # Extract ArXiv ID from the URL using shared utility
            from refchecker.utils.url_utils import extract_arxiv_id_from_url
            url_arxiv_id = extract_arxiv_id_from_url(reference_url)
            if url_arxiv_id:
                correct_arxiv_id = external_ids['ArXiv']
                # Only use the URL if the ArXiv IDs match
                if url_arxiv_id == correct_arxiv_id:
                    return reference_url
                # If they don't match, prefer the Semantic Scholar URL or DOI
                else:
                    return _get_fallback_url(external_ids)
            else:
                # If we can't extract ArXiv ID, be safe and use verified data
                return _get_fallback_url(external_ids)
        else:
            # No verified ArXiv ID, so the URL might be wrong
            return reference_url
    else:
        # Non-ArXiv URL, probably safe to use
        return reference_url

def _get_fallback_url(external_ids):
    """Get fallback URL from external IDs (Semantic Scholar or DOI)"""
    if external_ids.get('CorpusId'):
        from refchecker.utils.url_utils import construct_semantic_scholar_url
        return construct_semantic_scholar_url(external_ids['CorpusId'])
    elif external_ids.get('DOI'):
        from refchecker.utils.doi_utils import construct_doi_url
        return construct_doi_url(external_ids['DOI'])
    return None

def _format_year_string(year):
    """Format year for display, handling missing or invalid years"""
    if year and year != 0:
        return str(year)
    return "year unknown"

def _display_unverified_error_with_subreason(reference, reference_url, errors, debug_mode, print_output):
    """Display the unverified error message with citation details and subreason"""
    if not debug_mode and print_output:
        st.write(f"      ❓ Could not verify: {reference.get('title', 'Untitled')}")
        
        # Extract and display the subreason from unverified errors
        unverified_errors = [e for e in errors if e.get('error_type') == 'unverified']
        if unverified_errors:
            error_details = unverified_errors[0].get('error_details', '')
            if error_details:
                subreason = _categorize_unverified_reason(error_details)
                st.write(f"         Subreason: {subreason}")

def _categorize_unverified_reason(error_details):
    """Categorize the unverified error into checker error or not found"""
    error_details_lower = error_details.lower()
    
    # New specific URL-based unverified reasons
    if error_details_lower == "non-existent web page":
        return "Non-existent web page"
    elif error_details_lower == "paper not found and url doesn't reference it":
        return "Paper not found and URL doesn't reference it"
    elif error_details_lower == "paper not verified but url references paper":
        return "Paper not verified but URL references paper"
    
    # Checker/API errors
    api_error_patterns = [
        'api error', 'rate limit', 'http error', 'network error', 
        'could not fetch', 'connection', 'timeout', 'server error',
        'could not verify reference using any available api',
        'database connection not available'
    ]
    
    # Not found patterns  
    not_found_patterns = [
        'not found', 'could not be found', 'repository not found',
        'web page not found', '404', 'invalid', 'too short or empty'
    ]
    
    # Processing errors
    processing_error_patterns = [
        'error processing', 'error parsing', 'unexpected error'
    ]
    
    for pattern in api_error_patterns:
        if pattern in error_details_lower:
            return "Checker had an error"
            
    for pattern in not_found_patterns:
        if pattern in error_details_lower:
            return "Paper not found by any checker"
            
    for pattern in processing_error_patterns:
        if pattern in error_details_lower:
            return "Checker had an error"
    
    # Default fallback
    return "Paper not found by any checker"

def _display_non_unverified_errors(errors, debug_mode, print_output=True):
    """Display all non-unverified errors and warnings"""
    if not debug_mode and print_output:
        # st.write(errors)
        for error in errors:
            if error.get('error_type') != 'unverified' and error.get('warning_type') != 'unverified' and error.get('info_type') != 'unverified':
                error_type = error.get('error_type') or error.get('warning_type') or error.get('info_type')
                error_details = error.get('error_details') or error.get('warning_details') or error.get('info_details', 'Unknown error')
                
                # from refchecker.utils.error_utils import print_labeled_multiline

                if error_type == 'arxiv_id':
                    st.write(f"      ❌ {error_details}")
                elif 'error_type' in error:
                    # print_labeled_multiline("❌ Error", error_details)
                    st.write(f"      ❌ {error_details}")
                elif 'warning_type' in error:
                    # print_labeled_multiline("⚠️  Warning", error_details)
                    st.write(f"      ⚠️  Warning: {error_details}")
                else:
                    # print_labeled_multiline("ℹ️  Information", error_details)
                    st.write(f"      ℹ️  Information: {error_details}")

def _output_reference_errors(reference, errors, url):
    """
    Output method for parallel processor to use (maintains consistent formatting)
    
    Args:
        reference: The reference being processed
        errors: List of errors found
        url: URL of the reference if found
    """
    # This method is called by the parallel processor to maintain output format
    # The actual processing is handled by _process_reference_result
    pass

def _cleanup_resources():
    """Clean up database connections and other resources"""
    try:
        non_arxiv_checker.close()
        # No logging - cleanup happens automatically
    except Exception as e:
        # Silent cleanup - errors are expected with SQLite threading
        pass

def _process_reference_result(paper, reference, errors, reference_url, 
                            paper_errors, unverified_count, debug_mode=False, print_output=True, verified_data=None):
    """
    Process the result of reference verification (shared by both sequential and parallel)
    
    Args:
        paper: The source paper
        reference: The reference that was verified
        errors: List of errors found (or None)
        reference_url: URL of the reference if found
        paper_errors: List to append errors to
        unverified_count: Counter for unverified references (passed by reference)
        debug_mode: Whether debug mode is enabled
        print_output: Whether to print output (False for parallel mode to avoid duplication)
    """
    global total_errors_found
    global total_warnings_found
    global total_info_found
    global total_unverified_refs
    # If errors found, add to dataset and optionally print details
    if errors:
        # Check if there's an unverified error among the errors
        has_unverified_error = any(e.get('error_type') == 'unverified' or e.get('warning_type') == 'unverified' or e.get('info_type') == 'unverified' for e in errors)
        
        if has_unverified_error:
            total_unverified_refs += 1
            _display_unverified_error_with_subreason(reference, reference_url, errors, debug_mode, print_output)
        
        # Add to dataset and handle all errors
        add_error_to_dataset(paper, reference, errors, reference_url, verified_data)
        paper_errors.extend(errors)
        
        # Count errors vs warnings vs info
        error_count = sum(1 for e in errors if 'error_type' in e and e['error_type'] != 'unverified')
        warning_count = sum(1 for e in errors if 'warning_type' in e)
        info_count = sum(1 for e in errors if 'info_type' in e)
        total_errors_found += error_count
        total_warnings_found += warning_count
        total_info_found += info_count
        
        # Display all non-unverified errors and warnings
        _display_non_unverified_errors(errors, debug_mode, print_output)
    with st.expander("Log"):
        st.write("Information log: ")
        st.write(logger_info)
        st.divider()
        st.write("Error log: ")
        st.write(logger_error)
        st.divider()
        st.write("Warning log: ")
        st.write(logger_warning)
        st.divider()
        st.write("Debug log: ")
        st.write(logger_debug)

def _has_arxiv_id_error(errors):
    """Check if there's an ArXiv ID error in the error list"""
    if not errors:
        return False
    return any(error.get('error_type') == 'arxiv_id' for error in errors)

def _extract_corrected_data_from_error(error, verified_data):
    """
    Extract corrected data from error object and verified data
    
    Args:
        error: Error dictionary containing correction information
        verified_data: Verified data from the verification service
        
    Returns:
        Dictionary with corrected data fields
    """
    corrected_data = {}
    
    # Extract corrected information from error object
    # Always try to get title - either the corrected one or from verified_data
    if error.get('ref_title_correct'):
        corrected_data['title'] = error['ref_title_correct']
    elif verified_data and verified_data.get('title'):
        corrected_data['title'] = verified_data['title']
        
    if error.get('ref_authors_correct'):
        corrected_data['authors'] = error['ref_authors_correct']
    elif verified_data and verified_data.get('authors'):
        # Format authors from verified data
        if isinstance(verified_data['authors'], list):
            if verified_data['authors'] and isinstance(verified_data['authors'][0], dict):
                # Semantic Scholar format: [{'name': 'Author Name'}, ...]
                author_names = [author.get('name', '') for author in verified_data['authors']]
                corrected_data['authors'] = ', '.join(author_names)
            else:
                # Simple list of names
                corrected_data['authors'] = ', '.join(verified_data['authors'])
        else:
            corrected_data['authors'] = str(verified_data['authors'])
            
    if error.get('ref_year_correct'):
        corrected_data['year'] = error['ref_year_correct']
    elif verified_data and verified_data.get('year'):
        corrected_data['year'] = verified_data['year']
        
    if error.get('ref_url_correct'):
        corrected_data['url'] = error['ref_url_correct']
    elif verified_data and verified_data.get('url'):
        corrected_data['url'] = verified_data['url']
        
    # Add venue information
    if error.get('ref_venue_correct'):
        corrected_data['venue'] = error['ref_venue_correct']
    elif verified_data:
        if verified_data.get('venue'):
            corrected_data['venue'] = verified_data['venue']
        elif verified_data.get('journal'):
            corrected_data['journal'] = verified_data['journal']
    
    # Add DOI if available from verified data
    if verified_data:
        external_ids = verified_data.get('externalIds', {})
        if external_ids and external_ids.get('DOI'):
            corrected_data['doi'] = external_ids['DOI']
            
    return corrected_data

        
def _verify_references_sequential(paper, bibliography, paper_errors, error_types, unverified_count, debug_mode=False):
    """
    Sequential reference verification (original implementation)
    
    Args:
        paper: The source paper
        bibliography: List of references to verify
        paper_errors: List to append errors to
        error_types: Dictionary to track error types
        unverified_count: Counter for unverified references
        debug_mode: Whether debug mode is enabled
    """
    
    debug_mode = False
    for i, reference in enumerate(bibliography):
        global logger_debug
        global logger_warning
        global logger_info
        global logger_error
        logger_debug = ''
        logger_warning = ''
        logger_info = ''
        logger_error = ''
        st.divider()
        ref_id = extract_arxiv_id_from_url_app(reference['url'])
        # Print reference info in non-debug mode (improved formatting)
        raw_title = reference.get('title', 'Untitled')
        # Clean LaTeX commands from title for display
        from refchecker.utils.text_utils import strip_latex_commands
        title = strip_latex_commands(raw_title)
        from refchecker.utils.text_utils import format_authors_for_display
        authors = format_authors_for_display(reference.get('authors', []))
        year = reference.get('year', '')
        venue = reference.get('venue', '') or reference.get('journal', '')
        url = reference.get('url', '')
        doi = reference.get('doi', '')
        # Extract actual reference number from raw text for accurate display
        raw_text = reference.get('raw_text', '')
        match = re.match(r'\[(\d+)\]', raw_text)
        ref_num = match.group(1) if match else str(i + 1)
        st.markdown(f"**[{ref_num}/{len(bibliography)}] {title}**")
        if authors:
            st.markdown(f" * {authors}")
        if venue:
            st.markdown(f" * {venue}")
        if year:
            st.markdown(f" * {year}")
        if doi:
            st.markdown(f" * {doi}")
        with st.spinner(text="Verification in progress"):
            # --- DEBUG TIMER ---
            start_time = time.time()
            errors, reference_url, verified_data = verify_reference(paper, reference)

            # Show cited URL if available
            if url:
                st.markdown(f" * Cited URL: {url}")
            
            # Get the appropriate verified URL using shared logic
            verified_url_to_show = _get_verified_url(verified_data, reference_url, errors)
            
            # Show the verified URL with appropriate label
            if verified_url_to_show:
                st.markdown(f" * Verified URL: {verified_url_to_show}")
            
            # Show correct ArXiv URL if available from verified data and different from cited
            if verified_data:
                external_ids = verified_data.get('externalIds', {})
                if external_ids.get('ArXiv'):
                    correct_arxiv_url = f"https://arxiv.org/abs/{external_ids['ArXiv']}"
                    # Only show if it's different from the cited URL
                    if correct_arxiv_url != url:
                        st.markdown(f" * Correct ArXiv URL: {correct_arxiv_url}")
            
            # Show additional external ID URLs if available and different
            if verified_data:
                external_ids = verified_data.get('externalIds', {})
                
                # Show DOI URL if available and different from what's already shown
                if external_ids.get('DOI'):
                    from refchecker.utils.doi_utils import construct_doi_url
                    doi_url = construct_doi_url(external_ids['DOI'])
                    if doi_url != verified_url_to_show and doi_url != url:
                        st.markdown(f" * DOI URL: {doi_url}")
                
                # Show any other URL from verified data if different
                if verified_data.get('url') and verified_data['url'] != verified_url_to_show and verified_data['url'] != url:
                    st.markdown(f" * {verified_data['url']}")
            elapsed = time.time() - start_time
            if elapsed > 5.0:
                logger_debug += (f"\nReference {i+1} took {elapsed:.2f}s to verify: {reference.get('title', 'Untitled')}")
                logger_debug += (f"\nRaw text: {reference.get('raw_text', '')}")
            
            _process_reference_result(paper, reference, errors, reference_url, 
                                         paper_errors, unverified_count, debug_mode, verified_data=verified_data)


def main() -> None:
    global total_papers_processed
    global total_references_processed
    global papers_with_errors
    global papers_with_warnings
    global papers_with_info
    global total_errors_found
    global total_warnings_found
    global total_info_found
    global total_arxiv_refs
    global total_non_arxiv_refs
    global total_other_refs
    global total_unverified_refs
    global used_regex_extraction
    global used_unreliable_extraction
    global logger_debug
    global logger_warning
    global logger_info
    global logger_error
    logger_debug = ''
    logger_warning = ''
    logger_info = ''
    logger_error = ''
    st.set_page_config(page_title="Reference Checker", page_icon="📑", layout="wide")
    st.title("Academic Paper Reference Checker")
    st.write("Upload a .pdf or .tex file. We'll verify the accuracy of references by comparing cited information against authoritative sources")

    uploaded = st.file_uploader("Choose a .pdf, .txt or .tex file", type=["pdf", "txt", "tex"])

    if uploaded and st.button("Run check"):
        try:
            text, suffix = _extract_text(uploaded)
            with st.expander("Bibliography extraction log"):
                # report = _build_report(text)
                # extract bibliography
                st.write('Processing: ' + uploaded.name)
                # st.write('')
                total_references_processed = 0
                bibliography = extract_bibliography_app(text, suffix)
                st.text("Document text preview: ")
                st.text(text[:200] + "  [...]  " + text[-200:])
                with st.expander("Bibliography JSON"):
                    st.write(bibliography)
                # Apply deduplication to all bibliography sources (not just LLM-extracted)
                if len(bibliography) > 1:  # Only deduplicate if we have multiple references
                    original_count = len(bibliography)
                    bibliography = _deduplicate_bibliography_entries(bibliography)
                    if len(bibliography) < original_count:
                        st.write(f"Deduplicated {original_count} references to {len(bibliography)} unique references")
                # Update statistics
                total_references_processed += len(bibliography)
                st.markdown(' * Total references in bibliography: ' + str(original_count))
                st.markdown(' * Total unique references: ' + str(len(bibliography)))
                # st.write('Bibliography:' + str(bibliography))
                
                # results
                # Initialize counters for statistics
                total_papers_processed = 0
                total_references_processed = 0
                papers_with_errors = 0
                papers_with_warnings = 0
                papers_with_info = 0
                total_errors_found = 0
                total_warnings_found = 0
                total_info_found = 0
                total_arxiv_refs = 0
                total_non_arxiv_refs = 0
                total_other_refs = 0
                total_unverified_refs = 0
                used_regex_extraction = False
                used_unreliable_extraction = False
                                    
                # Update statistics
                total_papers_processed += 1
                total_references_processed += len(bibliography)

                # Count references by type
                arxiv_refs = [ref for ref in bibliography if ref.get('type') == 'arxiv']
                non_arxiv_refs = [ref for ref in bibliography if ref.get('type') == 'non-arxiv']
                other_refs = [ref for ref in bibliography if ref.get('type') == 'other']
                
                total_arxiv_refs += len(arxiv_refs)
                total_non_arxiv_refs += len(non_arxiv_refs)
                total_other_refs += len(other_refs)
                
                st.markdown(' * Total arxiv references in bibliography: ' + str(len(arxiv_refs)))
                st.markdown(' * Total non arxiv references in bibliography: ' + str(len(non_arxiv_refs)))
                st.markdown(' * Total other references in bibliography: ' + str(len(other_refs)))

            # Track errors for this paper
            paper_errors = []
            error_types = {}
            unverified_count = 0  # Count unverified references

            # Pre-fetch all ArXiv references in batches for better performance
            # batch_prefetch_arxiv_references(bibliography)

            # Check references
            debug_mode = False
            fatal_error = False
            paper_id = uploaded.name
            _verify_references_sequential(paper_id, bibliography, paper_errors, error_types, unverified_count, debug_mode)


            # Separate actual errors from warnings for paper classification
            actual_errors = [e for e in paper_errors if 'error_type' in e and e['error_type'] != 'unverified']
            warnings_only = [e for e in paper_errors if 'warning_type' in e]
            info_only = [e for e in paper_errors if 'info_type' in e]

            # Single paper mode - show simple summary
            if actual_errors or warnings_only or info_only:
                summary_parts = []
                if actual_errors:
                    summary_parts.append(f"{len(actual_errors)} errors")
                if warnings_only:
                    summary_parts.append(f"{len(warnings_only)} warnings")
                if info_only:
                    summary_parts.append(f"{len(info_only)} information")

            # Print final summary to console (only if no fatal error occurred)
            if not debug_mode and not fatal_error:
                # Single paper mode - show simplified summary
                st.divider()
                st.subheader(f"📋 SUMMARY", divider=True)
                st.write(f"📚 Total references processed: {total_references_processed}")
                if total_errors_found > 0:
                    st.write(f"❌ Total errors: {total_errors_found}")
                if total_warnings_found > 0:
                    st.write(f"⚠️  Total warnings: {total_warnings_found}")
                if total_info_found > 0:
                    st.write(f"ℹ️  Total information: {total_info_found}")
                if total_unverified_refs > 0:
                    st.write(f"❓ References that couldn't be verified: {total_unverified_refs}")
                if total_errors_found == 0 and total_warnings_found == 0 and total_info_found == 0 and total_unverified_refs == 0:
                    st.write(f"✅ All references verified successfully!")
                
                # Show warning if unreliable extraction was used and there are many errors
                if used_unreliable_extraction and total_errors_found > 5:
                    st.write(f"\n⚠️  Results might be affected by incorrect reference extraction. Consider using LLM extraction, which is more robust.")


            # st.session_state["report"] = report
            st.success("Analysis complete.")
        except Exception as exc:
            st.error(str(exc))
            return


if __name__ == "__main__":
    main()
