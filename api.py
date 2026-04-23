import os
import json
import re
from flask import Flask, request, jsonify
from flask_cors import CORS
from anthropic import Anthropic
from supabase import create_client, Client

app = Flask(__name__)
CORS(app)

anthropic = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
supabase: Client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

# --- HELPER FUNCTIONS TO FIX LLM CRASHES ---

def extract_json_from_llm(text):
    """Safely extracts JSON from LLM output, ignoring markdown or conversational filler."""
    try:
        # Look for JSON block within markdown
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            return json.loads(match.group(0))
        return json.loads(text)
    except Exception as e:
        print(f"JSON Extraction Error: {e}. Raw text was: {text}")
        return None

def clean_chat_history(raw_history):
    """Ensures alternating user/assistant roles (required by Anthropic API)."""
    clean = []
    for msg in raw_history:
        if not clean or clean[-1]["role"] != msg["role"]:
            clean.append({"role": msg["role"], "content": msg["content"]})
        else:
            # Append to previous message if roles are identical
            clean[-1]["content"] += "\n" + msg["content"]
    return clean

# --- PROMPTS ---
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

@app.route('/process_turn', methods=['POST'])
def process_turn():
    data = request.json
    student_id = data.get('student_id')
    student_message = data.get('message')
    session_mode = data.get('session_mode', 'learning')
    
    # 1. Profile Management
    profile_res = supabase.table("profiles").select("*").eq("student_id", student_id).execute()
    if not profile_res.data:
        # Fix: Ensure baseline_data is stored as a valid empty dict/JSON object
        supabase.table("profiles").insert({"student_id": student_id, "baseline_data": {}}).execute()
        profile = {"baseline_data": {}, "history_confidence": [], "history_engagement": [], "history_comprehension": []}
    else:
        profile = profile_res.data[0]

    # 2. History Management
    chat_res = supabase.table("conversation_logs").select("*").eq("student_id", student_id).order("created_at").execute()
    raw_history = [{"role": row["role"], "content": row["content"]} for row in chat_res.data]
    chat_history = clean_chat_history(raw_history)

    # --- ONBOARDING MODE ---
    if session_mode == 'onboarding':
        current_messages = chat_history + [{"role": "user", "content": student_message}]
        current_messages = clean_chat_history(current_messages) # Ensure alternation
        
        response = anthropic.messages.create(
            model="claude-3-sonnet-20240229",
            max_tokens=300,
            system=ONBOARDING_PROMPT,
            messages=current_messages
        )
        
        reply = response.content[0].text
        is_complete = False
        
        supabase.table("conversation_logs").insert([
            {"student_id": student_id, "role": "user", "content": student_message},
            {"student_id": student_id, "role": "assistant", "content": reply}
        ]).execute()
        
        if "[ONBOARDING_COMPLETE]" in reply:
            is_complete = True
            reply = reply.replace("[ONBOARDING_COMPLETE]", "").strip()
            
            extract_res = anthropic.messages.create(
                model="claude-3-haiku-20240307",
                max_tokens=500,
                system=EXTRACTION_PROMPT,
                messages=current_messages
            )
            
            extracted_json = extract_json_from_llm(extract_res.content[0].text)
            if extracted_json:
                supabase.table("profiles").update({"baseline_data": extracted_json}).eq("student_id", student_id).execute()
                
        return jsonify({
            "reply": reply, 
            "inferred_states": None,
            "onboarding_complete": is_complete
        })

    # --- LEARNING MODE ---
    else:
        # Stage 1: Detection
        detection_response = anthropic.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=150,
            system=DETECTION_PROMPT,
            messages=[{"role": "user", "content": student_message}]
        )
        
        state_scores = extract_json_from_llm(detection_response.content[0].text)
        if state_scores:
            confidence = float(state_scores.get("confidence", 0.5))
            engagement = float(state_scores.get("engagement", 0.5))
            comprehension = float(state_scores.get("comprehension", 0.5))
        else:
            confidence, engagement, comprehension = 0.5, 0.5, 0.5 # Fallback

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

        # Stage 2: Adaptive Response
        context_string = f"""
        Learner Context: {json.dumps(profile.get('baseline_data', {}))}
        Current Turn Scores:
        Confidence: {confidence}
        Engagement: {engagement}
        Comprehension: {comprehension}
        """
        
        current_messages = chat_history + [{"role": "user", "content": student_message}]
        current_messages = clean_chat_history(current_messages)
        
        generation_response = anthropic.messages.create(
            model="claude-3-sonnet-20240229",
            max_tokens=800,
            system=ADAPTIVE_PROMPT + "\n\n" + context_string,
            messages=current_messages
        )
        
        adaptive_reply = generation_response.content[0].text

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
