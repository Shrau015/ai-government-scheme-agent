import hashlib
import io
import re
import sqlite3
from pathlib import Path

import streamlit as st

try:
    import speech_recognition as sr
except ImportError:
    sr = None


# ---------------------------------------------------------
# Page setup
# ---------------------------------------------------------
st.set_page_config(
    page_title="AI Government Scheme Agent",
    page_icon="🏛️",
    layout="wide",
    initial_sidebar_state="expanded",
)

BASE_DIR = Path(__file__).resolve().parent
DB_FILE = BASE_DIR / "government_schemes.db"

DEFAULT_CATEGORIES = [
    "Agriculture",
    "Business",
    "Disability",
    "Education",
    "Employment",
    "Financial Support",
    "Healthcare",
    "House Loan",
    "Housing",
    "Insurance",
    "Pension",
    "Scholarship",
    "Senior Citizens",
    "Students",
    "Women & Child",
]


# ---------------------------------------------------------
# Database helpers
# ---------------------------------------------------------
def ensure_database_exists():
    if not DB_FILE.exists():
        st.error(
            "Database not found. Keep `government_schemes.db` in the same "
            "folder as this `app.py` file."
        )
        st.stop()


@st.cache_data(show_spinner=False)
def load_schemes(database_path: str, modified_time: float):
    """Load all scheme records from the SQLite database."""
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row

    try:
        rows = connection.execute(
            """
            SELECT
                name, category, min_age, max_age, max_income, student,
                gender, state, eligibility, benefits, documents, link
            FROM schemes
            ORDER BY name COLLATE NOCASE
            """
        ).fetchall()
    finally:
        connection.close()

    return [dict(row) for row in rows]


def normalise(value):
    return str(value or "").strip().lower()


def is_all_india(value):
    value = normalise(value)
    return value in {
        "",
        "all",
        "all india",
        "india",
        "national",
        "central",
        "any",
        "any state",
        "na",
        "n/a",
        "none",
    }


def matches_gender(scheme_gender, selected_gender):
    if selected_gender == "Any":
        return True

    scheme_gender = normalise(scheme_gender)
    selected_gender = normalise(selected_gender)

    return (
        is_all_india(scheme_gender)
        or selected_gender in scheme_gender
        or "all" in scheme_gender
        or "both" in scheme_gender
        or "any" in scheme_gender
    )


def matches_state(scheme_state, selected_state):
    if selected_state == "All India / Any State":
        return True

    scheme_state = normalise(scheme_state)
    selected_state = normalise(selected_state)

    return is_all_india(scheme_state) or selected_state in scheme_state


def matches_category(scheme_category, selected_categories):
    if not selected_categories:
        return True

    scheme_category = normalise(scheme_category)
    return any(normalise(category) in scheme_category for category in selected_categories)


def safe_number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def scheme_matches_profile(scheme, age, income, gender, state, categories):
    min_age = safe_number(scheme.get("min_age"))
    max_age = safe_number(scheme.get("max_age"))
    max_income = safe_number(scheme.get("max_income"))

    if min_age is not None and age < min_age:
        return False

    if max_age is not None and max_age > 0 and age > max_age:
        return False

    if max_income is not None and max_income > 0 and income > max_income:
        return False

    if not matches_gender(scheme.get("gender"), gender):
        return False

    if not matches_state(scheme.get("state"), state):
        return False

    if not matches_category(scheme.get("category"), categories):
        return False

    return True


def find_matching_schemes(schemes, age, income, gender, state, categories):
    return [
        scheme
        for scheme in schemes
        if scheme_matches_profile(scheme, age, income, gender, state, categories)
    ]


def search_schemes(schemes, question, profile_matches):
    """Find schemes relevant to a typed or spoken question."""
    question_words = set(re.findall(r"[a-zA-Z]+", question.lower()))

    scored = []
    for scheme in schemes:
        searchable_text = " ".join(
            str(scheme.get(field, "") or "")
            for field in [
                "name",
                "category",
                "eligibility",
                "benefits",
                "documents",
                "state",
            ]
        ).lower()

        score = sum(1 for word in question_words if len(word) > 2 and word in searchable_text)

        if scheme in profile_matches:
            score += 3

        if score > 0:
            scored.append((score, scheme))

    scored.sort(key=lambda item: (-item[0], item[1]["name"].lower()))
    return [scheme for _, scheme in scored[:5]]


