import os
import requests

def get_ai_response(prompt):
    api_key = os.environ.get("GEMINI_API_KEY")
    
    if not api_key or api_key.strip() == "":
        return "⚠️ System Alert: API Key missing."
        
    try:
        api_key = api_key.strip()
        
        # 💥 THE ULTIMATE ANTI-TRANSLATOR TRICK 💥
        # chr(45) automatically generates a '-' symbol. No browser can corrupt this!
        dash = chr(45)
        model_id = "gemini" + dash + "1.5" + dash + "flash"
        
        url = "https://generativelanguage.googleapis.com/v1beta/models/" + model_id + ":generateContent?key=" + api_key
        
        headers = {'Content-Type': 'application/json'}
        
        # Cool Gen-Z Personality
        personality = "You are VibeX AI, an advanced, cool AI assistant. Speak in a friendly mix of English and Indian Gen-Z Hinglish (like 'kya haal hai bhai?', 'this is lit 🔥'). Keep answers concise.\n\nUser: "
        
        data = {
            "contents": [{"parts": [{"text": personality + prompt}]}]
        }
        
        response = requests.post(url, headers=headers, json=data)
        
        if response.status_code == 200:
            return response.json()['candidates'][0]['content']['parts'][0]['text']
        else:
            return f"🚨 Render API Status {response.status_code}: {response.text}"
            
    except Exception as e:
        return f"🚨 Code Error: {str(e)}"
