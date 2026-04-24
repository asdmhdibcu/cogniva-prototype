import streamlit as st
import requests
import uuid
import os

# ── Page Configuration ───────────────────────────────────────────────────────

st.set_page_config(
    page_title="Cogniva",
    page_icon="🧠",
    layout="centered"
)

# ── FIX 1: API URL from environment variable ─────────────────────────────────

# Locally: set API_URL=http://127.0.0.1:5000/process_turn in your .env

# On Railway: set API_URL as an environment variable pointing to your Flask service

API_URL = os.environ.get("API_URL", "http://127.0.0.1:5000/process_turn")

# ── Session State Initialisation ─────────────────────────────────────────────

if "student_id" not in st.session_state:
    st.session_state.student_id = str(uuid.uuid4())

if "messages" not in st.session_state:
    st.session_state.messages = []

if "session_mode" not in st.session_state:
    st.session_state.session_mode = "onboarding"

if "self_report_done" not in st.session_state:
    st.session_state.self_report_done = False

# ── Header ───────────────────────────────────────────────────────────────────

st.title("🧠 Cogniva")
st.caption("Adaptive Learning Intelligence")

if st.session_state.session_mode == "onboarding":
    st.info("**Onboarding Mode** — Cogniva is learning about you.")
else:
    st.success("**Learning Mode** — Your personalised session is active.")

st.markdown("—")

# ── Render Conversation History ───────────────────────────────────────────────

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# ── Chat Input ────────────────────────────────────────────────────────────────

if prompt := st.chat_input("Type your message here…"):

    # Display student message immediately
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Send to Flask backend
    with st.spinner("Cogniva is thinking..."):
        payload = {
            "student_id": st.session_state.student_id,
            "message": prompt,
            "session_mode": st.session_state.session_mode
        }

        try:
            response = requests.post(API_URL, json=payload, timeout=30)
            response.raise_for_status()
            data = response.json()

            bot_reply = data.get("reply", "Sorry, I could not generate a response.")
            inferred_states = data.get("inferred_states")
            is_onboarding_complete = data.get("onboarding_complete", False)

            # Display Cogniva response
            st.session_state.messages.append({
                "role": "assistant",
                "content": bot_reply
            })
            with st.chat_message("assistant"):
                st.markdown(bot_reply)

            # Handle onboarding completion
            if is_onboarding_complete:
                st.session_state.session_mode = "learning"
                st.success(
                    "✅ Onboarding complete! Your learner profile has been "
                    "created. You are now in Learning Mode."
                )
                st.rerun()

            # Display inference data in learning mode
            if inferred_states and st.session_state.session_mode == "learning":
                with st.expander("📊 Cognitive State Inference (This Turn)"):
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.metric(
                            "Confidence",
                            f"{inferred_states.get('confidence', 0):.0%}"
                        )
                    with col2:
                        st.metric(
                            "Engagement",
                            f"{inferred_states.get('engagement', 0):.0%}"
                        )
                    with col3:
                        st.metric(
                            "Comprehension",
                            f"{inferred_states.get('comprehension', 0):.0%}"
                        )

        except requests.exceptions.ConnectionError:
            st.error(
                "❌ Cannot connect to backend. "
                "Make sure the Flask API is running."
            )
        except requests.exceptions.Timeout:
            st.error("❌ Request timed out. Please try again.")
        except requests.exceptions.RequestException as e:
            st.error(f"❌ Backend error: {e}")


# ── Sidebar ───────────────────────────────────────────────────────────────────

st.sidebar.title("Session Controls")
st.sidebar.markdown(f"**Student ID:** `{st.session_state.student_id[:8]}...`")
st.sidebar.markdown(
    f"**Mode:** {'Onboarding' if st.session_state.session_mode == 'onboarding' else 'Learning'}"
)

st.sidebar.markdown("—")

# Self-report instrument

if st.session_state.session_mode == "learning":
    st.sidebar.markdown("### 📝 Post-Session Self-Report")

    if not st.session_state.self_report_done:
        with st.sidebar.form("self_report_form"):
            st.markdown("Rate your experience this session:")

            confidence_rating = st.slider(
                "How confident did you feel?",
                1, 5, 3
            )
            engagement_rating = st.slider(
                "How engaged were you?",
                1, 5, 3
            )
            comprehension_rating = st.slider(
                "How well did you understand?",
                1, 5, 3
            )
            helpful_text = st.text_area(
                "What felt most helpful?",
                placeholder="Optional..."
            )
            unhelpful_text = st.text_area(
                "What could be improved?",
                placeholder="Optional..."
            )

            submitted = st.form_submit_button("Submit Self-Report")

            if submitted:
                st.session_state.self_report_done = True
                st.session_state.self_report_data = {
                    "confidence": confidence_rating,
                    "engagement": engagement_rating,
                    "comprehension": comprehension_rating,
                    "helpful": helpful_text,
                    "unhelpful": unhelpful_text
                }
                st.success("✅ Self-report submitted. Thank you!")
    else:
        st.sidebar.success("✅ Self-report already submitted for this session.")
