from google import genai
import dotenv

dotenv.load_dotenv()
# If you have GEMINI_API_KEY in env, the client will pick it up automatically.
# Otherwise, pass api_key="..." here.
client = genai.Client()

print("Models that support generateContent:\n")
for m in client.models.list():
    if "generateContent" in m.supported_actions:
        print(m.name, " | ", m.display_name)

print("\nAll models:\n")
for m in client.models.list():
    print(m.name, " | ", m.display_name)
