from fastapi import FastAPI, Request, Form, Depends, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from database import SessionLocal, engine, Base, Video, SystemConfig, init_db
import httpx
import logging
import json
from researcher import perform_research, perform_drafting

# Initialize DB (and migrate if needed)
init_db()

app = FastAPI()
templates = Jinja2Templates(directory="templates")

def from_json(value):
    if not value:
        return []
    try:
        return json.loads(value)
    except:
        return []

templates.env.filters["from_json"] = from_json

# Global state for Colab URL
COLAB_API_URL = ""

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.on_event("startup")
def load_config():
    global COLAB_API_URL
    db = SessionLocal()
    try:
        config = db.query(SystemConfig).filter(SystemConfig.key == "colab_url").first()
        if config:
            COLAB_API_URL = config.value
            logger.info(f"Loaded Colab URL from DB: {COLAB_API_URL}")
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
    finally:
        db.close()

async def run_research_task(video_id: int):
    """
    Background task to perform Deep Research and generate tweets.
    """
    logger.info(f"Starting research for video {video_id}")
    db = SessionLocal()
    video = db.query(Video).filter(Video.id == video_id).first()
    
    if not video or not video.transcription:
        logger.error(f"Video {video_id} not found or missing transcription.")
        db.close()
        return

    try:
        # Perform Research
        results = await perform_research(video.transcription)
        
        # Update DB
        video.research_notes = results.get("research_notes", "")
        # Store drafts as JSON string
        video.tweet_drafts = json.dumps(results.get("tweet_drafts", []))
        # Store sources as JSON string
        video.sources = json.dumps(results.get("sources", []))
        video.status = "Completed"
        
        db.commit()
        logger.info(f"Research completed for video {video_id}")
        
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Research failed for video {video_id}: {error_msg}")
        
        # Handle specific Quota/Auth errors (Cloudflare 401)
        if "401" in error_msg or "Authorization" in error_msg:
             video.status = "Quota_Exceeded"
             video.research_notes = f"Research Failed: Insufficient Perplexity API Quota or Auth Error (401).\n\nDetails: {error_msg[:200]}..."
        else:
             video.status = "Research_Failed"
             video.research_notes = f"Research Failed: {error_msg}"
             
        db.commit()
    finally:
        db.close()

async def run_drafting_task(video_id: int):
    """
    Background task to ONLY generate tweets from existing research notes.
    """
    logger.info(f"Starting drafting for video {video_id}")
    db = SessionLocal()
    video = db.query(Video).filter(Video.id == video_id).first()
    
    if not video or not video.transcription or not video.research_notes:
        logger.error(f"Video {video_id} missing transcription or research notes.")
        db.close()
        return

    try:
        # Parse sources if available
        citations = []
        if video.sources:
            try:
                citations = json.loads(video.sources)
            except Exception as e:
                logger.warning(f"Failed to parse sources for video {video_id}: {e}")

        # Perform Drafting
        results = await perform_drafting(video.transcription, video.research_notes, citations)
        
        # Update DB
        video.tweet_drafts = json.dumps(results.get("tweet_drafts", []))
        video.status = "Completed"
        
        db.commit()
        logger.info(f"Drafting completed for video {video_id}")
        
    except Exception as e:
        logger.error(f"Drafting failed for video {video_id}: {e}")
        video.status = "Error"
        db.commit()
    finally:
        db.close()

