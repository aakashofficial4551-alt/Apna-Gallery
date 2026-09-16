import os
from google import genai
from google.genai import types

def get_ai_response(prompt):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return "AI Assistant is offline (API key missing)."
    
    try:
        client = genai.Client(api_key=api_key)
        
        # System instruction to support Hinglish, Hindi, and English seamlessly
        system_instruction = (
            "You are an AI assistant for a high-performance social media platform called Apna Gallery. "
            "Detect the user's language style (Hinglish, Hindi, or English) and reply back naturally in that exact same style or language. "
            "Keep responses friendly, helpful, concise, and engaging."
        )
        
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.7,
            ),
        )
        return response.text
    except Exception as e:
        print(f"AI Service Error: {e}")
        return "AI Assistant is temporarily unavailable."
