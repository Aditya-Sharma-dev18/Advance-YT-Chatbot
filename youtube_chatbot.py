# youtube_chatbot.py - COMPLETE FIXED VERSION

import os
import json
import asyncio
from typing import List, Dict, Optional
from sentence_transformers import SentenceTransformer
from pinecone import Pinecone, ServerlessSpec
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough
import logging
import yt_dlp
import re
import requests

logger = logging.getLogger(__name__)

# API Keys
PINECONE_API_KEY = "pcsk_2afFE5_MZjAHxYnBaz9fMCtPQmNNgJiAXnSjnsijVmUmcKJh57GDF9sqkMM6H9jNzavTHa"
GROQ_API_KEY = "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"
PINECONE_ENVIRONMENT = "us-east-1"
PINECONE_INDEX_NAME = "youtube-rag"
VECTOR_DIMENSION = 384

class SimpleEmbeddings:
    def __init__(self):
        self.model = SentenceTransformer('all-MiniLM-L6-v2')
    
    def embed_query(self, text):
        return self.model.encode(text).tolist()

class VideoProcessor:
    def __init__(self):
        self.embeddings = SimpleEmbeddings()
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=150
        )
        self.pc = Pinecone(api_key=PINECONE_API_KEY)
        self.index_name = PINECONE_INDEX_NAME
        self._ensure_index()
    
    def _ensure_index(self):
        try:
            existing_indexes = [index.name for index in self.pc.list_indexes()]
            if self.index_name not in existing_indexes:
                self.pc.create_index(
                    name=self.index_name,
                    dimension=VECTOR_DIMENSION,
                    metric="cosine",
                    spec=ServerlessSpec(cloud="aws", region=PINECONE_ENVIRONMENT)
                )
                logger.info(f"✅ Created index: {self.index_name}")
        except Exception as e:
            logger.error(f"Error creating index: {e}")
    
    async def process_video(self, video_id: str, youtube_url: str = None) -> bool:
        try:
            if not youtube_url:
                youtube_url = f"https://www.youtube.com/watch?v={video_id}"
            
            logger.info(f"📥 Fetching transcript for video: {video_id}")
            
            # yt-dlp se transcript fetch karo
            ydl_opts = {
                'writesubtitles': True,
                'writeautomaticsub': True,
                'subtitleslangs': ['en'],
                'skip_download': True,
                'quiet': True,
                'no_warnings': True,
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(youtube_url, download=False)
                
                # Transcript nikaalo
                subtitles = info.get('subtitles', {})
                automatic_captions = info.get('automatic_captions', {})
                
                transcript_text = ""
                
                # Pehle manual subtitles try karo
                if 'en' in subtitles:
                    transcript_data = subtitles['en']
                    for item in transcript_data:
                        if item.get('ext') == 'vtt' or item.get('ext') == 'srt':
                            transcript_text = self._fetch_subtitle(item['url'])
                            break
                
                # Agar manual nahi mila toh automatic captions
                if not transcript_text and 'en' in automatic_captions:
                    transcript_data = automatic_captions['en']
                    for item in transcript_data:
                        if item.get('ext') == 'vtt' or item.get('ext') == 'srt':
                            transcript_text = self._fetch_subtitle(item['url'])
                            break
                
                if not transcript_text:
                    logger.error(f"❌ No transcript found for video {video_id}")
                    return False
                
                # Clean transcript
                transcript_text = re.sub(r'<[^>]+>', '', transcript_text)
                transcript_text = re.sub(r'\d+\n\d{2}:\d{2}:\d{2}\.\d{3} --> \d{2}:\d{2}:\d{2}\.\d{3}', '', transcript_text)
                transcript_text = re.sub(r'\n+', '\n', transcript_text).strip()
                
                # Split into chunks
                chunks = self.text_splitter.split_text(transcript_text)
                logger.info(f"📄 Split into {len(chunks)} chunks")
                
                # Store in Pinecone
                index = self.pc.Index(self.index_name)
                vectors = []
                
                for i, chunk in enumerate(chunks):
                    vector = self.embeddings.embed_query(chunk)
                    vectors.append({
                        "id": f"{video_id}_{i}",
                        "values": vector,
                        "metadata": {"text": chunk, "video_id": video_id}
                    })
                
                batch_size = 100
                for i in range(0, len(vectors), batch_size):
                    batch = vectors[i:i+batch_size]
                    index.upsert(vectors=batch, namespace=video_id)
                
                logger.info(f"✅ Successfully indexed video: {video_id} ({len(chunks)} chunks)")
                return True
                
        except Exception as e:
            logger.error(f"❌ Error processing video {video_id}: {e}")
            return False
    
    def _fetch_subtitle(self, url):
        """Fetch subtitle content from URL"""
        try:
            response = requests.get(url, timeout=30)
            if response.status_code == 200:
                return response.text
        except:
            pass
        return ""

class RAGSystem:
    def __init__(self):
        self.processor = VideoProcessor()
        self.embeddings = self.processor.embeddings
        self.pc = self.processor.pc
        self.index_name = self.processor.index_name
        
        # ✅ FIXED: New working model
        self.llm = ChatGroq(
            api_key=GROQ_API_KEY,
            model="openai/gpt-oss-120b",  # Changed from mixtral
            temperature=0
        )
        
        self.prompt = ChatPromptTemplate.from_template("""
You are an AI assistant answering questions about a YouTube video.

Answer ONLY using the provided transcript context.

If the answer cannot be found in the context,
say that the information is not available in the video.

Be accurate and concise.

Context:
{context}

Question:
{question}

Answer:
""")
    
    async def get_answer(self, video_id: str, question: str, chat_history: List[Dict] = None) -> str:
        try:
            index = self.pc.Index(self.index_name)
            query_vector = self.embeddings.embed_query(question)
            
            results = index.query(
                vector=query_vector,
                top_k=5,
                namespace=video_id,
                include_metadata=True
            )
            
            context = ""
            for match in results.matches:
                if match.metadata and match.metadata.get("text"):
                    context += match.metadata["text"] + "\n\n"
            
            if not context:
                return "I couldn't find any relevant information about that in the video."
            
            chain = (
                {"context": lambda x: context, "question": RunnablePassthrough()}
                | self.prompt
                | self.llm
                | StrOutputParser()
            )
            
            return chain.invoke(question)
        except Exception as e:
            logger.error(f"Error getting answer: {e}")
            return f"Sorry, I couldn't answer that question. Error: {str(e)}"
    
    async def check_video_indexed(self, video_id: str) -> bool:
        try:
            index = self.pc.Index(self.index_name)
            stats = index.describe_index_stats()
            if hasattr(stats, 'namespaces') and video_id in stats.namespaces:
                return stats.namespaces[video_id].vector_count > 0
            return False
        except Exception as e:
            logger.error(f"Error checking video index: {e}")
            return False

# Global functions
async def process_video(video_id: str, youtube_url: str = None) -> bool:
    processor = VideoProcessor()
    return await processor.process_video(video_id, youtube_url)

async def get_answer(video_id: str, question: str, chat_history: List[Dict] = None) -> str:
    rag = RAGSystem()
    return await rag.get_answer(video_id, question, chat_history)

async def check_video_indexed(video_id: str) -> bool:
    rag = RAGSystem()
    return await rag.check_video_indexed(video_id)

async def get_video_chunk_count(video_id: str) -> int:
    return 0

async def delete_video_from_index(video_id: str) -> bool:
    try:
        rag = RAGSystem()
        index = rag.pc.Index(rag.index_name)
        index.delete(delete_all=True, namespace=video_id)
        return True
    except Exception as e:
        logger.error(f"Error deleting video: {e}")
        return False

async def index_video(video_id: str, youtube_url: str = None) -> bool:
    return await process_video(video_id, youtube_url)