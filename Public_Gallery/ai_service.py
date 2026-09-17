import os
import google.generativeai as genai

def get_ai_response(prompt):
    api_key = os.environ.get("GEMINI_API_KEY")
    
    # Security Fallback: Agar Admin ne key nahi daali toh warning dega
    if not api_key or api_key.strip() == "":
        return "⚠️ System Alert: Admin ne abhi tak Gemini API Key add nahi ki hai. Please .env file mein GEMINI_API_KEY setup karein."
        
    try:
        # Gemini setup
        genai.configure(api_key=api_key)
        
        # Hum AI ko uski personality de rahe hain!
        system_instruction = """
        You are VibeX AI, an advanced, friendly, and cool AI assistant built into the Apna Gallery app. 
        You speak in a mix of English and Indian Gen-Z Hinglish (e.g., 'Kya haal hai bhai?', 'This is lit 🔥'). 
        Keep your answers concise, helpful, and creative. You are an expert in photography, aesthetic vibes, and coding.
        """
        
        # Using the fast and free Gemini model
        model = genai.GenerativeModel('gemini-1.5-flash', system_instruction=system_instruction)
        
        # Get response
        response = model.generate_content(prompt)
        return response.text
        
    except Exception as e:
        print("Gemini API Error:", e)
        return "Oops! 🛠️ Main thoda overloaded hu ya API me koi issue aagaya hai. Kuch der baad try karo!"
