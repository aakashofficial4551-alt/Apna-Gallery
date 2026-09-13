def get_ai_response(query):
    query = query.lower()
    
    if any(word in query for word in ['hi', 'hello', 'hey', 'namaste']):
        return "Namaste Agent! SOCHO KYA HOGA vault me aapka swagat hai. Main aapki kya sahayta karu?"
    elif any(word in query for word in ['kaise ho', 'how are you', 'sab thik']):
        return "Main ekdum badiya hu! System 100% operational hai. Aap batao, aaj vault me kya explore karna hai?"
    elif any(word in query for word in ['naam', 'name', 'who are you', 'tum kaun ho']):
        return "Mera naam Vault Guardian AI hai. Main is secret digital vault ki security aur intel ko monitor karta hu."
    elif any(word in query for word in ['mystery', 'secret', 'password']):
        return "Hmm, secret ki talash? Mystery room ka password 'SOCHO' hai. Kisi aur ko mat batana!"
    elif any(word in query for word in ['radhe', 'krishna', 'bhagwan']):
        return "RADHE RADHE! 🙏 Bhagwan ki kripa se aapka yahan aana mangalmay ho."
    elif any(word in query for word in ['upload', 'photo', 'video', 'content']):
        return "Aap dashboard se kisi bhi category (Photo, Video, Music, Docs, Shayari) me jaakar content upload kar sakte hain. Admin approval ke baad wo sabko dikhega!"
    elif any(word in query for word in ['game', 'snake', 'arcade']):
        return "Aap dashboard se 'The Arcade' me jakar retro snake game khel sakte hain!"
    else:
        return "Yeh ek aisi information hai jo abhi classified hai... Socho kya hoga agar ye raaz khul gaya?"