# ---------------------------------------------------------
# Chat helpers
# ---------------------------------------------------------
def build_assistant_reply(question, suggested_schemes, profile_matches):
    question = question.strip()

    if suggested_schemes:
        names = ", ".join(f"**{scheme['name']}**" for scheme in suggested_schemes[:3])
        return (
            f"Based on your question, these schemes may be relevant: {names}. "
            "Open the cards below to review eligibility, benefits, documents, and "
            "the official website."
        )

    if profile_matches:
        return (
            f"I could not find an exact keyword match for “{question}”, but I found "
            f"**{len(profile_matches)}** scheme(s) that match your selected profile. "
            "Try using words such as education, housing, loan, scholarship, health, "
            "women, pension, farming, or employment."
        )

    return (
        "I could not find a matching scheme with the current profile filters. "
        "Try changing your age, income, gender, state, or category in the Quick "
        "Scheme Finder."
    )


def show_scheme_card(scheme, key_prefix):
    with st.container(border=True):
        st.subheader(scheme.get("name") or "Government scheme")

        category = scheme.get("category") or "Not specified"
        state = scheme.get("state") or "All India"
        st.caption(f"Category: {category}  •  State: {state}")

        col1, col2, col3 = st.columns(3)
        col1.metric("Age", f"{scheme.get('min_age', '—')}–{scheme.get('max_age', '—')}")
        col2.metric(
            "Income limit",
            f"₹{scheme.get('max_income'):,}"
            if isinstance(scheme.get("max_income"), (int, float))
            else str(scheme.get("max_income") or "Not specified"),
        )
        col3.metric("Gender", str(scheme.get("gender") or "All"))

        with st.expander("Eligibility, benefits and documents"):
            st.markdown("**Eligibility**")
            st.write(scheme.get("eligibility") or "Not specified")

            st.markdown("**Benefits**")
            st.write(scheme.get("benefits") or "Not specified")

            st.markdown("**Documents required**")
            st.write(scheme.get("documents") or "Not specified")

        official_link = str(scheme.get("link") or "").strip()
        if official_link:
            if not official_link.startswith(("http://", "https://")):
                official_link = f"https://{official_link}"

            st.link_button(
                "Open official website",
                official_link,
                icon=":material/open_in_new:",
                key=f"{key_prefix}_{scheme['name']}",
            )


def render_chat_message(message, index):
    role = message["role"]

    if role == "user":
        left, right = st.columns([3, 1])
        with left:
            with st.container(border=True):
                st.caption("You")
                st.write(message["content"])
    else:
        left, right = st.columns([1, 3])
        with right:
            with st.container(border=True):
                st.caption("AI Government Scheme Agent")
                st.write(message["content"])

                for scheme_index, scheme in enumerate(message.get("schemes", [])):
                    show_scheme_card(scheme, f"chat_{index}_{scheme_index}")


def add_chat_exchange(question, schemes, profile_matches):
    reply = build_assistant_reply(question, schemes, profile_matches)

    st.session_state.messages.append(
        {
            "role": "user",
            "content": question,
        }
    )
    st.session_state.messages.append(
        {
            "role": "assistant",
            "content": reply,
            "schemes": schemes,
        }
    )


def transcribe_audio(audio_file):
    if sr is None:
        return None, (
            "Voice input needs the `SpeechRecognition` package. "
            "Install the requirements and restart the app."
        )

    recognizer = sr.Recognizer()

    try:
        audio_bytes = audio_file.getvalue()
        with sr.AudioFile(io.BytesIO(audio_bytes)) as source:
            audio_data = recognizer.record(source)

        transcript = recognizer.recognize_google(audio_data, language="en-IN")
        return transcript, None

    except sr.UnknownValueError:
        return None, "I could not understand that recording. Please try again."
    except sr.RequestError:
        return None, (
            "Voice recognition is currently unavailable. Check your internet "
            "connection and try again."
        )
    except Exception as error:
        return None, f"Unable to process this recording: {error}"


