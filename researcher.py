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
from langchain_text_splitters import RecursiveCharacterTextSplitter
from prompts import RESEARCH_SYSTEM_PROMPT, DRAFTING_SYSTEM_PROMPT, INITIAL_DRAFTING_PROMPT

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
DRAFT_CHUNK_SIZE = int(os.getenv("DRAFT_CHUNK_SIZE", "3000"))

PERPLEXITY_MODEL = os.getenv("PERPLEXITY_MODEL", "sonar-deep-research")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3-pro-preview")
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
    logger.info(f"Research Notes Preview (Raw): {repr(research_notes[:500])}...")
    
    # --- Advanced Chunking Strategy (Regex Pre-segmentation + Merge) ---
    try:
        # 1. Normalize newlines
        text = research_notes.replace("\\n", "\n")

        # 2. Regex Split (Perplexity suggestion)
        # Split on two or more newlines OR bullet starts OR markdown headings.
        # This breaks the text into atomic semantic units (paragraphs, list items, headers)
        parts = re.split(
            r"(?:\n{2,}|(?=^#{1,6} )|(?=^\* {2,})|(?=^\d+\. ))",
            text,
            flags=re.MULTILINE,
        )
        
        pre_chunks = [p.strip() for p in parts if p.strip()]
        logger.info(f"Regex pre-segmentation created {len(pre_chunks)} atomic blocks.")

        # 3. Intelligent Merge to DRAFT_CHUNK_SIZE
        # We re-assemble these small blocks into larger chunks that fit the context window
        valid_chunks = []
        current_chunk = ""
        
        # Helper to safely split very large atomic blocks (nuclear option)
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=DRAFT_CHUNK_SIZE,
            chunk_overlap=200,
            separators=["\n\n", "\n", " ", ""],
            keep_separator=True
        )

        for block in pre_chunks:
            # If a single block is massive (unlikely but possible), split it first
            if len(block) > DRAFT_CHUNK_SIZE:
                sub_chunks = splitter.split_text(block)
                # Flush current buffer
                if current_chunk:
                    valid_chunks.append(current_chunk.strip())
                    current_chunk = ""
                # Add sub-chunks, keeping the last one in buffer if it fits, else flush all
                for sub in sub_chunks:
                    if len(sub) < DRAFT_CHUNK_SIZE: # Should be true given splitter config
                         valid_chunks.append(sub)
                    else:
                         valid_chunks.append(sub)
                continue

            # Check if adding this block exceeds the limit
            # +2 for the newline separator
            if len(current_chunk) + len(block) + 2 <= DRAFT_CHUNK_SIZE:
                if current_chunk:
                    current_chunk += "\n\n" + block
                else:
                    current_chunk = block
            else:
                # Flush current chunk
                if current_chunk:
                    valid_chunks.append(current_chunk.strip())
                # Start new chunk with current block
                current_chunk = block
        
        # Flush remaining buffer
        if current_chunk:
            valid_chunks.append(current_chunk.strip())

        logger.info(f"Advanced Chunking created {len(valid_chunks)} final chunks using size={DRAFT_CHUNK_SIZE}.")

    except Exception as e:
        logger.error(f"Advanced chunking failed: {e}. Falling back to LangChain default.")
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=DRAFT_CHUNK_SIZE,
            chunk_overlap=200,
            separators=["\n\n", "\n", " ", ""],
            keep_separator=True
        )
        valid_chunks = splitter.split_text(research_notes)
    
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
            
            drafts = []
            try:
                cleaned_text = _clean_json_markdown(response.text)
                data = json.loads(cleaned_text)
                
                # Case 1: Standard Dict
                if isinstance(data, dict):
                    # Exact match
                    if "tweet_drafts" in data:
                        drafts = data["tweet_drafts"]
                    else:
                        # Fuzzy match keys
                        found_key = None
                        for key in data.keys():
                            if "tweet_drafts" in key.lower() or "tweets" in key.lower():
                                found_key = key
                                break
                        if found_key:
                            drafts = data[found_key]
                        else:
                            logger.warning(f"Chunk {index}: JSON dict returned but no 'tweet_drafts' key found. Keys: {list(data.keys())}")
                
                # Case 2: List returned directly
                elif isinstance(data, list):
                    drafts = data
                    
            except json.JSONDecodeError:
                logger.warning(f"Chunk {index}: Invalid JSON. Attempting regex extraction.")
                # Fallback: find ["..."] pattern
                array_match = re.search(r'\[.*\]', response.text, re.DOTALL)
                if array_match:
                    try:
                        potential_drafts = json.loads(array_match.group(0))
                        if isinstance(potential_drafts, list):
                            drafts = potential_drafts
                    except:
                        pass
                
                # If still empty, try line-based heuristic
                if not drafts:
                     drafts = [line.strip() for line in response.text.split('\n') if len(line) > 20 and not line.strip().startswith('```') and not line.strip().startswith('{') and not line.strip().startswith('}')]

            # Normalize drafts (ensure they are strings)
            if drafts:
                drafts = [str(d) for d in drafts if isinstance(d, (str, int, float))]

            if not drafts:
                logger.warning(f"Chunk {index}: No 'tweet_drafts' found in response.")
            else:
                logger.info(f"Chunk {index} generated {len(drafts)} tweets.")
            
            all_tweets.extend(drafts)
            
        except Exception as e:
            logger.error(f"Error processing chunk {index}: {e}")
            logger.error(f"Raw Response causing error: {response.text}") # Log the culprit
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
