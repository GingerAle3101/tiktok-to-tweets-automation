from dotenv import load_dotenv
import os

load_dotenv()
key = os.getenv("PERPLEXITY_API_KEY")

if key:
    print(f"Key loaded: Yes")
    print(f"Key length: {len(key)}")
    print(f"Key start: {key[:5]}...")
    if key.strip() != key:
        print("WARNING: Key has leading/trailing whitespace!")
else:
    print("Key loaded: NO")
