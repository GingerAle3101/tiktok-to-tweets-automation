import sys
import os

print(f"Python executable: {sys.executable}")
print(f"Current working directory: {os.getcwd()}")
print(f"sys.path: {sys.path}")

try:
    import openai
    print(f"OpenAI package location: {openai.__file__}")
    print(f"OpenAI version: {openai.__version__}")
    
    # Try importing the specific missing module
    try:
        from openai import resources
        print("Successfully imported openai.resources")
    except ImportError as e:
        print(f"Failed to import openai.resources: {e}")
        # list directory of openai package
        openai_dir = os.path.dirname(openai.__file__)
        print(f"Contents of openai package dir: {os.listdir(openai_dir)}")

except ImportError as e:
    print(f"Failed to import openai: {e}")

# Check for local shadows
if os.path.exists("openai.py"):
    print("WARNING: Found 'openai.py' in current directory! This shadows the library.")
if os.path.exists("openai") and os.path.isdir("openai"):
    print("WARNING: Found 'openai' directory in current directory! This might shadow the library.")
