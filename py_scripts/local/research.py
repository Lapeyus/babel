import time
from google import genai
from dotenv import load_dotenv
import os

load_dotenv()
    
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

client = genai.Client(api_key=GEMINI_API_KEY)

interaction = client.interactions.create(
    input="Research this book Nexus: a brief history of information networks from the Stone Age to AI",
    agent='deep-research-pro-preview-12-2025',
    background=True
)

print(f"Research started: {interaction.id}")

while True:
    interaction = client.interactions.get(interaction.id)
    if interaction.status == "completed":
        print(interaction.outputs[-1].text)
        break
    elif interaction.status == "failed":
        print(f"Research failed: {interaction.error}")
        break
    print(f"Research status: {interaction.status}")
    time.sleep(10)