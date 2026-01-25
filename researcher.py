import os
import json
import logging
import httpx
from dotenv import load_dotenv
from openai import AsyncOpenAI
from prompts import RESEARCH_SYSTEM_PROMPT, DRAFTING_SYSTEM_PROMPT

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")

if not PERPLEXITY_API_KEY:
    logger.warning("PERPLEXITY_API_KEY is not set. Research features will fail.")

# Custom HTTP client to pass WAF/Cloudflare checks
# Some APIs block default python-httpx User-Agents
custom_http_client = httpx.AsyncClient(
    headers={"User-Agent": "TikTok-Automation/1.0"},
    timeout=60.0
)

client = AsyncOpenAI(
    api_key=PERPLEXITY_API_KEY,
    base_url="https://api.perplexity.ai",
    http_client=custom_http_client
)

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
    draft_data = await _run_drafting_step(transcription, research_notes)
    
    # Combine results
    return {
        "research_notes": research_notes,
        "sources": citations,
        "tweet_drafts": draft_data.get("tweet_drafts", [])
    }

async def _run_research_step(transcription: str) -> dict:
    try:
        response_raw = await client.chat.completions.with_raw_response.create(
            model="sonar-deep-research",
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
        # Return empty safe defaults if research fails, so we can maybe still try to draft?
        # Or re-raise to fail the whole task. Let's re-raise to be safe.
        raise e

async def _run_drafting_step(transcription: str, research_notes: str) -> dict:
    try:
        user_content = (
            f"--- TRANSCRIPTION ---\n{transcription}\n\n"
            f"--- RESEARCH NOTES ---\n{research_notes}\n\n"
            "Generate the tweets now."
        )

        response = await client.chat.completions.create(
            model="sonar-pro", # Faster, good for creative writing
            messages=[
                {"role": "system", "content": DRAFTING_SYSTEM_PROMPT},
                {"role": "user", "content": user_content}
            ],
        )
        
        content = response.choices[0].message.content
        content = _clean_json_markdown(content)
        
        return json.loads(content)

    except Exception as e:
        logger.error(f"Drafting Step Failed: {e}")
        return {"tweet_drafts": ["Error generating drafts."]}

def _clean_json_markdown(text: str) -> str:
    """Helper to strip ```json and ``` code blocks."""
    if text.startswith("```json"):
        return text.replace("```json", "").replace("```", "")
    elif text.startswith("```"):
        return text.replace("```", "")
    return text.strip()
