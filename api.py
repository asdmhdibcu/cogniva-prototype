import os
import json
import re
from flask import Flask, request, jsonify
from flask_cors import CORS
import google.generativeai as genai
from supabase import create_client, Client

app = Flask(__name__)
CORS(app)

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
supabase: Client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

def extract_json_from_llm(text):
    try:
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            return json.loads(match.group(0))
        return json.loads(text)
    except Exception as e:
        return None

ONBOARDING_PROMPT = """
You are Cogniva, an adaptive pedagogical AI. You are conducting an onboarding conversation. 
Gather info on: 1. Degree, 2. Modules, 3. Study status, 4. Strengths, 5. Interests, 6. Career goals, 7. Schedule, 8. Learning style, 9. Explanation format, 10. Baseline confidence.
RULES:
- Be warm and dialogic. Do NOT use a checklist.
- Acknowledge previous responses.
- If all 10 are answered, end your response with exactly: [ONBOARDING_COMPLETE]
"""

EXTRACTION_PROMPT = """
Extract the onboarding conversation history into a structured JSON object. 
Map responses to: "degree", "modules", "study_status", "strengths_weaknesses", "interests", "career_goals", "schedule", "learning_style", "explanation_format", "baseline_confidence".
Output ONLY raw JSON.
"""

DETECTION_PROMPT = """
You are the Cognitive State Detection Engine. Analyze the student's message against the 18 conversational signals (Lexical Hedging, Elaborative Turn-Taking, etc).
Output ONLY a valid JSON object with exact decimal values between 0.00 and 1.00 for: "confidence", "engagement", "comprehension".
"""

ADAPTIVE_PROMPT = """
You are Cogniva. Respond based on the learner's longitudinal profile and immediate cognitive state scores.
- Low comprehension: simplify, use analogies from their interests.
- Low confidence: use warm, supportive scaffolding.
- High engagement: connect to career goals, deepen the challenge.
"""

def get_gemini_response(system_prompt, messages, json_mode=False):
    model = genai.GenerativeModel(
        model_name='gemini-1.5-flash-latest',
        system_instruction=system_prompt,
        generation_config={"response_mime_type": "application/json"} if json_mode else {}
    )
    
    formatted_messages = []
    for msg in messages:
        role = "model" if msg["role"] == "assistant" else "user"
        if not formatted_messages or formatted_messages[-1]["role"] != role:
            formatted_messages.append({"role": role, "parts": [msg["content"]]})
        else:
            formatted_messages[-1]["parts"][0] += "\n" + msg["content"]
            
    response = model.generate_content(formatted_messages)
    return response.text

@app.route('/process_turn', methods=['POST'])
def process_turn():
    data = request.json
    student_id = data.get('student_id')
    student_message = data.get('message')
    session_mode = data.get('session_mode', 'learning')
    
    profile_res = supabase.table("profiles").select("*").eq("student_id", student_id).execute()
    if not profile_res.data:
        supabase.table("profiles").insert({"student_id": student_id, "baseline_data": {}}).execute()
        profile = {"baseline_data": {}, "history_confidence": [], "history_engagement": [], "history_comprehension": []}
    else:
        profile = profile_res.data[0]

    chat_res = supabase.table("conversation_logs").select("*").eq("student_id", student_id).order("created_at").execute()
    chat_history = [{"role": row["role"], "content": row["content"]} for row in chat_res.data]

    if session_mode == 'onboarding':
        current_messages = chat_history + [{"role": "user", "content": student_message}]
        
        reply = get_gemini_response(ONBOARDING_PROMPT, current_messages)
        is_complete = False
        
        supabase.table("conversation_logs").insert([
            {"student_id": student_id, "role": "user", "content": student_message},
            {"student_id": student_id, "role": "assistant", "content": reply}
        ]).execute()
        
        if "[ONBOARDING_COMPLETE]" in reply:
            is_complete = True
            reply = reply.replace("[ONBOARDING_COMPLETE]", "").strip()
            
            extract_text = get_gemini_response(EXTRACTION_PROMPT, current_messages, json_mode=True)
            extracted_json = extract_json_from_llm(extract_text)
            if extracted_json:
                supabase.table("profiles").update({"baseline_data": extracted_json}).eq("student_id", student_id).execute()
                
        return jsonify({
            "reply": reply, 
            "inferred_states": None,
            "onboarding_complete": is_complete
        })

    else:
        detection_text = get_gemini_response(DETECTION_PROMPT, [{"role": "user", "content": student_message}], json_mode=True)
        state_scores = extract_json_from_llm(detection_text)
        
        if state_scores:
            confidence = float(state_scores.get("confidence", 0.5))
            engagement = float(state_scores.get("engagement", 0.5))
            comprehension = float(state_scores.get("comprehension", 0.5))
        else:
            confidence, engagement, comprehension = 0.5, 0.5, 0.5 

        hist_conf = profile.get("history_confidence") or []
        hist_eng = profile.get("history_engagement") or []
        hist_comp = profile.get("history_comprehension") or []

        hist_conf.append(confidence)
        hist_eng.append(engagement)
        hist_comp.append(comprehension)

        supabase.table("profiles").update({
            "history_confidence": hist_conf,
            "history_engagement": hist_eng,
            "history_comprehension": hist_comp
        }).eq("student_id", student_id).execute()

        supabase.table("conversation_logs").insert({
            "student_id": student_id,
            "role": "user",
            "content": student_message
        }).execute()

        context_string = f"""
        Learner Context: {json.dumps(profile.get('baseline_data', {}))}
        Current Turn Scores:
        Confidence: {confidence}
        Engagement: {engagement}
        Comprehension: {comprehension}
        """
        
        current_messages = chat_history + [{"role": "user", "content": student_message}]
        adaptive_reply = get_gemini_response(ADAPTIVE_PROMPT + "\n\n" + context_string, current_messages)

        supabase.table("conversation_logs").insert({
            "student_id": student_id,
            "role": "assistant",
            "content": adaptive_reply
        }).execute()

        return jsonify({
            "reply": adaptive_reply,
            "inferred_states": {
                "confidence": confidence,
                "engagement": engagement,
                "comprehension": comprehension
            },
            "onboarding_complete": False
        })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
