from sqlalchemy import create_engine, Column, Integer, String, Text, Boolean, DateTime, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime, timezone

DATABASE_URL = "sqlite:///./database.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

class Video(Base):
    __tablename__ = "videos"

    id = Column(Integer, primary_key=True, index=True)
    url = Column(String, index=True)
    status = Column(String, default="Pending")  # Pending, Transcribed, Error, Researching
    transcription = Column(Text, nullable=True)
    research_notes = Column(Text, nullable=True)
    tweet_drafts = Column(Text, nullable=True)  # Stored as JSON string
    sources = Column(Text, nullable=True)       # Stored as JSON string (list of URLs)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

def check_and_migrate_db():
    """Checks for missing columns and adds them (simple migration)."""
    inspector = inspect(engine)
    # If table doesn't exist, create_all will handle it, so we skip migration check
    if not inspector.has_table("videos"):
        return

    columns = [c['name'] for c in inspector.get_columns('videos')]
    
    with engine.connect() as conn:
        if 'research_notes' not in columns:
            print("Migrating: Adding 'research_notes' column...")
            conn.execute(text("ALTER TABLE videos ADD COLUMN research_notes TEXT"))
        
        if 'tweet_drafts' not in columns:
            print("Migrating: Adding 'tweet_drafts' column...")
            conn.execute(text("ALTER TABLE videos ADD COLUMN tweet_drafts TEXT"))

        if 'sources' not in columns:
            print("Migrating: Adding 'sources' column...")
            conn.execute(text("ALTER TABLE videos ADD COLUMN sources TEXT"))
            
        conn.commit()

def init_db():
    Base.metadata.create_all(bind=engine)
    check_and_migrate_db()
