import os
import json
import logging
import httpx
import re
import asyncio
import time
from dotenv import load_dotenv
from openai import AsyncOpenAI
from google import genai
from google.genai import types
from prompts import RESEARCH_SYSTEM_PROMPT, DRAFTING_SYSTEM_PROMPT, INITIAL_DRAFTING_PROMPT

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
DRAFT_CHUNK_SIZE = int(os.getenv("DRAFT_CHUNK_SIZE", "3000"))

PERPLEXITY_MODEL = os.getenv("PERPLEXITY_MODEL", "sonar-deep-research")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
DEEP_RESEARCH_AGENT = "deep-research-pro-preview-12-2025"

if not GEMINI_API_KEY:
    logger.warning("GEMINI_API_KEY is not set. Drafting and Research will fail.")

# Google Gemini Client
google_client = None
if GEMINI_API_KEY:
    google_client = genai.Client(api_key=GEMINI_API_KEY)

# -------------------------------------------------------------------------

async def perform_research(transcription: str) -> dict:
    """
    Orchestrates the two-step pipeline:
    1. Deep Research (Source gathering & Analysis using Gemini Deep Research)
    2. Content Drafting (Tweet generation based on step 1)
    """
    if not google_client:
        raise ValueError("Gemini API Key is missing.")

    logger.info("Starting Pipeline: Step 1 - Deep Research (Gemini)...")
    
    # --- STEP 1: RESEARCH ---
    research_data = await _run_research_step_gemini(transcription)
    research_notes = research_data.get("research_notes", "")
    citations = research_data.get("sources", [])
    
    logger.info(f"Step 1 Complete. Notes length: {len(research_notes)}")
    logger.info("Starting Pipeline: Step 2 - Drafting Tweets...")

    # --- STEP 2: DRAFTING ---
    draft_data = await perform_drafting(transcription, research_notes, citations)
    
    # Combine results
    return {
        "research_notes": research_notes,
        "sources": citations,
        "tweet_drafts": draft_data.get("tweet_drafts", [])
    }

async def _run_research_step_gemini(transcription: str) -> dict:
    """
    Runs Gemini Deep Research Agent via Interactions API.
    """
    try:
        # Construct the input prompt
        # We combine the system instructions and the transcription.
        prompt = f"{RESEARCH_SYSTEM_PROMPT}\n\nAnalyze this transcription:\n\n{transcription}"
        
        logger.info("Initiating Deep Research Interaction...")
        
        # Start the interaction
        def run_sync_interaction():
            interaction = google_client.interactions.create(
                input=prompt,
                agent=DEEP_RESEARCH_AGENT,
                background=True,
            )
            return interaction

        interaction = await asyncio.to_thread(run_sync_interaction)
        logger.info(f"Research started: {interaction.id}")

        # Poll for completion
        while True:
            await asyncio.sleep(10) # Non-blocking sleep
            
            def get_interaction_status(iid):
                return google_client.interactions.get(iid)

            interaction = await asyncio.to_thread(get_interaction_status, interaction.id)
            
            logger.info(f"Research Status: {interaction.status}")
            
            if interaction.status == "completed":
                break
            elif interaction.status == "failed":
                error_msg = getattr(interaction, 'error', 'Unknown Error')
                raise RuntimeError(f"Deep Research failed: {error_msg}")
        
        # Get the result
        if not interaction.outputs:
             raise RuntimeError("Deep Research completed but returned no outputs.")
             
        final_output = interaction.outputs[-1].text
        logger.info(f"Deep Research Output Length: {len(final_output)}")
        
        # Extract sources from the text first, as they are most reliable there
        extracted_sources = _extract_sources_from_text(final_output)
        
        # Try to parse as JSON (per prompt instructions)
        try:
            cleaned_content = _clean_json_markdown(final_output)
            data = json.loads(cleaned_content)
            
            # If JSON is valid, extract fields
            research_notes = data.get("research_notes", final_output)
            json_sources = data.get("sources", [])
            
            # Combine sources, prioritizing extracted ones if JSON is empty
            sources = extracted_sources if extracted_sources else json_sources
            
            return {"research_notes": research_notes, "sources": sources}
            
        except json.JSONDecodeError:
            logger.warning("Deep Research output was not valid JSON. Using raw text as research notes.")
            # Fallback: Treat the whole text as the report
            return {"research_notes": final_output, "sources": extracted_sources}

    except Exception as e:
        logger.error(f"Research Step Failed: {e}")
        raise e

