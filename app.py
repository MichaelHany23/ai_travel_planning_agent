"""
app.py — The Streamlit front-end for the AI Travel Planning Agent.

This file is the entry point. It sets up the chat interface, manages the
conversation history, talks to the ADK agent runner behind the scenes,
and handles all the little things that can go wrong (API overload, lost
sessions, etc.) so the user just sees a smooth chat experience.
"""

import os
import asyncio
import logging
import uuid

# Load the .env file *before* any Google/ADK imports so API keys are
# already in the environment when those libraries initialize.
from dotenv import load_dotenv
load_dotenv()

# The ADK logger is quite chatty — it prints full stack traces for every
# transient error, even ones the retry logic recovers from. We surface
# real failures in the UI, so we turn the logger down to keep the
# terminal clean and readable.
logging.getLogger("google_adk").setLevel(logging.CRITICAL)

# These markers help us figure out what kind of error we're dealing with
# so we can decide whether a retry is worth it.
_TRANSIENT_ERROR_MARKERS = ("503", "UNAVAILABLE", "high demand")
_QUOTA_ERROR_MARKERS = ("429", "RESOURCE_EXHAUSTED")
_QUOTA_RETRY_DELAY_SECONDS = 8

import nest_asyncio
# Streamlit already runs its own event loop, and asyncio normally
# doesn't allow nesting. nest_asyncio patches that so we can call
# asyncio.run() inside Streamlit without everything blowing up.
nest_asyncio.apply()

import streamlit as st
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from google.genai.errors import ServerError

from agents import root_agent


# ── Page Configuration ───────────────────────────────────────────────
# Basic Streamlit setup — page title, icon, layout, and sidebar state.
# ─────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="AI Travel Planning Agent",
    page_icon="✈️",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ── Sidebar ──────────────────────────────────────────────────────────
# The sidebar gives the user some context: how to use the app, what's
# powering it, a button to start fresh, and a quick glance at whether
# the API keys are configured correctly.
# ─────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("✈️ AI Travel Planner")
    st.markdown("---")
    st.markdown(
        "### How to use\n"
        "Just type a natural-language travel request in the chat below. "
        "Be as specific as you want — the more detail you give, the better "
        "the plan will be.\n\n"
        "**Some ideas to get you started:**\n"
        "- *Plan a 5-day trip to Paris in June, budget $2000, I love art and food*\n"
        "- *Weekend getaway to Tokyo, budget $1500, interested in anime and street food*\n"
        "- *10 days in Italy visiting Rome and Florence, $3000 budget, history lover*\n\n"
        "Once the plan is generated, feel free to ask follow-up questions — "
        "like swapping an activity, adjusting the budget, or getting more restaurant ideas!"
    )
    st.markdown("---")
    st.markdown(
        "### Powered by\n"
        "- 🤖 Google ADK + Gemini\n"
        "- 🔍 Tavily Search API\n"
        "- 🗺️ OpenStreetMap / Nominatim\n"
        "- 🌐 Streamlit"
    )
    st.markdown("---")

    # A simple "start over" button that clears the chat and creates a new session.
    if st.button("🗑️ Clear Conversation", use_container_width=True):
        st.session_state.messages = []
        st.session_state.session_id = str(uuid.uuid4())
        st.rerun()

    # Quick status indicators so the user can tell at a glance whether
    # their API keys are set up — no need to dig through error logs.
    st.markdown("### API Status")
    google_ok = bool(os.getenv("GOOGLE_API_KEY"))
    tavily_ok = bool(os.getenv("TAVILY_API_KEY"))
    st.markdown(
        f"{'✅' if google_ok else '❌'} Google API Key\n\n"
        f"{'✅' if tavily_ok else '❌'} Tavily API Key"
    )
    if not google_ok or not tavily_ok:
        st.warning("Add missing API keys to your `.env` file.")


# ── Session State Initialization ─────────────────────────────────────
# Streamlit reruns the whole script on every interaction, so we stash
# anything we need to persist (chat history, session IDs, the runner)
# in st.session_state.
# ─────────────────────────────────────────────────────────────────────

if "messages" not in st.session_state:
    st.session_state.messages = []

if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())

if "user_id" not in st.session_state:
    st.session_state.user_id = "streamlit_user"

# Create the ADK runner once and reuse it across reruns. The runner
# holds the agent graph and the session service that tracks conversation
# history for the model.
if "adk_runner" not in st.session_state:
    session_service = InMemorySessionService()
    runner = Runner(
        agent=root_agent,
        app_name="ai-travel-planning-agent",
        session_service=session_service,
    )
    st.session_state.adk_runner = runner
    st.session_state.adk_session_service = session_service


# ── Agent Runner Helpers ─────────────────────────────────────────────
# These async functions handle the back-and-forth with the ADK runner,
# including retry logic for when the model is temporarily overloaded
# or rate-limited.
# ─────────────────────────────────────────────────────────────────────

def _is_transient_error(message: str) -> bool:
    """Check if an error message looks like a temporary model overload (503, etc.)."""
    return any(marker in message for marker in _TRANSIENT_ERROR_MARKERS)


def _is_quota_error(message: str) -> bool:
    """Check if an error message looks like a rate-limit / quota hit (429, etc.)."""
    return any(marker in message for marker in _QUOTA_ERROR_MARKERS)


