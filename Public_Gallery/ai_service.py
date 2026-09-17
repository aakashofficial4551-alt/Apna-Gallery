import os
import google.generativeai as genai

def get_ai_response(prompt):
    # API key fetch kar rahe hain aur spaces hata rahe hain
    api_key = os.environ.get("GEMINI_API_KEY")
    
    if not api_key or api_key.strip() == "":
        return "⚠️ System Alert: Render Environment mein Gemini API Key missing hai."
        
    try:
        # Key se spaces clean karke configure kar rahe hain
        genai.configure(api_key=api_key.strip())
        
        # Simple initialization jo har library version par smoothly chalega
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        # 💥 Smart Fix: Personality ko direct prompt ke andar daal diya (Bypasses version errors)
        personality = "You are VibeX AI, an advanced, cool AI assistant built into the Apna Gallery app. Speak in a friendly mix of English and Indian Gen-Z Hinglish. Keep answers concise and helpful.\n\n"
        final_prompt = personality + "User says: " + prompt
        
        # Request bhejna
        response = model.generate_content(final_prompt)
        return response.text
        
    except Exception as e:
        print("Gemini API Error:", e)
        # 💥 DIAGNOSTIC MODE: Ab screen par asli error print hoga! 💥
        return f"🚨 API Fault Details: {str(e)}"
