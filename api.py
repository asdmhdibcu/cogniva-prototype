import os
import json
import re
from flask import Flask, request, jsonify
from flask_cors import CORS
import google.generativeai as genai
from supabase import create_client, Client

app = Flask(**name**)
CORS(app)

# Configure Gemini with your free API key from aistudio.google.com

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

supabase: Client = create_client(os.getenv(“SUPABASE_URL”), os.getenv(“SUPABASE_KEY”))

# ── SYSTEM PROMPTS ────────────────────────────────────────────────────────────

ONBOARDING_PROMPT = “””
You are Cogniva, an adaptive pedagogical AI. You are conducting a warm,
conversational onboarding with a new student.

Your goal is to gather information across these 10 thematic areas through
natural dialogue — NOT as a checklist:

1. Degree programme and institution
1. Current modules and subject areas
1. Full-time or part-time study status and commitments
1. Academic strengths and areas of difficulty
1. Personal interests and hobbies
1. Career goals and aspirations
1. Typical study schedule and available learning time
1. Preferred learning style
1. Preferred explanation format
1. Baseline confidence in their current studies

RULES:

- Be warm, curious, and non-judgmental.
- Always acknowledge the student’s previous response before moving forward.
- Ask one or two things at a time — never present a list.
- Use the student’s own words to personalise subsequent questions.
- When all 10 areas have been addressed, end your response with exactly:
  [ONBOARDING_COMPLETE]
  “””

EXTRACTION_PROMPT = “””
You are a data extraction assistant. Review the conversation history provided
and extract the student’s responses into a structured JSON object.

Map responses to these exact keys:
“degree”, “modules”, “study_status”, “strengths_weaknesses”, “interests”,
“career_goals”, “schedule”, “learning_style”, “explanation_format”,
“baseline_confidence”

Output ONLY the raw JSON object. No markdown, no extra text, no explanation.
“””

DETECTION_PROMPT = “””
You are the Cognitive State Detection Engine for an adaptive learning system.

Analyse the student’s message for these 18 conversational signals:

CONFIDENCE SIGNALS:

- Lexical Hedging: “maybe”, “possibly”, “I guess”
- Apologetic Prefacing: “I’m not sure but…”, “This is probably wrong”
- Qualifying Adverbs: “just”, “sort of”
- Tag Questions: “…right?”, “…don’t you think?”
- Ellipsis/Trailing Off: unfinished sentences when unsure

ENGAGEMENT SIGNALS:

- Elaborative Turn-Taking: extends beyond yes/no with examples
- Deep Questioning: asks “why” not just “what”
- Self-Correction: catches own mistakes mid-sentence
- Lexical Mirroring: adopts system terminology
- Active Backchanneling: “I see”, “that makes sense”

COMPREHENSION SIGNALS:

- Conceptual Paraphrasing: restates idea in different words
- Logical Connectors: “therefore”, “which means”, “because”
- Analogy Generation: relates concept to real-world scenario
- Gap Identification: states precisely what they don’t understand
- Application to New Context: asks if rule applies elsewhere

Output ONLY a valid JSON object with decimal values between 0.00 and 1.00:
{“confidence”: 0.00, “engagement”: 0.00, “comprehension”: 0.00}

No other text. No explanation. Only the JSON object.
“””

ADAPTIVE_PROMPT = “””
You are Cogniva, an adaptive pedagogical AI companion.

You receive the student’s longitudinal learner profile and current cognitive
state scores. Use them to calibrate every aspect of your response.

ADAPTATION RULES:

- comprehension < 0.4: Simplify significantly. Use analogies from their
  personal interests. Break into smaller steps.
- comprehension 0.4-0.7: Explain clearly with one relevant example.
- comprehension > 0.7: Go deeper. Introduce connected complexity.
- confidence < 0.4: Be warm and encouraging. Validate their attempt before
  correcting. Scaffold — guide, don’t tell.
- confidence 0.4-0.7: Balance support with gentle challenge.
- confidence > 0.7: Challenge them further. Ask them to explain back to you.
- engagement < 0.4: Connect concept directly to their career goals.
- engagement 0.4-0.7: Maintain current approach.
- engagement > 0.7: Deepen the challenge. Introduce what-if scenarios.

Always respond as a warm, intelligent companion. Never robotic.
Adapt your response intensity proportionally to the exact scores provided.
“””

# ── HELPER FUNCTIONS ──────────────────────────────────────────────────────────

def extract_json_from_llm(text):
“”“Safely extract JSON from LLM response text.”””
try:
match = re.search(r’{[\s\S]*}’, text)
if match:
return json.loads(match.group(0))
return json.loads(text)
except Exception:
return None

def format_messages_for_gemini(messages):
“””
Convert OpenAI-style messages to Gemini format.
Gemini uses ‘model’ instead of ‘assistant’ and ‘parts’ instead of ‘content’.
Also handles the constraint that conversation must start with ‘user’.
“””
formatted = []
for msg in messages:
role = “model” if msg[“role”] == “assistant” else “user”
# Merge consecutive same-role messages
if formatted and formatted[-1][“role”] == role:
formatted[-1][“parts”][0] += “\n” + msg[“content”]
else:
formatted.append({“role”: role, “parts”: [msg[“content”]]})

```
# Gemini requires conversation to start with user role
if formatted and formatted[0]["role"] == "model":
    formatted = formatted[1:]

return formatted
```

