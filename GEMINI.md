# Project Overview

This project implements a hybrid **Client-Server Automation Workflow** to convert TikTok videos into text (for repurposing as Tweets). It leverages a split architecture to combine local persistence/management with cloud-based heavy computing.

## Architecture

The system consists of two distinct components:

1.  **Local Receiver (The Control Center):**
    *   A **FastAPI** web application running locally on your machine.
    *   **Features:**
        *   **Dashboard:** A simple UI to input TikTok URLs and view transcription history.
        *   **Persistence:** Stores video links, status, and transcriptions in a local SQLite database (`database.db`).
        *   **Orchestration:** Manages the communication with the remote transcription server.
    *   **Tech Stack:** Python 3, FastAPI, Jinja2 (Templates), SQLAlchemy (SQLite), `uv` (dependency management).

2.  **Remote Transcriber (The Compute Engine):**
    *   A **Google Colab** instance running `Transcribe_Audio_With_Whisper.ipynb`.
    *   **Features:**
        *   Acts as an API server exposing a `/transcribe` endpoint.
        *   Uses **OpenAI's Whisper** model on Colab's T4 GPUs for fast transcription.
        *   Uses **pyngrok** to tunnel the localhost server to a public URL accessible by the Local Receiver.
    *   **Tech Stack:** Python 3, FastAPI, OpenAI Whisper, yt-dlp, pyngrok, ffmpeg.

# Workflow

1.  **User** pastes a TikTok link into the Local Receiver Dashboard.
2.  **Local Receiver** saves the link to the database as "Pending".
3.  **Local Receiver** sends the link via HTTP POST to the **Remote Transcriber** (via the Ngrok URL).
4.  **Remote Transcriber** downloads the video audio and transcribes it using Whisper.
5.  **Remote Transcriber** returns the text.
6.  **Local Receiver** updates the database with the text and marks the status as "Transcribed".

# Building and Running

## 1. Local Receiver Setup

This component runs on your machine and manages the workflow.

**Prerequisites:**
*   `uv` installed (Python package and project manager).

**Installation:**
```bash
# Initialize and install dependencies
uv sync
```

**Running the Server:**
```bash
uv run uvicorn receiver:app --reload
```
*   Access the dashboard at: `http://127.0.0.1:8000`

## 2. Remote Transcriber Setup

This component runs on Google Colab to utilize free GPU resources.

1.  Upload `Transcribe_Audio_With_Whisper.ipynb` to [Google Colab](https://colab.research.google.com/).
2.  Set the Runtime Type to **T4 GPU** (Runtime > Change runtime type > T4 GPU).
3.  **Run All Cells.**
    *   The notebook will install dependencies, load the model, and start the FastAPI server.
    *   The final cell output will display a public **Ngrok URL** (e.g., `https://xxxx-xx-xx.ngrok-free.app`).

## 3. Connecting the Two

1.  Copy the **Ngrok URL** from the Colab notebook output.
2.  Go to your Local Receiver Dashboard (`http://127.0.0.1:8000`).
3.  Paste the URL into the **"Colab API URL"** field and click **"Update URL"**.
4.  You are now ready to transcribe videos.

# Key Files

*   **`receiver.py`**: The main local FastAPI application file. Handles routes, DB logic, and background tasks.
*   **`Transcribe_Audio_With_Whisper.ipynb`**: The remote server implementation. Contains the Whisper inference logic and Ngrok tunneling.
*   **`database.py`**: SQLAlchemy database models and configuration.
*   **`templates/index.html`**: The Jinja2 HTML template for the local dashboard.
*   **`pyproject.toml` / `uv.lock`**: Project dependencies and configuration managed by `uv`.