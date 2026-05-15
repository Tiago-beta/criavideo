"""
Curated AI voice presets for create flows.

These presets use ElevenLabs multilingual voices chosen to expand the catalog
without removing the original builtin voices from the UI.
"""

ELEVENLABS_BR_BASE_TTS_INSTRUCTIONS = (
    "Fale em portugues do Brasil com pronuncia nativa, natural e fluida, "
    "sem sotaque estrangeiro, com diccao clara e ritmo humano."
)

ELEVENLABS_BR_VOICE_PRESETS = [
    {
        "id": "ErXwobaYiN019PkySvjV",
        "name": "Antoni",
        "label": "Masculina quente",
        "short_label": "Masc. Quente",
        "demo_text": "Oi, eu sou o Antoni. Minha voz quente e natural funciona bem para videos em portugues do Brasil.",
        "tts_instructions": "Timbre masculino quente, proximo e natural.",
    },
    {
        "id": "N2lVS1w4EtoT3dr4eOWO",
        "name": "Callum",
        "label": "Masculina clara",
        "short_label": "Masc. Clara",
        "demo_text": "Ola, eu sou o Callum. Tenho uma diccao clara e segura para narracoes em portugues do Brasil.",
        "tts_instructions": "Timbre masculino claro, seguro e bem articulado.",
    },
    {
        "id": "yoZ06aMxZJJ28mfd3POQ",
        "name": "Sam",
        "label": "Masculina limpa",
        "short_label": "Masc. Limpa",
        "demo_text": "Oi, eu sou o Sam. Minha voz limpa e conversada ajuda a explicar ideias de forma natural em portugues do Brasil.",
        "tts_instructions": "Timbre masculino limpo, moderno e conversacional.",
    },
    {
        "id": "TxGEqnHWrfWFTfGW9XjX",
        "name": "Josh",
        "label": "Narrativa premium",
        "short_label": "Narrativa",
        "demo_text": "Ola, eu sou o Josh. Minha voz narrativa traz presenca e ritmo para historias e explicacoes.",
        "tts_instructions": "Narracao premium, firme e envolvente.",
    },
    {
        "id": "pNInz6obpgDQGcFmaJgB",
        "name": "Adam",
        "label": "Masculina profunda",
        "short_label": "Masc. Profunda",
        "demo_text": "Ola, eu sou o Adam. Minha voz mais profunda funciona bem para narracoes firmes em portugues do Brasil.",
        "tts_instructions": "Timbre masculino profundo, firme e natural.",
    },
    {
        "id": "JBFqnCBsd6RMkjVDRZzb",
        "name": "George",
        "label": "Narrador firme",
        "short_label": "Narrador",
        "demo_text": "Oi, eu sou o George. Posso narrar com firmeza e clareza para projetos em portugues do Brasil.",
        "tts_instructions": "Narrador firme, estavel e elegante.",
    },
    {
        "id": "EXAVITQu4vr4xnSDxMaL",
        "name": "Bella",
        "label": "Feminina brilhante",
        "short_label": "Fem. Brilhante",
        "demo_text": "Oi, eu sou a Bella. Minha voz e viva e clara para falar com o publico brasileiro.",
        "tts_instructions": "Timbre feminino brilhante, vivo e natural.",
    },
    {
        "id": "21m00Tcm4TlvDq8ikWAM",
        "name": "Rachel",
        "label": "Feminina natural",
        "short_label": "Fem. Natural",
        "demo_text": "Ola, eu sou a Rachel. Minha voz e natural, proxima e funciona bem em portugues do Brasil.",
        "tts_instructions": "Timbre feminino natural, proximo e suave.",
    },
    {
        "id": "MF3mGyEYCl7XYWbV9V6O",
        "name": "Elli",
        "label": "Feminina madura",
        "short_label": "Fem. Madura",
        "demo_text": "Oi, eu sou a Elli. Tenho uma interpretacao mais madura, firme e elegante para narracoes.",
        "tts_instructions": "Timbre feminino maduro, seguro e elegante.",
    },
    {
        "id": "AZnzlk1XvdvUeBnXmlld",
        "name": "Domi",
        "label": "Feminina intensa",
        "short_label": "Fem. Intensa",
        "demo_text": "Oi, eu sou a Domi. Minha voz entrega energia e impacto sem perder clareza em portugues do Brasil.",
        "tts_instructions": "Timbre feminino intenso, energico e controlado.",
    },
    {
        "id": "pMsXgVXv3BLzUgSXRplE",
        "name": "Serena",
        "label": "Feminina suave",
        "short_label": "Fem. Suave",
        "demo_text": "Ola, eu sou a Serena. Minha voz e suave, calma e natural para falar com clareza em portugues do Brasil.",
        "tts_instructions": "Timbre feminino suave, calmo e acolhedor.",
    },
    {
        "id": "VR6AewLTigWG4xSOuka",
        "name": "Arnold",
        "label": "Masculina forte",
        "short_label": "Masc. Forte",
        "demo_text": "Oi, eu sou o Arnold. Posso narrar com mais impacto e presenca para videos em portugues do Brasil.",
        "tts_instructions": "Timbre masculino forte, marcante e estavel.",
    },
    {
        "id": "2EiwWnXFnvU5JabPnv8n",
        "name": "Clyde",
        "label": "Masculina grave",
        "short_label": "Masc. Grave",
        "demo_text": "Ola, eu sou o Clyde. Minha voz grave ajuda a criar narracoes densas e naturais em portugues do Brasil.",
        "tts_instructions": "Timbre masculino grave, encorpado e natural.",
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


def build_elevenlabs_ptbr_instructions(voice_id: str = "", extra_instructions: str = "") -> str:
    preset = get_elevenlabs_br_voice_preset(voice_id)
    parts = [ELEVENLABS_BR_BASE_TTS_INSTRUCTIONS]
    if preset:
        preset_instructions = str(preset.get("tts_instructions") or "").strip()
        if preset_instructions:
            parts.append(preset_instructions)
    extra = str(extra_instructions or "").strip()
    if extra:
        parts.append(extra)
    return " ".join(part for part in parts if part).strip()
