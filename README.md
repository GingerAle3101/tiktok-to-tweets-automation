# TikTok to Tweets Automation

A hybrid Client-Server automation tool to convert TikTok videos into viral tweets.

## Architecture

- **Local Receiver:** FastAPI app (runs on your machine) to manage the dashboard and database.
- **Deep Researcher:** Integrated Perplexity AI module for fact-checking and generating viral drafts.
- **Remote Transcriber:** Google Colab notebook (uses GPU) for Whisper-based transcription.

## Setup

### 1. Prerequisites
- Python 3.11+
- `uv` (Project manager)
- Perplexity API Key

### 2. Configuration
Create a `.env` file in the root directory:
```bash
cp .env.example .env
```
Edit `.env` and add your API key:
```
PERPLEXITY_API_KEY=pplx-xxxxxxxxxxxxxxxxxxxx
```

### 3. Installation
```bash
uv sync
```

## Running the App

Start the local server:
```bash
uv run uvicorn receiver:app --reload --port 8001
```

*Note: If you encounter "command not found" errors with `uvicorn`, use the robust module syntax:*
```bash
uv run python -m uvicorn receiver:app --reload --port 8001
```

Open [http://127.0.0.1:8001](http://127.0.0.1:8001) in your browser.

## Workflow

1.  **Start Colab:** Open `Transcribe_Audio_With_Whisper.ipynb` in Google Colab, run all cells, and copy the Ngrok URL.
2.  **Connect:** Paste the Ngrok URL into the local dashboard.
3.  **Add Video:** Paste a TikTok link.
    - The system will transcribe it (via Colab).
    - Then, it will use Perplexity to research context and draft tweets.
4.  **Review:** See the drafts, notes, and citations in the UI.

## Troubleshooting

### iCloud / Cloud Drive Sync Issues
If your project is located in an iCloud-synced folder (e.g., `Documents` or `Desktop`), you may encounter errors like `ModuleNotFoundError` or `FileNotFoundError` due to storage optimization offloading virtual environment files.

**Solution:** Use the included start script, which creates a safe environment outside the synced folder:

```bash
chmod +x start.sh
./start.sh
```
