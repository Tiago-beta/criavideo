"""
Curated AI voice presets for create flows.

These presets use ElevenLabs multilingual voices chosen for good PT-BR readability.
"""

ELEVENLABS_BR_VOICE_PRESETS = [
    {
        "id": "ErXwobaYiN019PkySvjV",
        "name": "Caio",
        "label": "Masculina quente",
        "short_label": "Masc. Quente",
        "demo_text": "Oi, eu sou o Caio. Minha voz quente e natural funciona muito bem para videos em portugues do Brasil.",
    },
    {
        "id": "N2lVS1w4EtoT3dr4eOWO",
        "name": "Davi",
        "label": "Masculina clara",
        "short_label": "Masc. Clara",
        "demo_text": "Ola, eu sou o Davi. Tenho uma diccao clara e segura para narracoes em portugues do Brasil.",
    },
    {
        "id": "TxGEqnHWrfWFTfGW9XjX",
        "name": "Mateus",
        "label": "Narrativa premium",
        "short_label": "Narrativa",
        "demo_text": "Ola, eu sou o Mateus. Minha voz narrativa traz mais presenca e ritmo para historias e explicacoes.",
    },
    {
        "id": "EXAVITQu4vr4xnSDxMaL",
        "name": "Clara",
        "label": "Feminina brilhante",
        "short_label": "Fem. Brilhante",
        "demo_text": "Oi, eu sou a Clara. Minha voz e leve, viva e clara para falar com o publico brasileiro.",
    },
    {
        "id": "MF3mGyEYCl7XYWbV9V6O",
        "name": "Helena",
        "label": "Feminina madura",
        "short_label": "Fem. Madura",
        "demo_text": "Ola, eu sou a Helena. Tenho uma interpretacao mais madura, firme e elegante para narracoes.",
    },
    {
        "id": "21m00Tcm4TlvDq8ikWAM",
        "name": "Luisa",
        "label": "Feminina natural",
        "short_label": "Fem. Natural",
        "demo_text": "Oi, eu sou a Luisa. Minha voz e natural, proxima e funciona bem em portugues do Brasil.",
    },
]

ELEVENLABS_BR_VOICE_IDS = {preset["id"] for preset in ELEVENLABS_BR_VOICE_PRESETS}
ELEVENLABS_BR_DEFAULT_VOICE_ID = ELEVENLABS_BR_VOICE_PRESETS[0]["id"]


def is_elevenlabs_br_voice_id(voice_id: str) -> bool:
    return str(voice_id or "").strip() in ELEVENLABS_BR_VOICE_IDS


def get_elevenlabs_br_voice_preset(voice_id: str) -> dict | None:
    target = str(voice_id or "").strip()
    for preset in ELEVENLABS_BR_VOICE_PRESETS:
        if preset["id"] == target:
            return dict(preset)
    return None
