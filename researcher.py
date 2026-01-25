import os
import json
import logging
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")

if not PERPLEXITY_API_KEY:
    logger.warning("PERPLEXITY_API_KEY is not set. Research features will fail.")

client = AsyncOpenAI(
    api_key=PERPLEXITY_API_KEY,
    base_url="https://api.perplexity.ai"
)

async def perform_research(transcription: str) -> dict:
    """
    Analyzes the transcription using Perplexity Sonar API.
    Returns a dict with 'research_notes', 'tweet_drafts', and 'sources'.
    """
    if not PERPLEXITY_API_KEY:
        raise ValueError("Perplexity API Key is missing.")

    logger.info("Starting Perplexity research...")
    logger.info(f"Input transcription length: {len(transcription)} characters")

    system_prompt = (
        "You are an expert researcher and social media strategist. "
        "Your task is to analyze the provided video transcription, verify any claims, "
        "provide additional context/background (Deep Research), and generate 3 viral tweet variations. "
        "\n\n"
        "You MUST return the response in strict JSON format with the following schema:\n"
        "{\n"
        '  "research_notes": "A detailed summary of fact-checks, context, and background info. Markdown is supported.",\n'
        '  "tweet_drafts": ["Tweet 1...", "Tweet 2...", "Tweet 3..."]\n'
        "}\n"
        "Do not include any text outside the JSON object."
    )

    try:
        logger.info(f"Sending request to Perplexity API (model='sonar-deep-research')...")
        
        # Use with_raw_response to access custom fields like 'citations'
        response_raw = await client.chat.completions.with_raw_response.create(
            model="sonar-deep-research", # Optimized for deep research
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Analyze this transcription:\n\n{transcription}"}
            ],
        )
        
        # Parse the raw JSON body
        response_data = json.loads(response_raw.content)
        
        # Extract content
        content = response_data['choices'][0]['message']['content']
        
        # Extract citations
        citations = response_data.get('citations', [])
        logger.info(f"Found {len(citations)} citations.")
        
        logger.info(f"Received response from Perplexity. Raw content preview: {content[:500]}...")
        
        # Clean up potential markdown code blocks if the model adds them
        if content.startswith("```json"):
            content = content.replace("```json", "").replace("```", "")
        elif content.startswith("```"):
            content = content.replace("```", "")
            
        data = json.loads(content)
        logger.info("Successfully parsed JSON response.")
        
        # Validate keys
        if "research_notes" not in data or "tweet_drafts" not in data:
            raise ValueError("Missing keys in API response.")
            
        # Add citations to the result
        data["sources"] = citations
        
        return data

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse JSON from Perplexity: {e}")
        # logger.error(f"Raw content: {content}") # content might not be defined if raw parse fails
        raise
    except Exception as e:
        logger.error(f"Perplexity API error: {e}")
        raise
