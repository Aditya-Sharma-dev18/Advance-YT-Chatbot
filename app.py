# app.py - FIXED

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Dict
import os
from dotenv import load_dotenv
import uvicorn
import logging
from datetime import datetime

load_dotenv()

from youtube_chatbot import (
    process_video,
    get_answer,
    check_video_indexed,
    index_video,
    delete_video_from_index
)

async def get_video_chunk_count(video_id: str) -> int:
    return 0

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="YouTube Chatbot API",
    description="API for YouTube video Q&A using RAG",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class QuestionRequest(BaseModel):
    videoId: str
    question: str
    chatHistory: Optional[List[Dict[str, str]]] = []

class IndexVideoRequest(BaseModel):
    videoId: str
    youtubeUrl: Optional[str] = None

class ChatResponse(BaseModel):
    success: bool
    answer: str
    videoId: str
    timestamp: str

class IndexResponse(BaseModel):
    success: bool
    message: str
    videoId: str
    indexed: bool

class VideoStatusResponse(BaseModel):
    videoId: str
    indexed: bool
    chunks: Optional[int] = None

@app.get("/")
async def root():
    return {"message": "YouTube Chatbot API", "version": "1.0.0", "status": "running"}

@app.get("/api/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

@app.post("/api/ask", response_model=ChatResponse)
async def ask_question(request: QuestionRequest):
    try:
        logger.info(f"Received question for video: {request.videoId}")
        is_indexed = await check_video_indexed(request.videoId)
        if not is_indexed:
            await index_video(request.videoId)
        answer = await get_answer(
            video_id=request.videoId,
            question=request.question,
            chat_history=request.chatHistory
        )
        return ChatResponse(
            success=True,
            answer=answer,
            videoId=request.videoId,
            timestamp=datetime.now().isoformat()
        )
    except Exception as e:
        logger.error(f"Error processing question: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/index-video", response_model=IndexResponse)
async def index_video_endpoint(request: IndexVideoRequest, background_tasks: BackgroundTasks):
    try:
        logger.info(f"Starting indexing for video: {request.videoId}")
        background_tasks.add_task(process_video, request.videoId, request.youtubeUrl)
        return IndexResponse(
            success=True,
            message="Video indexing started in background",
            videoId=request.videoId,
            indexed=True
        )
    except Exception as e:
        logger.error(f"Error indexing video: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/check-video/{video_id}", response_model=VideoStatusResponse)
async def check_video_status(video_id: str):
    try:
        is_indexed = await check_video_indexed(video_id)
        chunks = None
        if is_indexed:
            chunks = await get_video_chunk_count(video_id)
        return VideoStatusResponse(
            videoId=video_id,
            indexed=is_indexed,
            chunks=chunks
        )
    except Exception as e:
        logger.error(f"Error checking video status: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/delete-video/{video_id}")
async def delete_video(video_id: str):
    try:
        success = await delete_video_from_index(video_id)
        return {
            "success": success,
            "videoId": video_id,
            "message": "Video deleted successfully" if success else "Video not found"
        }
    except Exception as e:
        logger.error(f"Error deleting video: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=5000, reload=True, log_level="info")