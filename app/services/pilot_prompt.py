"""Shared prompt rules for pilot-driven realistic shorts.

Updated 2026-05-28:
- Removed anti-hybrid / no-fusion guards. Hybrid creatures, surreal mixes
  and stylized chimeras are now welcome when the channel benefits from them.
- ``build_shorts_pilot_plan_preview`` is async and uses the strongest LLM
  available (gpt-5) to produce a per-second cinematic timeline with a
  strong opening hook designed to retain the viewer until the end.
"""

from __future__ import annotations

import json
import logging
import re

import openai

from app.config import settings

logger = logging.getLogger(__name__)

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
	"natureza": "natureza viva (animais, plantas, paisagens)",
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
			f"LOCAL: ambientar em {label}, com luz, texturas e profundidade coerentes "
			"e cinematografia marcante."
		)
	labels = [_LOCATION_PERSONA_LABELS.get(loc, loc) for loc in locs]
	return (
		"LOCAL: variar entre os cenarios: " + ", ".join(labels) + ", "
		"preservando emocao e impacto visual em cada variacao."
	)


_DEFAULT_PILOT_PROMPT_TEMPLATE = (
	'Tema base do short: "{{excerpt}}". '
	"Criar uma cena cinematografica vertical 9:16 com gancho forte nos primeiros segundos "
	"para prender o espectador ate o fim. Liberdade criativa total: animais hibridos, "
	"criaturas surreais, personagens fantasticos e qualquer mistura visual sao permitidos "
	"quando ajudarem o canal. Sem texto na tela e sem legenda embutida. "
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
	if persona == "homem":
		return "PERSONA: incluir um homem como protagonista da cena, interagindo com o ambiente."
	if persona == "mulher":
		return "PERSONA: incluir uma mulher como protagonista da cena, interagindo com o ambiente."
	if persona == "crianca":
		return "PERSONA: incluir uma crianca como protagonista da cena, com linguagem visual sensivel."
	if persona == "familia":
		return "PERSONA: incluir uma familia (duas ou mais pessoas) interagindo de forma natural na cena."
	if persona == "desenho":
		return "PERSONA: usar um personagem em estilo desenho ou animacao, com identidade visual coerente."
	if persona == "personalizado":
		return "PERSONA: usar a persona personalizada definida pelo usuario, respeitando os tracos da referencia."
	if persona == "natureza":
		return "PERSONA: protagonista visual e natureza viva (animal, ave, planta, paisagem) com forte presenca."
	return ""


def build_interaction_persona_instruction(interaction_persona: str | list[str] | tuple[str, ...]) -> str:
	personas = normalize_interaction_personas(interaction_persona)
	if not personas:
		return ""
	if len(personas) == 1:
		return _single_persona_instruction(personas[0])

	persona_summary = summarize_interaction_personas(personas)
	return (
		f"PERSONAS: combinar na mesma cena estas presencas: {persona_summary}. "
		"Liberdade criativa para misturar, hibridizar ou alternar entre elas conforme o impacto da narrativa."
	)


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
	elif persona_instruction and "PERSONA" not in rendered:
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
		"A base visual sempre parte do trecho transcrito do short.",
		"O prompt e estruturado segundo a segundo, com gancho forte nos primeiros segundos.",
		"Liberdade criativa total: hibridos, criaturas surreais e misturas visuais sao permitidos.",
	]
	if candidate_count > 1:
		summary.append(
			f"O piloto vai alternar {candidate_count} configuracoes salvas na rodada inicial; esta previa mostra a primeira."
		)
	summary.append(
		"Voce pode editar este template antes de salvar. O texto final e reaplicado antes de cada short do piloto."
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


# ── Timeline cinematografico segundo a segundo ──────────────────────────────


def _format_timeline_seconds(timeline: list[dict], duration: int) -> str:
	"""Format a list of {sec, action} into readable per-second markers."""
	lines = []
	for entry in timeline:
		try:
			sec = int(entry.get("sec") or entry.get("second") or 0)
		except Exception:
			sec = 0
		action = str(entry.get("action") or entry.get("description") or "").strip()
		if not action:
			continue
		sec = max(0, min(sec, duration))
		lines.append(f"{sec:02d}s - {action}")
	return "\n".join(lines)


def _fallback_timeline(duration: int, theme: str) -> list[dict]:
	"""Deterministic per-second skeleton when LLM is unavailable."""
	theme_short = (theme or "tema").strip()
	timeline: list[dict] = []
	hook = (
		f"GANCHO: abertura inesperada e cinematografica conectada a '{theme_short}', "
		"com movimento de camera marcante e detalhe surreal para prender o olhar"
	)
	timeline.append({"sec": 0, "action": hook})
	if duration >= 3:
		timeline.append({"sec": 2, "action": "Revelacao do protagonista em close, com expressao forte"})
	mid = max(3, duration // 2)
	timeline.append({"sec": mid, "action": f"Desenvolvimento do conflito ou contraste visual sobre '{theme_short}'"})
	if duration > mid + 2:
		timeline.append({"sec": duration - 2, "action": "Virada visual ou momento de auge emocional"})
	timeline.append({"sec": duration, "action": "Frame final impactante que sugere continuacao e convida ao replay"})
	return timeline


async def _generate_timeline_with_llm(
	*,
	theme: str,
	duration: int,
	personas: list[str],
	locations: list[str],
	top_video: dict | None,
	channel_hint: str = "",
) -> tuple[list[dict], dict]:
	"""Call the strongest available LLM (gpt-5) to draft a per-second cinematic timeline.

	Returns (timeline, meta). meta keys: hook_summary, scene_description, image_prompt.
	"""
	api_key = (settings.openai_api_key or "").strip()
	if not api_key:
		return [], {}

	client = openai.AsyncOpenAI(api_key=api_key)

	persona_text = summarize_interaction_personas(personas) or "livre escolha"
	loc_labels = [_LOCATION_PERSONA_LABELS.get(l, l) for l in locations]
	loc_text = ", ".join(loc_labels) if loc_labels else "livre escolha"
	top_title = ""
	top_views = 0
	if isinstance(top_video, dict):
		top_title = str(top_video.get("title") or "").strip()
		try:
			top_views = int(top_video.get("views") or 0)
		except Exception:
			top_views = 0

	system_prompt = (
		"Voce e um diretor de cinema especialista em Shorts/Reels viralizaveis em 9:16. "
		"Sua tarefa: planejar uma cena com marcacao SEGUNDO A SEGUNDO, com gatilho inicial "
		"forte nos primeiros 2 segundos (algo inesperado, surpreendente ou emocionalmente "
		"intenso) que prenda o espectador ate o ultimo frame. Liberdade criativa total: "
		"animais hibridos, criaturas surreais, mundos fantasticos e qualquer mistura visual "
		"sao bem-vindos se servirem ao impacto. NAO escreva regras de proibicao no prompt. "
		"Responda SOMENTE em JSON valido."
	)

	user_prompt = (
		f"TEMA DO SHORT: {theme}\n"
		f"DURACAO TOTAL: {duration} segundos (formato 9:16, vertical)\n"
		f"PERSONA SUGERIDA: {persona_text}\n"
		f"LOCAL SUGERIDO: {loc_text}\n"
		f"INSPIRACAO (top video do canal): {top_title or '-'} ({top_views} views)\n"
		f"CONTEXTO DO CANAL: {channel_hint or '-'}\n\n"
		"Retorne JSON com este formato exato:\n"
		"{\n"
		'  "timeline": [\n'
		'    {"sec": 0, "action": "<descricao do que acontece em tela neste segundo>"},\n'
		'    {"sec": 1, "action": "..."},\n'
		'    ...\n'
		f'    {{"sec": {duration}, "action": "frame final impactante"}}\n'
		"  ],\n"
		'  "hook_summary": "<resumo de uma frase do gatilho inicial>",\n'
		'  "scene_description": "<descricao cinematografica geral, 2-3 frases>",\n'
		'  "image_prompt": "<prompt para gerar o frame inicial cinematografico vertical 9:16>"\n'
		"}\n\n"
		f"REGRAS: cobrir todos os {duration} segundos com pelo menos uma entrada a cada 1-2 segundos. "
		"O segundo 0 deve ser o gancho que prende a atencao com algo inesperado. "
		"Sem texto na tela, sem legendas embutidas. Linguagem visual rica e cinematografica."
	)

	resp = None
	try:
		resp = await client.chat.completions.create(
			model="gpt-5",
			messages=[
				{"role": "system", "content": system_prompt},
				{"role": "user", "content": user_prompt},
			],
			response_format={"type": "json_object"},
			max_completion_tokens=2000,
		)
	except Exception as exc:
		logger.warning("pilot timeline gpt-5 failed, trying gpt-4o: %s", exc)
		try:
			resp = await client.chat.completions.create(
				model="gpt-4o",
				messages=[
					{"role": "system", "content": system_prompt},
					{"role": "user", "content": user_prompt},
				],
				response_format={"type": "json_object"},
				max_tokens=1800,
				temperature=0.85,
			)
		except Exception as exc2:
			logger.warning("pilot timeline gpt-4o also failed: %s", exc2)
			return [], {}

	try:
		content = (resp.choices[0].message.content or "").strip()
		data = json.loads(content)
	except Exception as exc:
		logger.warning("pilot timeline JSON parse failed: %s", exc)
		return [], {}

	timeline_raw = data.get("timeline") if isinstance(data, dict) else None
	if not isinstance(timeline_raw, list) or not timeline_raw:
		return [], {}
	out = [entry for entry in timeline_raw if isinstance(entry, dict)]
	meta = {
		"hook_summary": str(data.get("hook_summary") or "").strip(),
		"scene_description": str(data.get("scene_description") or "").strip(),
		"image_prompt": str(data.get("image_prompt") or "").strip(),
	}
	return out, meta


async def build_shorts_pilot_plan_preview(
	*,
	theme: str,
	top_video: dict | None,
	interaction_personas,
	location_personas,
	engine_id: str = "mega15",
	engine_duration_seconds: int = 10,
	prompt_template: str = "",
	channel_hint: str = "",
) -> dict:
	"""Build a complete pre-approval plan for a single pilot Short.

	Calls the strongest LLM (gpt-5 -> gpt-4o fallback) to draft a per-second
	cinematic timeline with a strong opening hook. Falls back to a
	deterministic skeleton if no LLM is available.
	"""
	personas = normalize_interaction_personas(interaction_personas)
	locs = normalize_location_personas(location_personas)
	theme_text = " ".join(str(theme or "").split()).strip() or "tema do canal"
	top = top_video or {}
	top_title = str(top.get("title") or "").strip()
	try:
		duration = int(engine_duration_seconds or 10)
	except Exception:
		duration = 10
	if duration not in (5, 10, 15):
		duration = 10

	persona_instruction = build_interaction_persona_instruction(personas)
	location_instruction = build_location_persona_instruction(locs)

	timeline_clean, llm_meta = await _generate_timeline_with_llm(
		theme=theme_text,
		duration=duration,
		personas=personas,
		locations=locs,
		top_video=top,
		channel_hint=channel_hint,
	)

	used_fallback = False
	if not timeline_clean:
		timeline_clean = _fallback_timeline(duration, theme_text)
		used_fallback = True

	timeline_text = _format_timeline_seconds(timeline_clean, duration)

	hook_summary = llm_meta.get("hook_summary") or "Gatilho cinematografico nos primeiros segundos para prender o espectador."
	scene_description = llm_meta.get("scene_description") or ""

	# Final cinematic prompt (per-second timeline)
	prompt_parts = [
		f"Short cinematografico vertical 9:16 de {duration}s sobre: {theme_text}.",
		f"GANCHO INICIAL: {hook_summary}",
	]
	if scene_description:
		prompt_parts.append(f"CENA: {scene_description}")
	prompt_parts.append("MARCACAO SEGUNDO A SEGUNDO:")
	prompt_parts.append(timeline_text)
	if persona_instruction:
		prompt_parts.append(persona_instruction)
	if location_instruction:
		prompt_parts.append(location_instruction)
	prompt_parts.append(
		"Liberdade criativa total: hibridos, criaturas surreais e mundos fantasticos sao bem-vindos quando servirem ao impacto. "
		"Sem texto na tela, sem legenda embutida."
	)
	rendered = "\n".join(prompt_parts)

	image_prompt = llm_meta.get("image_prompt") or (
		f"Frame inicial cinematografico vertical 9:16 para short de {duration}s sobre: {theme_text}. "
		f"{hook_summary} Sem texto na tela."
	)

	summary = [
		f"Tema base: {theme_text}.",
		f"Motor selecionado: {engine_id} ({duration}s, formato 9:16).",
	]
	if top_title:
		views = int(top.get("views") or 0)
		summary.append(f"Inspirado no top 1 do canal: '{top_title}' ({views:,} views).".replace(",", "."))
	if personas:
		summary.append(f"Persona: {summarize_interaction_personas(personas)}.")
	if locs:
		labels = [_LOCATION_PERSONA_LABELS.get(loc, loc) for loc in locs]
		summary.append("Local: " + ", ".join(labels) + ".")
	summary.append(f"Cena planejada segundo a segundo ({len(timeline_clean)} marcacoes).")
	summary.append(f"Gancho inicial: {hook_summary}")
	if used_fallback:
		summary.append("Plano gerado em modo deterministico (LLM indisponivel). Voce pode reprovar e o piloto tenta novamente no proximo ciclo.")
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
		"engine_duration_seconds": duration,
		"aspect_ratio": "9:16",
		"timeline": timeline_clean,
		"timeline_text": timeline_text,
		"hook_summary": hook_summary,
		"scene_description": scene_description,
		"llm_used": not used_fallback,
	}

	return {
		"preview_prompt": rendered,
		"image_prompt": image_prompt,
		"decision_summary": summary,
		"plan": plan,
	}
