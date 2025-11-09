# ============================================
# iPad-Verhandlung – Kontrollbedingung (ohne Machtprimes)
# KI-Antworten nach Parametern, Deal/Abbruch, private Ergebnisse
# ============================================

import os, re, json, uuid, random, glob, requests
from datetime import datetime
import streamlit as st
import pandas as pd

# -----------------------------
# [SECRETS & MODELL]
# -----------------------------
API_KEY = st.secrets["OPENAI_API_KEY"]
MODEL  = st.secrets.get("OPENAI_MODEL", "gpt-4o-mini")
ADMIN_PASSWORD = st.secrets.get("ADMIN_PASSWORD")

# -----------------------------
# [STYLES]
# -----------------------------
st.markdown(
    """
    <style>
    .stButton > button { border-radius:9999px; padding:0.5rem 1rem; border:1px solid #cbd5e1; }
    .good { background:#ecfeff; border:1px solid #cffafe; color:#0e7490; padding:2px 8px; border-radius:6px; }
    .bad  { background:#fee2e2; border:1px solid #fecaca; color:#991b1b; padding:2px 8px; border-radius:6px; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("📱 iPad-Verhandlung (Kontrollbedingung)")
st.caption("Simulierte Ebay-Kleinanzeigen-Verhandlung ohne Machtprimes")

# -----------------------------
# [SIDEBAR]
# -----------------------------
with st.sidebar:
    st.subheader("Szenario")
    st.write("In diesem Szenario verhandeln Sie mit einem/ einer Verkäufer:in über ein neues iPad (256 GB, neuste Generation).")
    st.info("Sie können verhandeln, Fragen stellen oder ein Gegenangebot machen.")

# -----------------------------
# Default-Parameter (angepasst)
# -----------------------------
DEFAULT_PARAMS = {
    "scenario_text": "Sie verhandeln über ein neues iPad (256 GB, neuste Generation).",
    "list_price": 1000,          # Ausgangspreis (Anker) – sichtbar im Szenario
    "min_price": 750,            # *** Preis-Cap/Floor: Bot akzeptiert niemals < 750€ ***
    "tone": "freundlich, respektvoll, auf Augenhöhe, sachlich",
    "max_sentences": 4,          # KI-Antwortlänge in Sätzen
}

# -----------------------------
# [SESSION STATE]
# -----------------------------
if "sid" not in st.session_state:
    st.session_state.sid = str(uuid.uuid4())
if "params" not in st.session_state:
    st.session_state.params = DEFAULT_PARAMS.copy()
if "chat" not in st.session_state:
    # Erste Bot-Nachricht (freundlich, ohne Machtprimes)
    st.session_state.chat = [
        {"role":"assistant", "content":
         f"Hallo! Danke für Ihre Nachricht. Das iPad ist neu und originalverpackt. "
         f"Der angesetzte Preis liegt bei {st.session_state.params['list_price']} €. "
         "Wie ist Ihr Vorschlag?"}
    ]
if "closed" not in st.session_state:
    st.session_state.closed = False     # ob Verhandlung beendet ist
if "outcome" not in st.session_state:
    st.session_state.outcome = None     # "deal" oder "aborted"
if "final_price" not in st.session_state:
    st.session_state.final_price = None

# -----------------------------
# [REGELN: KEINE MACHTPRIMES + PREISFLOOR]
# -----------------------------
BAD_PATTERNS = [
    r"\balternative(n)?\b", r"\bweitere(n)?\s+interessent(en|in)\b", r"\bknapp(e|heit)\b",
    r"\bdeadline\b", r"\bletzte chance\b", r"\bbranchen(üblich|standard)\b",
    r"\bmarktpreis\b", r"\bneupreis\b", r"\bschmerzgrenze\b", r"\buntergrenze\b", r"darunter\s+gehe\s+ich\s+nicht", r"nicht\s+unter\s*\d+", r"mindestens\s*\d+", r"\bsonst geht es\b"
]

def contains_power_primes(text: str) -> bool:
    t = text.lower()
    return any(re.search(p, t) for p in BAD_PATTERNS)

# Preis aus Text erkennen (simple Extraktion €-Wert oder nackte Zahl)
PRICE_RE = re.compile(r"(?:€\s*)?(\d{2,5})")

def extract_prices(text: str):
    return [int(m.group(1)) for m in PRICE_RE.finditer(text)]

# -----------------------------
# [PREIS-LOGIK FÜR REALISTISCHE VERHANDLUNG]
# -----------------------------
def get_last_offer(history, role="assistant"):
    """
    Suche den letzten Preis (als Ganzzahl) aus den Nachrichten der angegebenen Rolle.
    """
    for m in reversed(history):
        if m.get("role") == role:
            prices = extract_prices(m.get("content",""))
            if prices:
                return prices[-1]
    return None

def suggest_counter_offer(history, params: dict, rounds:int) -> int | None:
    """
    Erzeuge einen konkreten Gegenpreis zwischen letztem Bot-Angebot und letztem Nutzer-Angebot.
    Springe nicht direkt auf die Untergrenze. Je niedriger das Nutzerangebot,
    desto kleinere Schritte und längere Verhandlung.
    """
    floor = int(params["min_price"])
    # Schätze vorheriges Bot-Angebot: Falls keines, nimm Listenpreis
    prev_bot = get_last_offer(history, role="assistant") or int(params["list_price"])
    user_offer = get_last_offer(history, role="user")
    if user_offer is None:
        # Kein Preis vom/r Käufer:in – biete kleinen Rabatt als Anker, aber weit über Floor
        target = max(prev_bot - 30, floor + 80)
        return target

    # Grundschritt: in Richtung Nutzerangebot, aber nicht zu schnell
    spread = max(prev_bot - user_offer, 0)
    # Schrittweite abhängig vom Spread (kleiner Schritt bei großem Spread)
    step = max(10, int(spread * 0.35))
    raw_target = prev_bot - step

    # Nie unter Nutzerangebot, aber zwischen beiden
    midpoint = int((prev_bot + user_offer) / 2)
    # Bleibe über dem Midpoint in frühen Runden, nähere dich später
    bias = max(0, 15 - 3*rounds)  # kleiner Bias mit Runden
    target = max(user_offer + 5, min(raw_target, midpoint + bias))

    # Pacing: Abstand zur Untergrenze in frühen Runden hoch halten
    buffer_above_floor = max(50 - 10*rounds, 15)  # Runde0:>=+50, Runde1:>=+40, ...
    target = max(target, floor + buffer_above_floor)

    # Obergrenze nicht erhöhen (keine Preiserhöhung gegenüber letztem Bot-Preis)
    target = min(target, prev_bot - 5) if prev_bot - 5 >= floor else max(target, floor + buffer_above_floor)

    return int(target)

# -----------------------------
# [SYSTEM-PROMPT KONSTRUKTION]
# -----------------------------
def system_prompt(params: dict) -> str:
    return (
        "Du simulierst eine Ebay-Kleinanzeigen-Verhandlung als VERKÄUFER eines iPad (256 GB, neuste Generation). "
        f"Ausgangspreis: {params['list_price']} €. "
        f"Sprache: Deutsch. Ton: {params['tone']}. "
        f"Antwortlänge: höchstens {params['max_sentences']} Sätze, keine Listen. "
        "Kontrollbedingung: KEINE Macht-/Knappheits-/Autoritäts-Frames, keine Hinweise auf Alternativen, Deadlines, "
        "Markt-/Neupreis oder 'Schmerzgrenze'. Keine Drohungen, keine Beleidigungen, keine Falschangaben. "
        "Bleibe strikt in der Rolle. "
        f"Preisliche Untergrenze (geheim): Du akzeptierst niemals < {params['min_price']} € und machst keine Angebote darunter. Verrate niemals, dass du eine Untergrenze hast, nenne keine konkreten Minimalpreise und verwende keine Formulierungen wie 'Untergrenze', 'Schmerzgrenze', 'darunter gehe ich nicht', 'mindestens X €'. "
        "Wenn der/die Käufer:in deutlich unterbietet, bleibe freundlich und verhandle, mache kleine Zugeständnisse und bleibe über der Untergrenze. "
        "Nimm ein Angebot erst an, wenn es >= {params['min_price']} € ist und es mindestens zwei Gegenrunden gab; ansonsten mache ein konkretes Gegenangebot."
    )

# -----------------------------
# [OPENAI: REST CALL]
# -----------------------------
def call_openai(messages, temperature=0.3, max_tokens=240):
    import json, requests, streamlit as st

    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": MODEL,            # z. B. "gpt-4o-mini"
        "messages": messages,      # [{"role":"system"/"user"/"assistant","content":"..."}]
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=60)
    except requests.RequestException as e:
        st.error(f"Netzwerkfehler zur OpenAI-API: {e}")
        return None

    status = r.status_code
    text = r.text

    try:
        data = r.json()
    except Exception:
        st.error(f"API-Fehler ({status}):\n{text}")
        return None

    if status >= 400:
        st.error(f"API-Fehler ({status}):\n{json.dumps(data, ensure_ascii=False, indent=2)}")
        return None

    try:
        return data["choices"][0]["message"]["content"]
    except Exception:
        st.error("Antwortformat unerwartet. Rohdaten:")
        st.code(text[:1000])
        return None


# -----------------------------
# [REPLY-GENERATOR]
# -----------------------------
def generate_reply(history, params: dict) -> str:
    # Runde bestimmen (Anzahl bisheriger User-Nachrichten)
    rounds = len([m for m in history if m.get("role") == "user"])
    # Konkreten Gegenpreis vorschlagen (für das Modell als Guidance)
    suggested = suggest_counter_offer(history, params, rounds)
    strategy = (
        "Verhandlungsstrategie: "
        "Mache ein konkretes Gegenangebot, steigere die Einigungschance realistisch und gehe in kleinen Schritten herunter. "
        f"Wenn ein Nutzerpreis >= {params['min_price']} € angeboten wird und es bereits mindestens zwei Gegenrunden gab, kannst du zustimmen. "
    )
    if suggested:
        strategy += f"Konkretes Gegenangebot für diese Runde: {suggested} €."
    sys_msg = {"role": "system", "content": system_prompt(params) + " " + strategy}
    reply = call_openai([sys_msg] + history)
    if not isinstance(reply, str):
        return "Entschuldigung, gerade gab es ein technisches Problem. Bitte versuchen Sie es erneut."

    # 2. Compliance: keine Machtprimes, Untergrenze einhalten
    def violates_rules(text: str, params: dict) -> str | None:
        t = text.lower()
        if contains_power_primes(text):
            return "Keine Macht-/Knappheits-/Autoritäts-Frames verwenden."
        # Keine Offenlegung der Untergrenze / Minimalpreis
        if re.search(r"\buntergrenze\b|\bschmerzgrenze\b|darunter\s+gehe\s+ich\s+nicht|nicht\s+unter\s*\d+", t):
            return "Verrate keine Untergrenze oder Minimalpreise."
        # Preis-Floor check
        prices = extract_prices(text)
        if any(p < params["min_price"] for p in prices):
            return f"Unterschreite nie {params['min_price']} €; mache kein Angebot darunter."
        return None

    reason = violates_rules(reply, params)
    attempts = 0
    while reason and attempts < 2:
        attempts += 1
        history2 = [{"role": "system", "content": system_prompt(params) + " " + strategy}] + history + [
            {"role":"system","content": f"REGEL-VERSTOSS: {reason}. Bitte korrigiere dich. "}
        ]
        reply = call_openai(history2, temperature=0.2)
        if not isinstance(reply, str):
            return "Entschuldigung, es gab ein Problem. Bitte erneut versuchen."
        reason = violates_rules(reply, params)

    return reply

# -----------------------------
# [UI]
# -----------------------------
st.write(f"**Ausgangspreis:** {st.session_state.params['list_price']} €")

st.caption(f"Session-ID: `{st.session_state.sid}`")

# -----------------------------
# [CHAT-VERLAUF]
# -----------------------------
for m in st.session_state.chat:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])

# Eingabe der Proband:innen
user_msg = st.chat_input("Ihre Nachricht …", disabled=st.session_state.closed)

# -----------------------------
# [LOGGING]
# -----------------------------
def append_log(event: dict):
    os.makedirs("logs", exist_ok=True)
    path = os.path.join("logs", f"{st.session_state.sid}.jsonl")
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")

# -----------------------------
# [INTERAKTION]
# -----------------------------
if user_msg and not st.session_state.closed:
    st.session_state.chat.append({"role":"user","content":user_msg})
    append_log({"t": datetime.utcnow().isoformat(), "role":"user", "content": user_msg})

    with st.chat_message("assistant"):
        # Sichtbare History (wie im Chat zu sehen)
        visible_history = [
            {"role":m["role"], "content":m["content"]}
            for m in st.session_state.chat
        ]
        reply = generate_reply(visible_history, st.session_state.params)
        st.markdown(reply)

    st.session_state.chat.append({"role":"assistant","content":reply})
    append_log({"t": datetime.utcnow().isoformat(), "role":"assistant", "content": reply})

# -----------------------------
# [DEAL / ABBRECHEN – Buttons]
# -----------------------------
st.divider()
st.subheader("Abschluss")
col1, col2 = st.columns(2)
with col1:
    deal_click = st.button("✅ Deal", disabled=st.session_state.closed)
with col2:
    abort_click = st.button("❌ Abbrechen", disabled=st.session_state.closed)

if deal_click and not st.session_state.closed:
    with st.expander("Finalen Preis bestätigen"):
        final = st.number_input("Finaler Preis (€):", min_value=0, max_value=10000,
                                value=st.session_state.params["list_price"], step=5)
        confirm = st.button("Einigung speichern")
        if confirm:
            st.session_state.closed = True
            st.session_state.outcome = "deal"
            st.session_state.final_price = int(final)
            append_log({"t": datetime.utcnow().isoformat(), "event":"outcome", "outcome":"deal", "final_price": int(final)})
            st.success("Einigung gespeichert. Vielen Dank!")

if abort_click and not st.session_state.closed:
    st.session_state.closed = True
    st.session_state.outcome = "aborted"
    st.session_state.final_price = None
    append_log({"t": datetime.utcnow().isoformat(), "event":"outcome", "outcome":"aborted"})
    st.warning("Die Verhandlung wurde abgebrochen.")

# -----------------------------
# [ADMIN]
# -----------------------------
st.divider()
st.subheader("Admin")
with st.expander("Admin-Bereich öffnen"):
    pwd = st.text_input("Admin-Passwort", type="password")
    if ADMIN_PASSWORD and pwd == ADMIN_PASSWORD:
        st.success("Admin-Zugang gewährt.")

        # --- Parametrisierung (nur Admin kann ändern) ---
        st.markdown("**Parameter anpassen**")
        with st.form("param_form"):
            scen = st.text_area("Szenario-Text", value=st.session_state.params["scenario_text"])
            list_price = st.number_input("Ausgangspreis (€)", min_value=0, max_value=10000, value=st.session_state.params["list_price"], step=10)
            min_price  = st.number_input("Untergrenze (€)", min_value=0, max_value=10000, value=st.session_state.params["min_price"], step=10)
            tone = st.text_input("Ton (Beschreibung)", value=st.session_state.params["tone"])
            max_sent = st.number_input("Max. Sätze pro Antwort", min_value=1, max_value=10, value=st.session_state.params["max_sentences"], step=1)
            ok = st.form_submit_button("Speichern")
        if ok:
            st.session_state.params.update({
                "scenario_text": scen,
                "list_price": int(list_price),
                "min_price": int(min_price),
                "tone": tone,
                "max_sentences": int(max_sent),
            })
            st.success("Parameter aktualisiert.")

        # --- Debug: Letzte Preise (optional) ---
        if st.checkbox("Letzte Angebote anzeigen"):
            last_bot = get_last_offer(st.session_state.chat, role="assistant")
            last_user = get_last_offer(st.session_state.chat, role="user")
            st.write(f"Letztes Bot-Angebot: {last_bot}")
            st.write(f"Letztes Nutzer-Angebot: {last_user}")