def _extract_sources_from_text(text: str) -> list:
    """
    Parses 'Sources' section from Gemini text output to build a list of URLs.
    Handles various formats including numbered lists, markdown links, and plain URLs.
    """
    sources = []
    try:
        # Find the start of the sources section (case-insensitive)
        # Look for "**Sources:**" or "Sources:" at the start of a line
        match = re.search(r'(?i)^\s*\*\*?Sources:?\*\*?', text, re.MULTILINE)
        
        if match:
            sources_text = text[match.end():]
            
            # Split by lines and look for numbered items or links
            lines = sources_text.splitlines()
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                
                # Regex to find URL in "1. [Title](URL)" or "1. URL"
                # 1. Markdown link: \[.*?\]\((.*?)\)
                md_link_match = re.search(r'\[.*?\]\((https?://.*?)\)', line)
                if md_link_match:
                    sources.append(md_link_match.group(1))
                    continue
                    
                # 2. Plain URL: (https?://...)
                url_match = re.search(r'(https?://[^\s\)]+)', line)
                if url_match:
                    sources.append(url_match.group(1))
                    continue
        else:
             # Fallback: If no "Sources" header, just scan the whole text for a block of URLs at the end?
             # Or generally scan for all URLs? 
             # Let's try to find citations like [1] [site.com] pattern if standard header missing
             pass

    except Exception as e:
        logger.warning(f"Failed to extract sources from text: {e}")
        
    logger.info(f"Extracted {len(sources)} sources from text report.")
    return sources