async def _stream_response(runner: Runner, session_id: str, user_id: str, content: types.Content) -> str:
    """Send a message to the agent and collect the final response text.

    We iterate over the async event stream from the runner. Most events
    are intermediate (tool calls, sub-agent hand-offs), but we only care
    about the final response — the one the user will actually see.
    """
    final_text = ""
    agen = runner.run_async(
        session_id=session_id,
        user_id=user_id,
        new_message=content,
    )
    try:
        async for event in agen:
            # Some failures come through as an error event on the stream
            # rather than a raised exception, so catch those first.
            if getattr(event, "error_code", None):
                raise RuntimeError(event.error_message or event.error_code)
            if event.is_final_response():
                if event.content and event.content.parts:
                    final_text = "\n".join(
                        part.text for part in event.content.parts if hasattr(part, "text")
                    )
                break
    finally:
        # Always close the generator in the same async context it was
        # opened in. If we don't, a retry could start a new generator
        # while the old one is still tearing down — and that causes
        # messy GeneratorExit / OpenTelemetry cleanup errors.
        await agen.aclose()
    return final_text


async def _stream_response_with_retry(
    runner: Runner,
    session_id: str,
    user_id: str,
    content: types.Content,
    max_attempts: int = 2,
) -> str:
    """Try to get a response, retrying on temporary errors.

    Two kinds of errors get a retry:
    - 503 / UNAVAILABLE (model overloaded) → short exponential backoff
    - 429 / RESOURCE_EXHAUSTED (quota hit)  → one retry after a longer pause,
      because hammering a rate-limited API just makes things worse
    """
    quota_retried = False
    for attempt in range(1, max_attempts + 1):
        try:
            return await _stream_response(runner, session_id, user_id, content)
        except (ServerError, RuntimeError) as e:
            message = str(e)
            if _is_quota_error(message):
                if attempt == max_attempts or quota_retried:
                    raise
                quota_retried = True
                await asyncio.sleep(_QUOTA_RETRY_DELAY_SECONDS)
            elif _is_transient_error(message):
                if attempt == max_attempts:
                    raise
                await asyncio.sleep(2 ** attempt)
            else:
                raise


async def _run_agent_async(runner: Runner, session_id: str, user_id: str, message: str) -> str:
    """Top-level async function that runs the agent end-to-end.

    It handles session creation, sends the message, and deals with
    edge cases like a session that disappeared from the in-memory store
    (which can happen if Streamlit hot-reloads the app).
    """
    content = types.Content(
        role="user",
        parts=[types.Part(text=message)],
    )

    session_service = st.session_state.adk_session_service
    app_name = "ai-travel-planning-agent"

    # Make sure the session exists. If it doesn't (first message, or
    # the session was lost), create a new one.
    try:
        await session_service.get_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
        )
    except Exception:
        await session_service.create_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
        )

    try:
        final_text = await _stream_response_with_retry(runner, session_id, user_id, content)
    except Exception as e:
        # If the session vanished mid-conversation, just spin up a new one.
        if "Session not found" in str(e):
            new_session_id = str(uuid.uuid4())
            await session_service.create_session(
                app_name=app_name,
                user_id=user_id,
                session_id=new_session_id,
            )
            st.session_state.session_id = new_session_id
            final_text = await _stream_response_with_retry(runner, new_session_id, user_id, content)
        elif isinstance(e, (ServerError, RuntimeError)) and _is_quota_error(str(e)):
            return "The AI model's quota is temporarily exhausted. Please wait a bit before trying again."
        elif isinstance(e, (ServerError, RuntimeError)) and _is_transient_error(str(e)):
            return "The AI model is temporarily overloaded. Please try again in a moment."
        else:
            raise

    return final_text or "I couldn't generate a response. Please try again."


def get_travel_plan(message: str) -> str:
    """Synchronous wrapper so the rest of the Streamlit code doesn't
    need to deal with async/await directly.
    """
    runner: Runner = st.session_state.adk_runner
    session_id: str = st.session_state.session_id
    user_id: str = st.session_state.user_id

    try:
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(
            _run_agent_async(runner, session_id, user_id, message)
        )
    except RuntimeError:
        return asyncio.run(
            _run_agent_async(runner, session_id, user_id, message)
        )


# ── Main Chat Interface ─────────────────────────────────────────────
# This is what the user actually sees: a title, a subtitle, the chat
# history, and an input box at the bottom.
# ─────────────────────────────────────────────────────────────────────

st.title("🌍 AI Travel Planning Agent")
st.caption(
    "Tell me about your dream trip and I'll coordinate flight, hotel, and itinerary "
    "specialists to build your complete travel plan."
)

# Re-render the conversation history so previous messages don't disappear.
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# Show a friendly welcome message the first time someone opens the app.
if not st.session_state.messages:
    with st.chat_message("assistant"):
        welcome = (
            "👋 Hey there! I'm your AI Travel Planning Agent.\n\n"
            "Tell me about the trip you're dreaming of and I'll get three specialist "
            "agents working on it — one for flights, one for hotels, and one for "
            "building your day-by-day itinerary.\n\n"
            "**Here's an example to get started:**\n"
            "> *Plan a 5-day trip to Paris in June, budget $2000, I love art and food*"
        )
        st.markdown(welcome)

# The chat input box — this is where the magic starts.
if prompt := st.chat_input("Where do you want to go? (e.g. '5 days in Bali, June, $1500, beaches and culture')"):
    # Show the user's message immediately.
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Call the agents and display the response.
    with st.chat_message("assistant"):
        with st.spinner("🔍 Your three specialists are researching flights, hotels & activities — hang tight, this usually takes 30–60 seconds..."):
            response = get_travel_plan(prompt)

        st.markdown(response)

    st.session_state.messages.append({"role": "assistant", "content": response})
