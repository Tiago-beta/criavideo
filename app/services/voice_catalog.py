"""
Curated AI voice presets for create flows.

These presets expand the catalog without removing the original builtin voices.
"""

ELEVENLABS_BR_BASE_TTS_INSTRUCTIONS = (
    "Fale em português do Brasil com pronúncia nativa, natural e fluida, "
    "sem sotaque estrangeiro, com dicção clara e ritmo humano."
)

ELEVENLABS_BR_VOICE_PRESETS = [
    {
        "id": "ErXwobaYiN019PkySvjV",
        "name": "Antoni",
        "label": "Masculina quente",
        "short_label": "Masc. Quente",
        "demo_text": "Oi, eu sou o Antoni. Minha voz quente e natural funciona muito bem para vídeos em português do Brasil.",
        "tts_instructions": "Timbre masculino quente, próximo e natural.",
    },
    {
        "id": "N2lVS1w4EtoT3dr4eOWO",
        "name": "Callum",
        "label": "Masculina clara",
        "short_label": "Masc. Clara",
        "demo_text": "Olá, eu sou o Callum. Tenho uma dicção clara e segura para narrações em português do Brasil.",
        "tts_instructions": "Timbre masculino claro, seguro e bem articulado.",
    },
    {
        "id": "yoZ06aMxZJJ28mfd3POQ",
        "name": "Sam",
        "label": "Masculina limpa",
        "short_label": "Masc. Limpa",
        "demo_text": "Oi, eu sou o Sam. Minha voz limpa e conversada ajuda a explicar ideias de forma natural em português do Brasil.",
        "tts_instructions": "Timbre masculino limpo, moderno e conversacional.",
    },
    {
        "id": "TxGEqnHWrfWFTfGW9XjX",
        "name": "Josh",
        "label": "Narrativa premium",
        "short_label": "Narrativa",
        "demo_text": "Olá, eu sou o Josh. Minha voz narrativa traz presença e ritmo para histórias e explicações.",
        "tts_instructions": "Narração premium, firme e envolvente.",
    },
    {
        "id": "pNInz6obpgDQGcFmaJgB",
        "name": "Adam",
        "label": "Masculina profunda",
        "short_label": "Masc. Profunda",
        "demo_text": "Olá, eu sou o Adam. Minha voz mais profunda funciona bem para narrações firmes em português do Brasil.",
        "tts_instructions": "Timbre masculino profundo, firme e natural.",
    },
    {
        "id": "JBFqnCBsd6RMkjVDRZzb",
        "name": "George",
        "label": "Narrador firme",
        "short_label": "Narrador",
        "demo_text": "Oi, eu sou o George. Posso narrar com firmeza e clareza para projetos em português do Brasil.",
        "tts_instructions": "Narrador firme, estável e elegante.",
    },
    {
        "id": "EXAVITQu4vr4xnSDxMaL",
        "name": "Bella",
        "label": "Feminina brilhante",
        "short_label": "Fem. Brilhante",
        "demo_text": "Oi, eu sou a Bella. Minha voz é viva e clara para falar com o público brasileiro.",
        "tts_instructions": "Timbre feminino brilhante, vivo e natural.",
    },
    {
        "id": "21m00Tcm4TlvDq8ikWAM",
        "name": "Rachel",
        "label": "Feminina natural",
        "short_label": "Fem. Natural",
        "demo_text": "Olá, eu sou a Rachel. Minha voz é natural, próxima e funciona muito bem em português do Brasil.",
        "tts_instructions": "Timbre feminino natural, próximo e suave.",
    },
    {
        "id": "MF3mGyEYCl7XYWbV9V6O",
        "name": "Elli",
        "label": "Feminina madura",
        "short_label": "Fem. Madura",
        "demo_text": "Oi, eu sou a Elli. Tenho uma interpretação mais madura, firme e elegante para narrações.",
        "tts_instructions": "Timbre feminino maduro, seguro e elegante.",
    },
    {
        "id": "AZnzlk1XvdvUeBnXmlld",
        "name": "Domi",
        "label": "Feminina intensa",
        "short_label": "Fem. Intensa",
        "demo_text": "Oi, eu sou a Domi. Minha voz entrega energia e impacto sem perder clareza em português do Brasil.",
        "tts_instructions": "Timbre feminino intenso, enérgico e controlado.",
    },
    {
        "id": "pMsXgVXv3BLzUgSXRplE",
        "name": "Serena",
        "label": "Feminina suave",
        "short_label": "Fem. Suave",
        "demo_text": "Olá, eu sou a Serena. Minha voz é suave, calma e natural para falar com clareza em português do Brasil.",
        "tts_instructions": "Timbre feminino suave, calmo e acolhedor.",
    },
    {
        "id": "VR6AewLTigWG4xSOuka",
        "name": "Arnold",
        "label": "Masculina forte",
        "short_label": "Masc. Forte",
        "demo_text": "Oi, eu sou o Arnold. Posso narrar com mais impacto e presença para vídeos em português do Brasil.",
        "tts_instructions": "Timbre masculino forte, marcante e estável.",
    },
    {
        "id": "2EiwWnXFnvU5JabPnv8n",
        "name": "Clyde",
        "label": "Masculina grave",
        "short_label": "Masc. Grave",
        "demo_text": "Olá, eu sou o Clyde. Minha voz grave ajuda a criar narrações densas e naturais em português do Brasil.",
        "tts_instructions": "Timbre masculino grave, encorpado e natural.",
    },
]

