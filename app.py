import streamlit as st
import requests
import uuid

API_URL = "http://127.0.0.1:5000/process_turn"


st.set_page_config(page_title="Cogniva Prototype", page_icon="🧠")

if "student_id" not in st.session_state:
    st.session_state.student_id = str(uuid.uuid4()) 
    
if "messages" not in st.session_state:
    st.session_state.messages = []

if "session_mode" not in st.session_state:
    st.session_state.session_mode = "onboarding"

st.title("Cogniva: Adaptive Learning Interface")
if st.session_state.session_mode == "onboarding":
    st.info("Current Mode: Onboarding Profile Generation")
else:
    st.success("Current Mode: Active Learning Session")
st.markdown("---")

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

if prompt := st.chat_input("Type your response here..."):
    
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.spinner("Cogniva is thinking..."):
        payload = {
            "student_id": st.session_state.student_id,
            "message": prompt,
            "session_mode": st.session_state.session_mode
        }
        
        try:
            response = requests.post(API_URL, json=payload)
            response.raise_for_status()
            data = response.json()
            
            bot_reply = data.get("reply", "Error generating response.")
            inferred_states = data.get("inferred_states")
            is_onboarding_complete = data.get("onboarding_complete", False)
            
            st.session_state.messages.append({"role": "assistant", "content": bot_reply})
            with st.chat_message("assistant"):
                st.markdown(bot_reply)
                
            if is_onboarding_complete:
                st.session_state.session_mode = "learning"
                st.success("Onboarding complete! Your longitudinal profile has been created. You are now in Learning Mode.")
                st.rerun()
                
            if inferred_states and st.session_state.session_mode == "learning":
                with st.expander("System Inference Data (Turn-Level)"):
                    st.write(f"**Confidence:** {inferred_states.get('confidence')}")
                    st.write(f"**Engagement:** {inferred_states.get('engagement')}")
                    st.write(f"**Comprehension:** {inferred_states.get('comprehension')}")
                
        except requests.exceptions.RequestException as e:
            st.error(f"Backend connection failed: {e}")

st.sidebar.title("Session Controls")
if st.sidebar.button("End Session & Complete Self-Report"):
    st.sidebar.success("Self-report instrument triggered. (Implementation for Likert scale goes here).")
