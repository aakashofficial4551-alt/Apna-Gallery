import os
import requests

def get_ai_response(prompt):
    api_key = os.environ.get("GEMINI_API_KEY")
    
    if not api_key or api_key.strip() == "":
        return "⚠️ System Alert: Render Environment mein Gemini API Key missing hai."
        
    try:
        api_key = api_key.strip()
        
        # 💥 THE REAL FIX: URL mein officially 'gemini-pro' set kar diya hai 💥
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent?key={api_key}"
        
        headers = {'Content-Type': 'application/json'}
        
        # The Gen-Z Hinglish Personality
        personality = "You are VibeX AI, an advanced, cool AI assistant built into the Apna Gallery app. Speak in a friendly mix of English and Indian Gen-Z Hinglish (like 'kya haal hai bhai?', 'this is lit 🔥', 'mast lag raha hai'). Keep answers concise and helpful.\n\nUser says: "
        final_prompt = personality + prompt
        
        # Google ka official data format
        data = {
            "contents": [{
                "parts": [{"text": final_prompt}]
            }]
        }
        
        # Seedha Google ke server pe hit!
        response = requests.post(url, headers=headers, json=data)
        
        # Agar Google ne success (200) diya
        if response.status_code == 200:
            result = response.json()
            return result['candidates'][0]['content']['parts'][0]['text']
        else:
            # Agar koi error aaya toh seedha Google ka exact error dikhayega
            return f"🚨 API Fault Details: HTTP {response.status_code} - {response.text}"
            
    except Exception as e:
        print("Gemini API Error:", e)
        return f"🚨 API Fault Details: {str(e)}"
