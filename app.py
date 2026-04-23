import streamlit as st
import requests
import uuid

# Configuration
API_URL = "http://localhost:5000/process_turn"

st.set_page_config(page_title="Cogniva Prototype", page_icon="🧠")

# Session State Initialization
if "student_id" not in st.session_state:
    # Simulating a logged-in student UUID for the prototype
    st.session_state.student_id = str(uuid.uuid4()) 
    
if "messages" not in st.session_state:
    st.session_state.messages = []

# UI Header
st.title("Cogniva: Adaptive Learning Interface")
st.markdown("---")

# Render existing chat history from session state
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# Chat Input
if prompt := st.chat_input("Type your response here..."):
    
    # Render user message
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Call Flask Backend
    with st.spinner("Cogniva is thinking..."):
        payload = {
            "student_id": st.session_state.student_id,
            "message": prompt
        }
        
        try:
            response = requests.post(API_URL, json=payload)
            response.raise_for_status()
            data = response.json()
            
            bot_reply = data.get("reply", "Error generating response.")
            inferred_states = data.get("inferred_states", {})
            
            # Render bot response
            st.session_state.messages.append({"role": "assistant", "content": bot_reply})
            with st.chat_message("assistant"):
                st.markdown(bot_reply)
                
            # Developer Data: Show the exact inference values (Optional for debugging)
            with st.expander("System Inference Data (Turn-Level)"):
                st.write(f"**Confidence:** {inferred_states.get('confidence')}")
                st.write(f"**Engagement:** {inferred_states.get('engagement')}")
                st.write(f"**Comprehension:** {inferred_states.get('comprehension')}")
                
        except requests.exceptions.RequestException as e:
            st.error(f"Backend connection failed: {e}")

# Footer Tool: Session End / Self-Report Trigger
st.sidebar.title("Session Controls")
if st.sidebar.button("End Session & Complete Self-Report"):
    st.sidebar.success("Self-report instrument triggered. (Implementation for Likert scale goes here).")
