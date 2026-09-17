import os
import google.generativeai as genai

def get_ai_response(prompt):
    api_key = os.environ.get("GEMINI_API_KEY")
    
    if not api_key or api_key.strip() == "":
        return "⚠️ System Alert: Render Environment mein Gemini API Key missing hai."
        
    try:
        genai.configure(api_key=api_key.strip())
        
        # 💥 MASTER FIX: Universally supported 'gemini-pro' use kar rahe hain 💥
        model = genai.GenerativeModel('gemini-pro')
        
        # Hinglish Gen-Z Personality
        personality = "You are VibeX AI, an advanced, cool AI assistant built into the Apna Gallery app. Speak in a friendly mix of English and Indian Gen-Z Hinglish (like 'kya haal hai bhai?', 'this is lit 🔥', 'mast lag raha hai'). Keep answers concise and helpful.\n\n"
        final_prompt = personality + "User says: " + prompt
        
        response = model.generate_content(final_prompt)
        return response.text
        
    except Exception as e:
        print("Gemini API Error:", e)
        return f"🚨 API Fault Details: {str(e)}"
