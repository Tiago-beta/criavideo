"""Shared prompt rules for pilot-driven realistic shorts."""

from __future__ import annotations

import re

_INTERACTION_PERSONAS = {
	"homem",
	"mulher",
	"crianca",
	"familia",
	"natureza",
	"desenho",
	"personalizado",
}

_INTERACTION_PERSONA_LABELS = {
	"homem": "homem",
	"mulher": "mulher",
	"crianca": "crianca",
	"familia": "familia",
	"natureza": "natureza viva",
	"desenho": "personagem em estilo desenho",
	"personalizado": "persona personalizada",
}

_LOCATION_PERSONAS = {
	"estudio",
	"exterior",
	"interior",
	"natureza",
	"urbano",
	"personalizado",
}

_LOCATION_PERSONA_LABELS = {
	"estudio": "estudio fotografico cinematografico",
	"exterior": "ambiente externo aberto",
	"interior": "ambiente interno aconchegante",
	"natureza": "cenario de natureza viva",
	"urbano": "cenario urbano contemporaneo",
	"personalizado": "cenario personalizado da referencia",
}


def normalize_location_persona(value: str) -> str:
	raw = str(value or "").strip().lower()
	mapping = {
		"estúdio": "estudio",
		"estudio": "estudio",
		"custom": "personalizado",
		"personalizada": "personalizado",
	}
	normalized = mapping.get(raw, raw)
	return normalized if normalized in _LOCATION_PERSONAS else ""


def normalize_location_personas(values) -> list[str]:
	if values is None:
		return []
	if isinstance(values, str):
		raw_values = [values]
	else:
		try:
			raw_values = list(values)
		except Exception:
			return []
	out: list[str] = []
	for item in raw_values:
		loc = normalize_location_persona(str(item or ""))
		if loc and loc not in out:
			out.append(loc)
	return out


def build_location_persona_instruction(location_persona) -> str:
	locs = normalize_location_personas(location_persona)
	if not locs:
		return ""
	if len(locs) == 1:
		label = _LOCATION_PERSONA_LABELS.get(locs[0], locs[0])
		return (
			f"PERSONA DE LOCAL: ambiente o trecho em {label}, com luz, texturas e profundidade coerentes com esse cenario, "
			"mantendo cinematografia realista."
		)
	labels = [_LOCATION_PERSONA_LABELS.get(loc, loc) for loc in locs]
	return (
		"PERSONA DE LOCAL: o piloto vai testar variacoes nos cenarios: " + ", ".join(labels) + ". "
		"Cada variacao deve preservar a emocao do trecho com luz e composicao realistas."
	)


_DEFAULT_PILOT_PROMPT_TEMPLATE = (
	'Trecho base do short: "{{excerpt}}". '
	"Crie uma unica cena realista cinematografica baseada somente nesse trecho e na emocao imediata dele. "
	"Use apenas um protagonista principal claramente definido por vez e preserve a mesma identidade visual do inicio ao fim. "
	"Nao misture elementos de outros versos. "
	"Nao fundir humano com animal, planta, paisagem, objeto ou outra identidade no mesmo rosto/corpo. "
	"Nao transformar o protagonista em outra especie ao longo da cena. "
	"Sem texto na tela e sem legenda embutida. "
	"{{persona_instruction}}"
)


def normalize_interaction_persona(value: str) -> str:
	raw = str(value or "").strip().lower()
	mapping = {
		"criança": "crianca",
		"crianca": "crianca",
		"família": "familia",
		"familia": "familia",
		"custom": "personalizado",
		"personalizada": "personalizado",
	}
	normalized = mapping.get(raw, raw)
	return normalized if normalized in _INTERACTION_PERSONAS else ""


def normalize_interaction_personas(values: str | list[str] | tuple[str, ...] | None) -> list[str]:
	if values is None:
		return []
	if isinstance(values, str):
		raw_values = [values]
	else:
		raw_values = list(values)

	normalized: list[str] = []
	for item in raw_values:
		persona = normalize_interaction_persona(str(item or ""))
		if persona and persona not in normalized:
			normalized.append(persona)
	return normalized


def summarize_interaction_personas(values: str | list[str] | tuple[str, ...] | None) -> str:
	personas = normalize_interaction_personas(values)
	if not personas:
		return ""
	labels = [_INTERACTION_PERSONA_LABELS.get(persona, persona) for persona in personas]
	if len(labels) == 1:
		return labels[0]
	if len(labels) == 2:
		return f"{labels[0]} + {labels[1]}"
	return ", ".join(labels[:-1]) + f" e {labels[-1]}"


