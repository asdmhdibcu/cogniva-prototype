import os
import json
from flask import Flask, request, jsonify
from flask_cors import CORS
from anthropic import Anthropic
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

# Initialize Clients
anthropic = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
supabase: Client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

# --- PROMPTS ---
DETECTION_PROMPT = """
You are the Cognitive State Detection Engine. Analyze the student's message against the 18 conversational signals.
Evaluate for Lexical Hedging, Elaborative Turn-Taking, Conceptual Paraphrasing, etc.
You must output ONLY a valid JSON object with exact decimal values between 0.00 and 1.00 for the following keys:
"confidence", "engagement", "comprehension". Do not include any other text.
"""

ADAPTIVE_PROMPT = """
You are Cogniva, an adaptive pedagogical AI.
You must respond to the student's message based on their longitudinal profile and their immediate cognitive state scores.
- If comprehension is low, simplify and use analogies related to their personal interests.
- If confidence is low, use warm, supportive scaffolding.
- If engagement is low, connect the concept to their career goals.
Adapt your exact response intensity proportionally to the exact decimal scores provided.
"""

@app.route('/process_turn', methods=['POST'])
def process_turn():
    data = request.json
    student_id = data.get('student_id')
    student_message = data.get('message')
    
    # 1. Retrieve Longitudinal Profile & History
    profile_res = supabase.table("profiles").select("*").eq("student_id", student_id).execute()
    profile = profile_res.data[0] if profile_res.data else {"baseline_data": {}}
    
    chat_res = supabase.table("conversation_logs").select("*").eq("student_id", student_id).order("created_at").execute()
    chat_history = [{"role": row["role"], "content": row["content"]} for row in chat_res.data]

    # --- STAGE 1: SIGNAL DETECTION CALL ---
    detection_response = anthropic.messages.create(
        model="claude-3-haiku-20240307",
        max_tokens=150,
        system=DETECTION_PROMPT,
        messages=[{"role": "user", "content": student_message}]
    )
    
    try:
        # Extract exact decimal values
        state_scores = json.loads(detection_response.content[0].text)
        confidence = float(state_scores.get("confidence", 0.5))
        engagement = float(state_scores.get("engagement", 0.5))
        comprehension = float(state_scores.get("comprehension", 0.5))
    except (json.JSONDecodeError, ValueError, KeyError):
        # Fallback if LLM fails strict JSON format
        confidence, engagement, comprehension = 0.5, 0.5, 0.5

    # 2. Update Longitudinal Profile in Supabase
    # In a production environment, append these to the arrays using RPC or direct list manipulation
    supabase.table("profiles").update({
        # Pseudo-code logic for array append:
        # "history_confidence": profile.get("history_confidence", []) + [confidence],
    }).eq("student_id", student_id).execute()

    # 3. Log user message
    supabase.table("conversation_logs").insert({
        "student_id": student_id,
        "role": "user",
        "content": student_message
    }).execute()

    # --- STAGE 2: ADAPTIVE RESPONSE CALL ---
    # Construct context string for the LLM
    context_string = f"""
    Learner Context: {json.dumps(profile.get('baseline_data', {}))}
    Current Turn Scores:
    Confidence: {confidence}
    Engagement: {engagement}
    Comprehension: {comprehension}
    """
    
    generation_response = anthropic.messages.create(
        model="claude-3-sonnet-20240229",
        max_tokens=800,
        system=ADAPTIVE_PROMPT + "\n\n" + context_string,
        messages=chat_history + [{"role": "user", "content": student_message}]
    )
    
    adaptive_reply = generation_response.content[0].text

    # 4. Log AI response
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
        }
    })

if __name__ == '__main__':
    app.run(port=5000, debug=True)