ELEVENLABS_BR_VOICE_IDS = {preset["id"] for preset in ELEVENLABS_BR_VOICE_PRESETS}
ELEVENLABS_BR_DEFAULT_VOICE_ID = ELEVENLABS_BR_VOICE_PRESETS[0]["id"]

GEMINI_BR_BASE_TTS_INSTRUCTIONS = (
    "Fale em português do Brasil com pronúncia nativa, dicção clara, "
    "cadência natural e interpretação humana. Nunca leia em voz alta "
    "títulos, labels, notas de direção ou metadados: fale apenas o texto final."
)

GEMINI_BR_VOICE_PRESETS = [
    {
        "id": "Kore",
        "name": "Kore",
        "label": "Firme",
        "short_label": "Firme",
        "demo_text": "Olá, eu sou a Kore. Minha voz firme e natural funciona muito bem para narrações claras em português do Brasil.",
        "tts_instructions": "Presença firme, segura e articulada, com naturalidade.",
    },
    {
        "id": "Puck",
        "name": "Puck",
        "label": "Animada",
        "short_label": "Animada",
        "demo_text": "Oi, eu sou a Puck. Minha voz mais animada dá ritmo e frescor para vídeos em português do Brasil.",
        "tts_instructions": "Energia alta, brilho na interpretação e leveza conversacional.",
    },
    {
        "id": "Charon",
        "name": "Charon",
        "label": "Informativa",
        "short_label": "Info",
        "demo_text": "Olá, eu sou a Charon. Minha voz informativa e estável ajuda a explicar qualquer assunto com clareza.",
        "tts_instructions": "Tom informativo, estável e muito claro.",
    },
    {
        "id": "Aoede",
        "name": "Aoede",
        "label": "Leve",
        "short_label": "Leve",
        "demo_text": "Oi, eu sou a Aoede. Minha voz leve e arejada deixa a narração mais suave e agradável em português do Brasil.",
        "tts_instructions": "Entrega leve, fluida e acolhedora.",
    },
    {
        "id": "Gacrux",
        "name": "Gacrux",
        "label": "Madura",
        "short_label": "Madura",
        "demo_text": "Olá, eu sou a Gacrux. Tenho uma interpretação madura, elegante e natural para projetos narrados.",
        "tts_instructions": "Voz madura, elegante e segura, com bom peso emocional.",
    },
    {
        "id": "Sulafat",
        "name": "Sulafat",
        "label": "Acolhedora",
        "short_label": "Acolhedora",
        "demo_text": "Oi, eu sou a Sulafat. Minha voz acolhedora e quente aproxima a mensagem do público brasileiro.",
        "tts_instructions": "Tom acolhedor, quente e próximo, sem soar artificial.",
    },
]

GEMINI_BR_VOICE_IDS = {preset["id"] for preset in GEMINI_BR_VOICE_PRESETS}
GEMINI_BR_DEFAULT_VOICE_ID = GEMINI_BR_VOICE_PRESETS[0]["id"]


def is_elevenlabs_br_voice_id(voice_id: str) -> bool:
    return str(voice_id or "").strip() in ELEVENLABS_BR_VOICE_IDS


def get_elevenlabs_br_voice_preset(voice_id: str) -> dict | None:
    target = str(voice_id or "").strip()
    for preset in ELEVENLABS_BR_VOICE_PRESETS:
        if preset["id"] == target:
            return dict(preset)
    return None


def is_gemini_br_voice_id(voice_id: str) -> bool:
    return str(voice_id or "").strip() in GEMINI_BR_VOICE_IDS


def get_gemini_br_voice_preset(voice_id: str) -> dict | None:
    target = str(voice_id or "").strip()
    for preset in GEMINI_BR_VOICE_PRESETS:
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


def build_gemini_ptbr_instructions(voice_id: str = "", extra_instructions: str = "") -> str:
    preset = get_gemini_br_voice_preset(voice_id)
    parts = [GEMINI_BR_BASE_TTS_INSTRUCTIONS]
    if preset:
        preset_instructions = str(preset.get("tts_instructions") or "").strip()
        if preset_instructions:
            parts.append(preset_instructions)
    extra = str(extra_instructions or "").strip()
    if extra:
        parts.append(extra)
    return " ".join(part for part in parts if part).strip()