def _single_persona_instruction(persona: str) -> str:
	identity_guard = (
		" Preserve um unico rosto/corpo coerente do inicio ao fim. "
		"Nao fundir a persona com animal, planta, paisagem, objeto ou outra identidade."
	)

	if persona == "homem":
		return (
			"PERSONA DE INTERACAO: inclua um homem em cena interagindo com o ambiente e com a emocao do trecho "
			"(por exemplo, orando, cantando, caminhando ou contemplando), sem perder o sentido da letra."
			f"{identity_guard}"
		)
	if persona == "mulher":
		return (
			"PERSONA DE INTERACAO: inclua uma mulher em cena interagindo com o ambiente e com a emocao do trecho "
			"(por exemplo, orando, cantando, caminhando ou contemplando), sem perder o sentido da letra."
			f"{identity_guard}"
		)
	if persona == "crianca":
		return (
			"PERSONA DE INTERACAO: inclua uma crianca em cena interagindo com o ambiente e com a emocao do trecho, "
			"com linguagem visual sensivel e respeitosa."
			f"{identity_guard}"
		)
	if persona == "familia":
		return (
			"PERSONA DE INTERACAO: inclua uma familia (duas ou mais pessoas) interagindo de forma natural com o ambiente e com a emocao do trecho. "
			"Mantenha todos os integrantes humanos e visualmente coerentes entre si. "
			"Nao fundir nenhum membro com animal, planta, paisagem ou objeto."
		)
	if persona == "desenho":
		return (
			"PERSONA DE INTERACAO: inclua um personagem em estilo desenho ou animacao interagindo com o ambiente e com a emocao do trecho, "
			"mantendo coerencia visual cinematografica."
			f"{identity_guard}"
		)
	if persona == "personalizado":
		return (
			"PERSONA DE INTERACAO: inclua a persona personalizada definida pelo usuario, respeitando os tracos, estilo e identidade visual da referencia escolhida."
			f"{identity_guard}"
		)
	if persona == "natureza":
		return (
			"PERSONA DE INTERACAO: use apenas natureza viva como protagonista visual do trecho, com animal, ave, flor, planta ou outro ser natural em destaque. "
			"Nao inclua rosto, corpo, maos, silhueta ou traços humanos. "
			"Nao misture humano com animal, planta, paisagem ou objeto, nem combine duas especies no mesmo personagem."
		)
	return ""


def build_interaction_persona_instruction(interaction_persona: str | list[str] | tuple[str, ...]) -> str:
	personas = normalize_interaction_personas(interaction_persona)
	if not personas:
		return ""
	if len(personas) == 1:
		return _single_persona_instruction(personas[0])

	persona_summary = summarize_interaction_personas(personas)
	has_nature = "natureza" in personas
	has_human_like = any(persona in {"homem", "mulher", "crianca", "familia", "personalizado", "desenho"} for persona in personas)
	composition_rules = [
		f"PERSONAS DE INTERACAO: inclua na mesma cena estas presencas de forma separada e legivel: {persona_summary}.",
		"Cada persona deve existir como personagem ou elemento proprio, interagindo no mesmo momento sem trocar de identidade.",
		"Nao transformar uma persona na outra, nao fundir humano com animal/planta/paisagem/objeto e nao criar hibridos.",
		"Mantenha composicao clara, com cada persona reconhecivel e ocupando um papel visual especifico na cena.",
	]
	if has_nature and has_human_like:
		composition_rules.append(
			"Quando houver natureza e persona humana/desenho na mesma cena, trate a natureza como personagem proprio ou ambiente vivo separado, nunca como extensao do corpo/rosto da persona humana."
		)
	return " ".join(composition_rules)


def render_pilot_prompt_template(
	prompt_template: str,
	interaction_persona: str | list[str] | tuple[str, ...],
	excerpt: str,
) -> str:
	template = str(prompt_template or "").strip() or _DEFAULT_PILOT_PROMPT_TEMPLATE
	excerpt_text = " ".join(str(excerpt or "").split()).strip() or "{{TRECHO_TRANSCRITO_DO_SHORT}}"
	persona_instruction = build_interaction_persona_instruction(interaction_persona)

	rendered = template
	for placeholder in ("{{excerpt}}", "{{transcribed_excerpt}}", "{{lyrics_excerpt}}"):
		rendered = rendered.replace(placeholder, excerpt_text)

	if "{{persona_instruction}}" in rendered:
		rendered = rendered.replace("{{persona_instruction}}", persona_instruction)
	elif persona_instruction and "PERSONA DE INTERACAO:" not in rendered:
		rendered = f"{rendered} {persona_instruction}"

	rendered = re.sub(r"\s+", " ", rendered).strip()
	return rendered


