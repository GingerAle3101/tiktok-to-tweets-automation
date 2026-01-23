from fastapi import FastAPI, Request, Form, Depends, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from database import SessionLocal, engine, Base, Video, init_db
import requests
import logging

# Initialize DB
init_db()

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# Global state for Colab URL (in-memory for now, could be DB backed)
COLAB_API_URL = ""

# Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def send_to_colab(video_id: int, tiktok_url: str, db: Session):
    """
    Background task to send the link to the Colab instance for transcription.
    """
    global COLAB_API_URL
    if not COLAB_API_URL:
        print(f"No Colab URL set. Skipping transcription for video {video_id}")
        return

    print(f"Sending video {video_id} to Colab: {COLAB_API_URL}")
    
    try:
        # Construct the full endpoint URL
        endpoint = f"{COLAB_API_URL.rstrip('/')}/transcribe"
        
        # Send request
        response = requests.post(endpoint, json={"url": tiktok_url}, timeout=300) # 5 min timeout
        
        # Re-fetch video to ensure we have a fresh session attached object if needed, 
        # but here we just need to update it.
        # We need a new DB session for the background thread usually, 
        # but FastAPI BackgroundTasks runs in the same loop context often, 
        # safest to create a new session here.
        bg_db = SessionLocal()
        video = bg_db.query(Video).filter(Video.id == video_id).first()
        
        if response.status_code == 200:
            data = response.json()
            video.transcription = data.get("text", "")
            video.status = "Transcribed"
        else:
            video.status = "Error"
            video.transcription = f"Colab Error: {response.status_code} - {response.text}"
            
        bg_db.commit()
        bg_db.close()
        
    except Exception as e:
        print(f"Failed to communicate with Colab: {e}")
        bg_db = SessionLocal()
        video = bg_db.query(Video).filter(Video.id == video_id).first()
        video.status = "Error"
        video.transcription = f"Connection Failed: {str(e)}"
        bg_db.commit()
        bg_db.close()

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request, db: Session = Depends(get_db)):
    videos = db.query(Video).order_by(Video.created_at.desc()).all()
    return templates.TemplateResponse("index.html", {
        "request": request, 
        "videos": videos,
        "colab_url": COLAB_API_URL
    })

@app.post("/set-colab-url")
async def set_colab_url(colab_url: str = Form(...)):
    global COLAB_API_URL
    COLAB_API_URL = colab_url
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
    background_tasks.add_task(send_to_colab, new_video.id, new_video.url, db)
    
    return RedirectResponse(url="/", status_code=303)

@app.post("/retry/{video_id}")
async def retry_video(
    video_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    video = db.query(Video).filter(Video.id == video_id).first()
    if video:
        video.status = "Pending"
        db.commit()
        background_tasks.add_task(send_to_colab, video.id, video.url, db)
    
    return RedirectResponse(url="/", status_code=303)
