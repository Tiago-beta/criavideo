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