def build_pilot_prompt_preview(
	interaction_persona: str | list[str] | tuple[str, ...],
	prompt_template: str = "",
	candidate_count: int = 1,
) -> dict:
	personas = normalize_interaction_personas(interaction_persona)
	persona = personas[0] if len(personas) == 1 else ""
	template = str(prompt_template or "").strip() or _DEFAULT_PILOT_PROMPT_TEMPLATE
	source = "custom" if str(prompt_template or "").strip() else "default"

	summary = [
		"A base visual sempre parte do trecho transcrito do short. Se a transcricao falhar, o sistema usa o trecho de letra salvo.",
		"O prompt trava um unico protagonista visual e bloqueia fusao de humano, animal, planta, paisagem ou objeto no mesmo rosto/corpo.",
	]
	if len(personas) > 1:
		summary.append(
			"Quando houver composicao de personas, cada presenca deve aparecer como entidade separada na mesma cena, sem metamorfose ou fusao entre elas."
		)
	elif persona == "natureza":
		summary.append("Com persona Natureza, o protagonista deve ser somente natureza viva e qualquer traço humano fica proibido.")
	elif persona:
		summary.append("Com persona humana ou personalizada, a natureza pode existir apenas no ambiente e nunca fundida ao protagonista.")
	if candidate_count > 1:
		summary.append(
			f"O piloto vai alternar {candidate_count} configuracoes salvas na rodada inicial; esta previa mostra a primeira configuracao ativa."
		)
	summary.append(
		"Voce pode editar este template antes de salvar. O texto final e reaplicado automaticamente antes de cada short do piloto."
	)

	return {
		"persona": persona,
		"personas": personas,
		"persona_summary": summarize_interaction_personas(personas),
		"prompt_template": template,
		"preview_prompt": render_pilot_prompt_template(template, personas or persona, "{{TRECHO_TRANSCRITO_DO_SHORT}}"),
		"source": source,
		"decision_summary": summary,
	}


def build_shorts_pilot_plan_preview(
	*,
	theme: str,
	top_video: dict | None,
	interaction_personas,
	location_personas,
	engine_id: str = "mega15",
	engine_duration_seconds: int = 10,
	prompt_template: str = "",
) -> dict:
	"""Build a complete pre-approval plan for a single pilot Short.

	Returns dict with: preview_prompt (final cinematic prompt), image_prompt
	(seed for thumbnail/frame generation), decision_summary (list of bullets),
	and structured plan metadata for persistence in AutoScheduleTheme.preview_plan.
	"""
	personas = normalize_interaction_personas(interaction_personas)
	locs = normalize_location_personas(location_personas)
	theme_text = " ".join(str(theme or "").split()).strip() or "tema do canal"
	top = top_video or {}
	top_title = str(top.get("title") or "").strip()

	persona_instruction = build_interaction_persona_instruction(personas)
	location_instruction = build_location_persona_instruction(locs)

	excerpt = theme_text
	if top_title:
		excerpt = f"{theme_text} (inspirado em '{top_title}')"

	rendered = render_pilot_prompt_template(prompt_template, personas, excerpt)
	if location_instruction and "PERSONA DE LOCAL:" not in rendered:
		rendered = f"{rendered} {location_instruction}"

	image_prompt_parts = [
		f"Frame inicial cinematografico vertical 9:16 para short de {engine_duration_seconds}s sobre: {theme_text}.",
	]
	if persona_instruction:
		image_prompt_parts.append(persona_instruction)
	if location_instruction:
		image_prompt_parts.append(location_instruction)
	image_prompt_parts.append("Sem texto na tela, sem legenda, luz natural realista, foco no protagonista principal.")
	image_prompt = " ".join(image_prompt_parts)

	summary = [
		f"Tema base: {theme_text}.",
		f"Motor selecionado: {engine_id} ({engine_duration_seconds}s, formato 9:16).",
	]
	if top_title:
		views = int(top.get("views") or 0)
		summary.append(f"Inspirado no top 1 do canal: '{top_title}' ({views:,} views).".replace(",", "."))
	if personas:
		summary.append(f"Persona de pessoa: {summarize_interaction_personas(personas)}.")
	if locs:
		labels = [_LOCATION_PERSONA_LABELS.get(loc, loc) for loc in locs]
		summary.append("Persona de local: " + ", ".join(labels) + ".")
	summary.append(
		"Este plano fica pre-aprovado. Se voce nao reprovar ate a janela expirar, o short e publicado automaticamente."
	)

	plan = {
		"theme": theme_text,
		"top_video": {
			"id": top.get("id") or "",
			"title": top_title,
			"views": int(top.get("views") or 0),
			"likes": int(top.get("likes") or 0),
			"comments": int(top.get("comments") or 0),
			"thumbnail_url": str(top.get("thumbnail_url") or ""),
			"url": str(top.get("url") or ""),
		} if top_title else None,
		"interaction_personas": personas,
		"location_personas": locs,
		"engine_id": engine_id,
		"engine_duration_seconds": int(engine_duration_seconds or 10),
		"aspect_ratio": "9:16",
	}

	return {
		"preview_prompt": rendered,
		"image_prompt": image_prompt,
		"decision_summary": summary,
		"plan": plan,
	}