async def perform_drafting(transcription: str, research_notes: str, citations: list = None) -> dict:
    if not google_client:
        logger.error("Gemini Client not initialized.")
        return {"tweet_drafts": ["Error: GEMINI_API_KEY missing."]}

    logger.info(f"=== STARTING DRAFTING PHASE ===")
    logger.info(f"Total Research Notes Length: {len(research_notes)}")
    
    # Robust Chunking Strategy
    # 1. Try splitting by Headers (Level 1, 2, or 3)
    chunks = re.split(r'(?m)^(?=#{1,3}\s)', research_notes)
    
    # Filter out empty strings and strip whitespace
    valid_chunks = [c.strip() for c in chunks if c.strip()]
    logger.info(f"Initial split by headers found {len(valid_chunks)} chunks.")
    
    # 2. Fallback: If only 1 chunk found (no headers matched), try splitting by double newline (paragraphs)
    if len(valid_chunks) <= 1:
        logger.info("No headers found for splitting (or single chunk). Attempting paragraph split strategy...")
        chunks = research_notes.split('\n\n')
        logger.info(f"Split by double newline found {len(chunks)} paragraphs.")
        
        # Re-group paragraphs into larger chunks if they are too small
        # Simple logic: merge paragraphs until ~2000 chars
        merged_chunks = []
        current_chunk = ""
        
        for p in chunks:
            p = p.strip()
            if not p: continue
            
            if len(current_chunk) + len(p) < 2000:
                current_chunk += "\n\n" + p
            else:
                if current_chunk: merged_chunks.append(current_chunk)
                current_chunk = p
        
        if current_chunk: merged_chunks.append(current_chunk)
        
        # If merged chunks resulted in valid chunks, use them. Otherwise fallback to the single research_notes
        valid_chunks = merged_chunks if merged_chunks else [research_notes]
        logger.info(f"After merging paragraphs, created {len(valid_chunks)} valid chunks.")
    
    logger.info(f"Final Processing: {len(valid_chunks)} sections. Max target chunk size: {DRAFT_CHUNK_SIZE}")
    
    all_tweets = []
    
    # Process sequentially to maintain context
    for index, chunk_text in enumerate(valid_chunks):
        try:
            chunk_len = len(chunk_text)
            logger.info(f"--- Processing Chunk {index} ({chunk_len} chars) ---")
            logger.debug(f"Chunk Start: {chunk_text[:100]}...") 
            
            # Context for system prompt: Last 2 tweets
            previous_context = ""
            if all_tweets:
                last_two = all_tweets[-2:]
                previous_context = "\n".join([f"- {t}" for t in last_two])
            else:
                previous_context = "None (Start of thread)"
                
            logger.info(f"Context injected: {previous_context[:100]}...")

            # Select and format prompt
            if index == 0:
                logger.info("Using INITIAL_DRAFTING_PROMPT")
                system_prompt = INITIAL_DRAFTING_PROMPT
            else:
                logger.info("Using DRAFTING_SYSTEM_PROMPT")
                # Format the DRAFTING_SYSTEM_PROMPT with the context
                system_prompt = DRAFTING_SYSTEM_PROMPT.format(previous_context=previous_context)
            
            user_content = (
                f"--- RESEARCH SECTION ---\n{chunk_text}\n\n"
                f"--- ORIGINAL TRANSCRIPTION CONTEXT ---\n{transcription}\n\n"
                "Generate the tweets based on this research section."
            )

            response = await google_client.aio.models.generate_content(
                model=GEMINI_MODEL,
                contents=user_content,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    response_mime_type="application/json",
                    response_schema={
                        "type": "OBJECT",
                        "properties": {
                             "tweet_drafts": {
                                 "type": "ARRAY",
                                 "items": {"type": "STRING"}
                             }
                        }
                    }
                )
            )
            
            logger.info(f"Chunk {index} Response received.")
            logger.debug(f"Raw Response: {response.text}")
            
            data = json.loads(response.text)
            drafts = data.get("tweet_drafts", [])
            
            if not drafts:
                logger.warning(f"Chunk {index}: No 'tweet_drafts' found in response.")
            else:
                logger.info(f"Chunk {index} generated {len(drafts)} tweets.")
            
            all_tweets.extend(drafts)
            
        except Exception as e:
            logger.error(f"Error processing chunk {index}: {e}")
            # Continue to next chunk even if one fails
            continue

    # --- CITATION REPLACEMENT LOGIC ---
    if citations:
        logger.info(f"Replacing citations in {len(all_tweets)} tweets with {len(citations)} sources...")
        logger.debug(f"Available Sources: {citations}")
        
        def replace_citation(match):
            try:
                # match.group(1) is the number N in [cite: N]
                idx = int(match.group(1)) - 1 # 0-based index
                if 0 <= idx < len(citations):
                    # Gemini sources are often objects with 'uri' or 'url'
                    source = citations[idx]
                    if isinstance(source, dict):
                         url = source.get("uri") or source.get("url") or str(source)
                    else:
                         url = str(source)
                    return f"({url})"
                return match.group(0) # Return original if out of bounds
            except Exception:
                return match.group(0)

        processed_tweets = []
        for tweet in all_tweets:
            # Handle standard [N] and Gemini's [cite: N]
            # Pattern matches [1], [12], [cite: 1], [cite: 12]
            new_tweet = re.sub(r'\[(?:cite:\s*)?(\d+)\]', replace_citation, tweet)
            processed_tweets.append(new_tweet)
        
        all_tweets = processed_tweets
        
    logger.info(f"Drafting Complete. Generated {len(all_tweets)} tweets total.")
    return {"tweet_drafts": all_tweets}

def _clean_json_markdown(text: str) -> str:
    """Helper to strip ```json and ``` code blocks."""
    if text.startswith("```json"):
        return text.replace("```json", "").replace("```", "")
    elif text.startswith("```"):
        return text.replace("```", "")
    return text.strip()
