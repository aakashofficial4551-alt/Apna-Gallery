import os
import requests

def get_ai_response(prompt):
    api_key = os.environ.get("GEMINI_API_KEY")
    
    if not api_key or api_key.strip() == "":
        return "⚠️ System Alert: Render Environment mein Gemini API Key missing hai."
        
    try:
        api_key = api_key.strip()
        
        # 💥 ANTI-TRANSLATOR & ANTI-CACHE FIX 💥
        # String ko tod diya taaki browser translate karke space na ghusa paye
        model_name = "gemini" + "-" + "1.5" + "-" + "flash"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        
        headers = {'Content-Type': 'application/json'}
        personality = "You are VibeX AI, an advanced AI assistant. Speak in a friendly mix of English and Indian Gen-Z Hinglish. User says: "
        
        data = {
            "contents": [{
                "parts": [{"text": personality + prompt}]
            }]
        }
        
        # Seedha Google ko request
        response = requests.post(url, headers=headers, json=data)
        
        if response.status_code == 200:
            result = response.json()
            return result['candidates'][0]['content']['parts'][0]['text']
        else:
            return f"🚨 Google API Error {response.status_code}: {response.text}"
            
    except Exception as e:
        print("Gemini Error:", e)
        return f"🚨 System Error: {str(e)}"
