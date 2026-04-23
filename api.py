import os
import json
from flask import Flask, request, jsonify
from flask_cors import CORS
from anthropic import Anthropic
from supabase import create_client, Client

app = Flask(__name__)
CORS(app)

anthropic = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
supabase: Client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

ONBOARDING_PROMPT = """
You are Cogniva, an adaptive pedagogical AI. You are currently conducting an onboarding conversation with a new student. 
Your goal is to gather information across the following 10 thematic areas:
1. Degree programme and institution
2. Current modules and subject areas
3. Full-time or part-time study status and commitments
4. Academic strengths and difficulties
5. Personal interests and hobbies
6. Career goals and aspirations
7. Typical study schedule
8. Preferred learning style
9. Preferred explanation format
10. Baseline confidence in their studies

RULES:
- Maintain a warm, curious, and non-judgmental conversational register.
- DO NOT present these questions as a checklist. Ask them sequentially in a natural, dialogic manner.
- Acknowledge the student's previous response before moving to the next topic.
- Use the student's own words to personalize subsequent questions.
- If the student has answered all 10 areas, end your response with the exact string: "[ONBOARDING_COMPLETE]".
"""

EXTRACTION_PROMPT = """
Extract the onboarding conversation history into a structured JSON object. 
Map the student's responses to the following keys: "degree", "modules", "study_status", "strengths_weaknesses", "interests", "career_goals", "schedule", "learning_style", "explanation_format", "baseline_confidence".
Output ONLY the JSON object. Do not include markdown formatting or extra text.
"""

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
    session_mode = data.get('session_mode', 'learning')
    
    # Ensure profile exists
    profile_res = supabase.table("profiles").select("*").eq("student_id", student_id).execute()
    if not profile_res.data:
        supabase.table("profiles").insert({"student_id": student_id, "baseline_data": {}}).execute()
        profile = {"baseline_data": {}}
    else:
        profile = profile_res.data[0]

    chat_res = supabase.table("conversation_logs").select("*").eq("student_id", student_id).order("created_at").execute()
    chat_history = [{"role": row["role"], "content": row["content"]} for row in chat_res.data]

    if session_mode == 'onboarding':
        response = anthropic.messages.create(
            model="claude-3-sonnet-20240229",
            max_tokens=300,
            system=ONBOARDING_PROMPT,
            messages=chat_history + [{"role": "user", "content": student_message}]
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
                messages=chat_history + [{"role": "user", "content": student_message}]
            )
            
            try:
                extracted_json = json.loads(extract_res.content[0].text)
                supabase.table("profiles").update({"baseline_data": extracted_json}).eq("student_id", student_id).execute()
            except Exception:
                pass
                
        return jsonify({
            "reply": reply, 
            "inferred_states": None,
            "onboarding_complete": is_complete
        })

    else:
        detection_response = anthropic.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=150,
            system=DETECTION_PROMPT,
            messages=[{"role": "user", "content": student_message}]
        )
        
        try:
            state_scores = json.loads(detection_response.content[0].text)
            confidence = float(state_scores.get("confidence", 0.5))
            engagement = float(state_scores.get("engagement", 0.5))
            comprehension = float(state_scores.get("comprehension", 0.5))
        except Exception:
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
        
        generation_response = anthropic.messages.create(
            model="claude-3-sonnet-20240229",
            max_tokens=800,
            system=ADAPTIVE_PROMPT + "\n\n" + context_string,
            messages=chat_history + [{"role": "user", "content": student_message}]
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