async def send_to_colab(video_id: int, tiktok_url: str):
    """
    Background task to send the link to the Colab instance for transcription.
    """
    global COLAB_API_URL
    if not COLAB_API_URL:
        logger.warning(f"No Colab URL set. Skipping transcription for video {video_id}")
        return

    # Clean the URL
    clean_url = COLAB_API_URL.strip().rstrip('/')
    logger.info(f"Sending video {video_id} to Colab: {clean_url}")
    
    db = SessionLocal()
    video = db.query(Video).filter(Video.id == video_id).first()
    
    try:
        endpoint = f"{clean_url}/transcribe"
        logger.info(f"Requesting Endpoint: {endpoint}")
        
        # Increased timeout to 20 minutes for long videos
        # SSL verification disabled to handle Ngrok certificate quirks
        async with httpx.AsyncClient(timeout=1200.0, verify=False) as client:
            response = await client.post(endpoint, json={"url": tiktok_url})
        
        if response.status_code == 200:
            data = response.json()
            video.transcription = data.get("text", "")
            video.status = "Researching"  # New status
            db.commit()
            
            # Chain the next task
            await run_research_task(video_id)
            
        else:
            video.status = "Error"
            video.transcription = f"Colab Error: {response.status_code} - {response.text}"
            db.commit()
            
    except Exception as e:
        logger.error(f"Failed to communicate with Colab for video {video_id}: {type(e).__name__}: {str(e)}")
        video.status = "Error"
        video.transcription = f"Connection Failed: {type(e).__name__} - {str(e)}"
        db.commit()
    finally:
        db.close()

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request, db: Session = Depends(get_db)):
    videos = db.query(Video).order_by(Video.created_at.desc()).all()
    return templates.TemplateResponse("index.html", {
        "request": request, 
        "videos": videos,
        "colab_url": COLAB_API_URL
    })

@app.post("/set-colab-url")
async def set_colab_url(
    colab_url: str = Form(...),
    db: Session = Depends(get_db)
):
    global COLAB_API_URL
    COLAB_API_URL = colab_url
    
    # Save to DB
    config = db.query(SystemConfig).filter(SystemConfig.key == "colab_url").first()
    if not config:
        config = SystemConfig(key="colab_url", value=colab_url)
        db.add(config)
    else:
        config.value = colab_url
    db.commit()
    
    return RedirectResponse(url="/", status_code=303)

@app.post("/add")
async def add_video(
    background_tasks: BackgroundTasks,
    tiktok_url: str = Form(...),
    db: Session = Depends(get_db)
):
    # Save to DB
    new_video = Video(url=tiktok_url)
    db.add(new_video)
    db.commit()
    db.refresh(new_video)
    
    # Trigger transcription in background
    # Note: We don't pass 'db' to the background task anymore
    background_tasks.add_task(send_to_colab, new_video.id, new_video.url)
    
    return RedirectResponse(url="/", status_code=303)

@app.post("/retry/{video_id}")
async def retry_video(
    video_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    video = db.query(Video).filter(Video.id == video_id).first()
    if video:
        # Smart Retry: If we already have the text, don't re-transcribe (saves GPU/Time)
        # Just run the Research/Drafting step.
        if video.transcription and len(video.transcription) > 10:
             logger.info(f"Video {video_id} has transcription. Retrying research only.")
             video.status = "Researching"
             db.commit()
             background_tasks.add_task(run_research_task, video.id)
        else:
            # Full Retry: No text? Start from scratch.
            logger.info(f"Retrying full workflow for video {video_id}")
            video.status = "Pending"
            db.commit()
            background_tasks.add_task(send_to_colab, video.id, video.url)
    
    return RedirectResponse(url="/", status_code=303)

@app.post("/update-research/{video_id}")
async def update_research(
    video_id: int,
    background_tasks: BackgroundTasks,
    research_notes: str = Form(...),
    db: Session = Depends(get_db)
):
    video = db.query(Video).filter(Video.id == video_id).first()
    if video:
        video.research_notes = research_notes
        video.status = "Drafting" # Or Researching, but Drafting is more specific if we had it
        db.commit()
        
        # Trigger drafting
        background_tasks.add_task(run_drafting_task, video.id)
        
    return RedirectResponse(url="/", status_code=303)

@app.post("/delete/{video_id}")
async def delete_video(
    video_id: int,
    db: Session = Depends(get_db)
):
    video = db.query(Video).filter(Video.id == video_id).first()
    if video:
        db.delete(video)
        db.commit()
    
    return RedirectResponse(url="/", status_code=303)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8001)