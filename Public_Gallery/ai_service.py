import os
from google import genai
from google.genai import types

def get_ai_response(prompt):
    api_key = os.environ.get("GEMINI_API_KEY")
    # Failsafe: Agar key missing hai toh app crash nahi hogi, fallback caption degi.
    if not api_key:
        return "Enjoying the little things! ✨ #vibes #trending"
    
    try:
        client = genai.Client(api_key=api_key)
        
        # System instruction to support Hinglish, Hindi, and English seamlessly
        system_instruction = (
            "You are an AI for Apna Gallery. "
            "Write highly engaging social media captions, short shayaris, or answers. "
            "IMPORTANT: Reply in the exact same language as the user's prompt (Hinglish, Hindi, or English). "
            "Always include 3 trending hashtags. Keep it concise, friendly, and natural."
        )
        
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.7,
            ),
        )
        return response.text.replace('"', '').replace("'", "")
    except Exception as e:
        print(f"AI Service Error: {e}")
        # Ultimate Failsafe: Agar internet ya AI down hai, toh upload mat roko!
        return "Epic moment! 🔥 #amazing #life"
