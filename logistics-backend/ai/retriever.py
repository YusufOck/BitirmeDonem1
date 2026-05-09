import os
import glob
from typing import List, Dict, Any
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

KNOWLEDGE_BASE_DIR = os.path.join(os.path.dirname(__file__), "knowledge_base")

class DocumentChunk:
    def __init__(self, filename: str, content: str):
        self.filename = filename
        self.content = content.strip()

class MinimalRetriever:
    def __init__(self):
        self.chunks: List[DocumentChunk] = []
        self.vectorizer = TfidfVectorizer(stop_words='english')
        self.tfidf_matrix = None
        self._load_and_chunk()

    def _load_and_chunk(self):
        """Loads all markdown files and chunks them by paragraphs."""
        if not os.path.exists(KNOWLEDGE_BASE_DIR):
            return
            
        md_files = glob.glob(os.path.join(KNOWLEDGE_BASE_DIR, "*.md"))
        for file_path in md_files:
            filename = os.path.basename(file_path)
            with open(file_path, "r", encoding="utf-8") as f:
                text = f.read()
            
            # Simple paragraph chunking
            paragraphs = [p for p in text.split("\n\n") if len(p.strip()) > 20]
            for p in paragraphs:
                self.chunks.append(DocumentChunk(filename, p))
        
        if self.chunks:
            corpus = [chunk.content for chunk in self.chunks]
            self.tfidf_matrix = self.vectorizer.fit_transform(corpus)

    def retrieve(self, query: str, top_k: int = 3) -> List[Dict[str, Any]]:
        """Returns top_k relevant chunks based on cosine similarity."""
        if not self.chunks or self.tfidf_matrix is None:
            return []
            
        query_vec = self.vectorizer.transform([query])
        similarities = cosine_similarity(query_vec, self.tfidf_matrix).flatten()
        
        top_indices = similarities.argsort()[-top_k:][::-1]
        
        results = []
        for idx in top_indices:
            score = float(similarities[idx])
            if score > 0.05: # Minimal threshold
                results.append({
                    "filename": self.chunks[idx].filename,
                    "content": self.chunks[idx].content,
                    "score": round(score, 4)
                })
        
        return results

# Singleton instance
retriever = MinimalRetriever()

def retrieve_context(query: str, top_k: int = 3) -> List[Dict[str, Any]]:
    return retriever.retrieve(query, top_k)
