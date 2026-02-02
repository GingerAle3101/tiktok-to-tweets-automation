import os
import json
import logging
import httpx
import re
import asyncio
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

PERPLEXITY_MODEL = os.getenv("PERPLEXITY_MODEL", "sonar-deep-research")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

if not PERPLEXITY_API_KEY:
    logger.warning("PERPLEXITY_API_KEY is not set. Research features will fail.")

if not GEMINI_API_KEY:
    logger.warning("GEMINI_API_KEY is not set. Drafting will fail.")

# Custom HTTP client to pass WAF/Cloudflare checks
custom_http_client = httpx.AsyncClient(
    headers={"User-Agent": "TikTok-Automation/1.0"},
    timeout=60.0
)

# Perplexity Client
perplexity_client = AsyncOpenAI(
    api_key=PERPLEXITY_API_KEY,
    base_url="https://api.perplexity.ai",
    http_client=custom_http_client
)

# Google Gemini Client
google_client = None
if GEMINI_API_KEY:
    google_client = genai.Client(api_key=GEMINI_API_KEY)

# -------------------------------------------------------------------------

async def perform_research(transcription: str) -> dict:
    """
    Orchestrates the two-step pipeline:
    1. Deep Research (Source gathering & Analysis)
    2. Content Drafting (Tweet generation based on step 1)
    """
    if not PERPLEXITY_API_KEY:
        raise ValueError("Perplexity API Key is missing.")

    logger.info("Starting Pipeline: Step 1 - Deep Research...")
    
    # --- STEP 1: RESEARCH ---
    research_data = await _run_research_step(transcription)
    research_notes = research_data.get("research_notes", "")
    citations = research_data.get("sources", [])
    
    logger.info(f"Step 1 Complete. Found {len(citations)} sources.")
    logger.info("Starting Pipeline: Step 2 - Drafting Tweets...")

    # --- STEP 2: DRAFTING ---
    draft_data = await perform_drafting(transcription, research_notes, citations)
    
    # Combine results
    return {
        "research_notes": research_notes,
        "sources": citations,
        "tweet_drafts": draft_data.get("tweet_drafts", [])
    }

async def _run_research_step(transcription: str) -> dict:
    try:
        response_raw = await perplexity_client.chat.completions.with_raw_response.create(
            model=PERPLEXITY_MODEL,
            messages=[
                {"role": "system", "content": RESEARCH_SYSTEM_PROMPT},
                {"role": "user", "content": f"Analyze this transcription:\n\n{transcription}"}
            ],
        )
        
        response_data = json.loads(response_raw.content)
        content = response_data['choices'][0]['message']['content']
        citations = response_data.get('citations', [])
        
        # Clean markdown
        content = _clean_json_markdown(content)
        data = json.loads(content)
        
        # Add citations to result for internal passing
        data["sources"] = citations
        return data

    except Exception as e:
        logger.error(f"Research Step Failed: {e}")
        raise e

async def perform_drafting(transcription: str, research_notes: str, citations: list = None) -> dict:
    if not google_client:
        logger.error("Gemini Client not initialized.")
        return {"tweet_drafts": ["Error: GEMINI_API_KEY missing."]}

    # Split notes into chunks based on "## " or "### " (Markdown Header 2+)
    # Use a lookahead to split *before* the header, so the header remains part of the chunk.
    chunks = re.split(r'(?m)^(?=#{2,})', research_notes)
    
    # Filter out empty strings and strip whitespace
    valid_chunks = [c.strip() for c in chunks if c.strip()]
    
    logger.info(f"Drafting Step: Processing {len(valid_chunks)} sections.")
    
    async def process_chunk(index, chunk_text):
        try:
            # Use INITIAL_DRAFTING_PROMPT for the first chunk, DRAFTING_SYSTEM_PROMPT for others
            system_prompt = INITIAL_DRAFTING_PROMPT if index == 0 else DRAFTING_SYSTEM_PROMPT
            
            logger.info(f"Processing Chunk {index} (Length: {len(chunk_text)})")
            
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
            
            logger.info(f"Chunk {index} Raw Response: {response.text}")
            
            # response.text is already a JSON string thanks to response_mime_type
            data = json.loads(response.text)
            drafts = data.get("tweet_drafts", [])
            
            if not drafts:
                logger.warning(f"Chunk {index}: No 'tweet_drafts' found in response.")
                
            return drafts
            
        except Exception as e:
            logger.error(f"Error processing chunk {index}: {e}")
            return []

    # Run all chunks in parallel
    results = await asyncio.gather(*(process_chunk(i, chunk) for i, chunk in enumerate(valid_chunks)))
    
    # Flatten results
    all_tweets = []
    for res in results:
        all_tweets.extend(res)
        
    # --- CITATION REPLACEMENT LOGIC ---
    if citations:
        logger.info(f"Replacing citations in {len(all_tweets)} tweets...")
        def replace_citation(match):
            try:
                # match.group(1) is the number N in [N]
                idx = int(match.group(1)) - 1 # 0-based index
                if 0 <= idx < len(citations):
                    return f"({citations[idx]})"
                return match.group(0) # Return original [N] if out of bounds
            except Exception:
                return match.group(0)

        processed_tweets = []
        for tweet in all_tweets:
            # Replace [N] with (URL)
            new_tweet = re.sub(r'\[(\d+)\]', replace_citation, tweet)
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
