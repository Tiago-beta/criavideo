from app.services.pilot_prompt import (
    build_interaction_persona_instruction,
    build_pilot_prompt_preview,
    normalize_interaction_personas,
    summarize_interaction_personas,
)
from app.tasks.auto_creation_tasks import _normalize_persona_composition_candidates


def test_normalize_interaction_personas_deduplicates_and_normalizes_aliases():
    personas = normalize_interaction_personas(["Natureza", "criança", "natureza", "custom"])

    assert personas == ["natureza", "crianca", "personalizado"]


def test_build_interaction_persona_instruction_for_multiple_personas_forbids_fusion():
    instruction = build_interaction_persona_instruction(["natureza", "mulher"])

    assert "PERSONAS DE INTERACAO" in instruction
    assert "natureza viva + mulher" in instruction
    assert "Nao transformar uma persona na outra" in instruction
    assert "nunca como extensao do corpo/rosto da persona humana" in instruction


def test_build_pilot_prompt_preview_reports_persona_summary_for_composition():
    preview = build_pilot_prompt_preview(["natureza", "homem"], candidate_count=2)

    assert preview["personas"] == ["natureza", "homem"]
    assert summarize_interaction_personas(preview["personas"]) == "natureza viva + homem"
    assert "natureza viva + homem" in preview["persona_summary"]
    assert "entidade separada" in " ".join(preview["decision_summary"])
    assert "PERSONAS DE INTERACAO" in preview["preview_prompt"]


def test_normalize_persona_composition_candidates_keeps_multiple_types():
    candidates = _normalize_persona_composition_candidates(
        [
            {"persona_type": "natureza", "persona_profile_ids": [11, 12]},
            {"persona_type": "mulher", "persona_profile_id": 22},
        ]
    )

    assert candidates == [
        {
            "persona_type": "natureza",
            "persona_profile_id": 11,
            "persona_profile_ids": [11, 12],
            "disable_persona_reference": False,
        },
        {
            "persona_type": "mulher",
            "persona_profile_id": 22,
            "persona_profile_ids": [22],
            "disable_persona_reference": False,
        },
    ]