def get_gemini_response(system_prompt, messages, json_mode=False, max_tokens=800):
“””
Call Gemini API with system prompt and message history.
Returns text response.
Uses gemini-2.0-flash which is free at 1000 requests/day.
“””
generation_config = {“max_output_tokens”: max_tokens}
if json_mode:
generation_config[“response_mime_type”] = “application/json”

```
model = genai.GenerativeModel(
    model_name="gemini-2.0-flash",
    system_instruction=system_prompt,
    generation_config=generation_config
)

formatted = format_messages_for_gemini(messages)

# Gemini needs at least one message
if not formatted:
    formatted = [{"role": "user", "parts": ["Hello"]}]

response = model.generate_content(formatted)
return response.text
```

# ── MAIN ROUTE ────────────────────────────────────────────────────────────────

@app.route(’/process_turn’, methods=[‘POST’])
def process_turn():
data = request.json
student_id = data.get(‘student_id’)
student_message = data.get(‘message’)
session_mode = data.get(‘session_mode’, ‘learning’)

```
# ── Load or create student profile ──────────────────────────────────────
profile_res = supabase.table("profiles").select("*").eq(
    "student_id", student_id
).execute()

if not profile_res.data:
    supabase.table("profiles").insert({
        "student_id": student_id,
        "baseline_data": {},
        "history_confidence": [],
        "history_engagement": [],
        "history_comprehension": []
    }).execute()
    profile = {
        "baseline_data": {},
        "history_confidence": [],
        "history_engagement": [],
        "history_comprehension": []
    }
else:
    profile = profile_res.data[0]

# ── Load conversation history ────────────────────────────────────────────
chat_res = supabase.table("conversation_logs").select("*").eq(
    "student_id", student_id
).order("created_at").execute()

chat_history = [
    {"role": row["role"], "content": row["content"]}
    for row in chat_res.data
]

# ════════════════════════════════════════════════════════════════════════
# ONBOARDING MODE
# ════════════════════════════════════════════════════════════════════════
if session_mode == 'onboarding':
    current_messages = chat_history + [
        {"role": "user", "content": student_message}
    ]

    reply = get_gemini_response(
        ONBOARDING_PROMPT,
        current_messages,
        max_tokens=400
    )
    is_complete = False

    # Save this turn to conversation log
    supabase.table("conversation_logs").insert([
        {"student_id": student_id, "role": "user",
         "content": student_message},
        {"student_id": student_id, "role": "assistant",
         "content": reply}
    ]).execute()

    # Check if onboarding is complete
    if "[ONBOARDING_COMPLETE]" in reply:
        is_complete = True
        reply = reply.replace("[ONBOARDING_COMPLETE]", "").strip()

        # Extract structured profile from conversation
        extract_text = get_gemini_response(
            EXTRACTION_PROMPT,
            current_messages,
            json_mode=True,
            max_tokens=600
        )
        extracted_json = extract_json_from_llm(extract_text)

        if extracted_json:
            supabase.table("profiles").update({
                "baseline_data": extracted_json
            }).eq("student_id", student_id).execute()

    return jsonify({
        "reply": reply,
        "inferred_states": None,
        "onboarding_complete": is_complete
    })

# ════════════════════════════════════════════════════════════════════════
# LEARNING MODE
# ════════════════════════════════════════════════════════════════════════
else:
    # ── Stage 1: Signal Detection ────────────────────────────────────────
    detection_text = get_gemini_response(
        DETECTION_PROMPT,
        [{"role": "user", "content": student_message}],
        json_mode=True,
        max_tokens=100
    )
    state_scores = extract_json_from_llm(detection_text)

    if state_scores:
        confidence = float(state_scores.get("confidence", 0.5))
        engagement = float(state_scores.get("engagement", 0.5))
        comprehension = float(state_scores.get("comprehension", 0.5))
    else:
        confidence, engagement, comprehension = 0.5, 0.5, 0.5

    # ── Stage 2: Update Longitudinal Profile ─────────────────────────────
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

    # Save student message
    supabase.table("conversation_logs").insert({
        "student_id": student_id,
        "role": "user",
        "content": student_message
    }).execute()

    # ── Stage 3: Adaptive Response Generation ────────────────────────────
    context_string = f"""
```

LEARNER PROFILE:
{json.dumps(profile.get(‘baseline_data’, {}), indent=2)}

CURRENT TURN COGNITIVE STATE SCORES:

- Confidence:    {confidence:.2f}
- Engagement:    {engagement:.2f}
- Comprehension: {comprehension:.2f}

LONGITUDINAL TREND (last 5 turns):

- Confidence trend:    {hist_conf[-5:]}
- Engagement trend:    {hist_eng[-5:]}
- Comprehension trend: {hist_comp[-5:]}
  “””
  current_messages = chat_history + [
  {“role”: “user”, “content”: student_message}
  ]
  
  ```
    adaptive_reply = get_gemini_response(
        ADAPTIVE_PROMPT + "\n\n" + context_string,
        current_messages,
        max_tokens=800
    )
  
    # Save assistant response
    supabase.table("conversation_logs").insert({
        "student_id": student_id,
        "role": "assistant",
        "content": adaptive_reply
    }).execute()
  
    return jsonify({
        "reply": adaptive_reply,
        "inferred_states": {
            "confidence": round(confidence, 2),
            "engagement": round(engagement, 2),
            "comprehension": round(comprehension, 2)
        },
        "onboarding_complete": False
    })
  ```

if **name** == ‘**main**’:
app.run(
host=‘0.0.0.0’,
port=int(os.environ.get(“PORT”, 5000)),
debug=False
)