# ---------------------------------------------------------
# Session state
# ---------------------------------------------------------
st.session_state.setdefault("messages", [])
st.session_state.setdefault("processed_audio_hash", "")

ensure_database_exists()
schemes = load_schemes(str(DB_FILE), DB_FILE.stat().st_mtime)

all_database_categories = sorted(
    {
        str(scheme.get("category")).strip()
        for scheme in schemes
        if str(scheme.get("category") or "").strip()
    }
)

category_options = sorted(set(DEFAULT_CATEGORIES + all_database_categories))


# ---------------------------------------------------------
# Sidebar: Quick Scheme Finder
# ---------------------------------------------------------
with st.sidebar:
    st.title("Quick Scheme Finder")
    st.caption("Choose your details to find potentially eligible schemes.")

    with st.form("scheme_finder_form"):
        age = st.slider("Age", min_value=1, max_value=100, value=25)

        income = st.number_input(
            "Annual family income (₹)",
            min_value=0,
            value=300000,
            step=10000,
        )

        gender = st.selectbox(
            "Gender",
            ["Any", "Female", "Male", "Transgender", "Other"],
        )

        state = st.text_input(
            "State or Union Territory",
            placeholder="Example: Karnataka",
        )
        selected_state = state.strip() or "All India / Any State"

        selected_categories = st.multiselect(
            "Categories",
            category_options,
            placeholder="Choose one or more categories",
        )

        find_schemes_clicked = st.form_submit_button(
            "Find schemes",
            type="primary",
            icon=":material/search:",
        )

    if st.button("Clear chat history", icon=":material/delete_outline:"):
        st.session_state.messages = []
        st.rerun()

profile_matches = find_matching_schemes(
    schemes,
    age,
    income,
    gender,
    selected_state,
    selected_categories,
)


# ---------------------------------------------------------
# Main interface
# ---------------------------------------------------------
st.title("AI Government Scheme Agent")
st.write(
    "Find government schemes, check basic eligibility, and open official websites."
)

if find_schemes_clicked:
    if profile_matches:
        st.success(f"Found {len(profile_matches)} potentially matching scheme(s).")

        for index, scheme in enumerate(profile_matches):
            show_scheme_card(scheme, f"finder_{index}")
    else:
        st.info("No schemes matched the selected profile. Try adjusting the filters.")

st.divider()
st.subheader("Ask about government schemes")

if not st.session_state.messages:
    st.caption(
        "Examples: “Which education schemes can I apply for?” or "
        "“Show me housing loan schemes.”"
    )

for message_index, message in enumerate(st.session_state.messages):
    render_chat_message(message, message_index)


# ---------------------------------------------------------
# Microphone voice-to-text
# ---------------------------------------------------------
st.markdown("#### Voice input")
audio_file = st.audio_input(
    "Record your question",
    key="scheme_voice_input",
    sample_rate=16000,
)

if audio_file is not None:
    audio_hash = hashlib.sha256(audio_file.getvalue()).hexdigest()

    if audio_hash != st.session_state.processed_audio_hash:
        st.session_state.processed_audio_hash = audio_hash

        with st.spinner("Converting your voice to text..."):
            transcript, error_message = transcribe_audio(audio_file)

        if error_message:
            st.warning(error_message)
        elif transcript:
            st.success(f"You said: {transcript}")
            relevant_schemes = search_schemes(schemes, transcript, profile_matches)
            add_chat_exchange(transcript, relevant_schemes, profile_matches)
            st.rerun()


# ---------------------------------------------------------
# Text chat input
# ---------------------------------------------------------
question = st.chat_input("Type your question about a government scheme")

if question:
    relevant_schemes = search_schemes(schemes, question, profile_matches)
    add_chat_exchange(question, relevant_schemes, profile_matches)
    st.rerun()
