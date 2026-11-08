import asyncio
import datetime
import os
import warnings

# Suppress ROCm experimental feature warnings
warnings.filterwarnings("ignore", message="Flash Efficient attention")
warnings.filterwarnings("ignore", message="Mem Efficient attention")

# Optional: If you want to explicitly disable the experimental path to be safe
os.environ["TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL"] = "0"

import re
import aiohttp
from sentence_transformers import SentenceTransformer


# Singleton instance holder
_EMBEDDER_INSTANCE = None



class LLM:
    def __init__(self, model_name="google/gemma-3-1b-it", base_url="http://localhost:8000"):
        self.base_url = base_url
        self.headers = {"Content-Type": "application/json"}
        self.model_name = model_name
        self.semaphore = asyncio.Semaphore(50)

        global _EMBEDDER_INSTANCE
        embedding_model_name = 'BAAI/bge-base-en-v1.5'
        local_model_path = os.path.join("Models", "bge-base-en-v1.5")

        if _EMBEDDER_INSTANCE is None:
            if os.path.exists(local_model_path):
                #print(f"Loading Embedding Model from local path: {local_model_path}")
                _EMBEDDER_INSTANCE = SentenceTransformer(local_model_path, device='cpu')
            else:
                #print("Downloading Embedding Model from SentenceTransformer hub...")
                _EMBEDDER_INSTANCE = SentenceTransformer(embedding_model_name, device='cpu')
                #print(f"Saving model locally to {local_model_path} for future runs...")
                _EMBEDDER_INSTANCE.save(local_model_path)
            #print(f"{embedding_model_name} Embedding Model Loaded.")

        self.embedder = _EMBEDDER_INSTANCE

    def get_embedding(self, text: str) -> list[float]:
        """
        Computes the embedding vector for a given text.
        Synchronous because it runs locally on CPU/GPU via PyTorch, not HTTP.
        """
        return self.embedder.encode(text).tolist()

    @staticmethod
    def _parse_gemma_chat_prompt(prompt: str) -> list[dict]:
        """
        Decomposes legacy Gemma-formatted turn strings into standard OpenAI chat messages.
        """
        pattern = r'<start_of_turn>(system|user|model)\s*(.*?)(?=(?:<end_of_turn>|<start_of_turn>|$))'
        matches = re.findall(pattern, prompt, flags=re.DOTALL)
        if not matches:
            return [{"role": "user", "content": prompt.strip()}]
        
        role_map = {'system': 'system', 'user': 'user', 'model': 'assistant'}
        messages = []
        for role, content in matches:
            clean_content = content.replace('<end_of_turn>', '').strip()
            if clean_content:
                messages.append({'role': role_map.get(role, 'user'), 'content': clean_content})
        return messages if messages else [{"role": "user", "content": prompt.strip()}]

    async def generate_text(self, user_prompt: str | list[dict], temperature: float, max_tokens=2048, stop=None) -> str:
        async with self.semaphore:
            # 1. Normalize input to OpenAI chat messages format
            if isinstance(user_prompt, list):
                messages = user_prompt
            elif isinstance(user_prompt, str):
                if "<start_of_turn>" in user_prompt:
                    messages = self._parse_gemma_chat_prompt(user_prompt)
                else:
                    messages = [{"role": "user", "content": user_prompt}]
            else:
                messages = [{"role": "user", "content": str(user_prompt)}]

            stop_tokens = []
            if stop:
                stop_tokens = stop if isinstance(stop, list) else [stop]

            # 2. Determine if we are talking to Ollama or vLLM
            is_ollama = "11434" in self.base_url
            
            # 3. Adjust Payload
            if is_ollama:
                endpoint = f"{self.base_url}/api/chat"
                payload = {
                    "model": self.model_name,
                    "messages": messages,
                    "stream": False,
                    "options": {
                        "num_predict": max_tokens,
                        "temperature": temperature,
                        "num_ctx": 4096
                    }
                }
                if stop_tokens:
                    payload["options"]["stop"] = stop_tokens
            else:
                # vLLM / OpenAI Chat Completions format
                endpoint = f"{self.base_url}/v1/chat/completions"
                payload = {
                    "model": self.model_name,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                }
                if stop_tokens:
                    payload["stop"] = stop_tokens

            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(endpoint, headers=self.headers, json=payload) as resp:
                        resp.raise_for_status()
                        data = await resp.json()
                        
                        # 4. Handle Different Response Structures
                        if is_ollama:
                            answer = data.get('message', {}).get('content', '').strip()
                        else:
                            choice = data['choices'][0]
                            answer = choice.get('message', {}).get('content') or ''
                            # Strip thinking tags if generated by reasoning models
                            answer = re.sub(r'<think>.*?</think>', '', answer, flags=re.DOTALL).strip()

                        # Ensure the logs directory exists
                        #log_file = "Utils/Logs/server_logs.md"
                        #os.makedirs(os.path.dirname(log_file), exist_ok=True)

                        #timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        
                        # Construct the formatted Markdown entry
                        #log_entry = f"""
#**⚙️ Parameters:**
# - `Model`: {self.model_name}
# - `Temperature`: {temperature}
# - `Max Tokens`: {max_tokens}
# - `Timestamp`: {timestamp}
# ---------------------------------------------
# **📥 Query:**
# ```text
# {user_prompt.strip()}
# ```
# ---------------------------------------------
# **📤 Response:**
# ```text
# {answer.strip()}
# ```
# ---------------------------------------------"""
                        
                        # Append the message to the markdown file
                        #with open(log_file, "a", encoding="utf-8") as f:
                            # We strip leading newlines to avoid weird markdown formatting gaps, 
                            # but keep the newline at the end for the next log.
                            #f.write(log_entry + "\n\n")
                        
                        return answer
                    
            except Exception as e:
                #print(f"LLM Error: {e}")
                return ""
        