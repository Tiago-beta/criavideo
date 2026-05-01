from app.routers.analyze import _build_persona_insights, _normalize_persona_composition_from_tags


def test_normalize_persona_composition_from_tags_uses_saved_composition():
    tags = {
        "persona_composition": [
            {"persona_type": "natureza", "persona_profile_ids": [10, 11]},
            {"persona_type": "mulher", "persona_profile_id": 22},
        ]
    }

    assert _normalize_persona_composition_from_tags(tags) == [
        {
            "persona_type": "natureza",
            "persona_profile_id": 10,
            "persona_profile_ids": [10, 11],
            "disable_persona_reference": False,
        },
        {
            "persona_type": "mulher",
            "persona_profile_id": 22,
            "persona_profile_ids": [22],
            "disable_persona_reference": False,
        },
    ]


def test_build_persona_insights_highlights_best_combination():
    top_videos = [
        {"id": "yt-top-1", "title": "Video vencedor", "views": 936, "likes": 107, "comments": 4},
        {"id": "yt-top-2", "title": "Segundo video", "views": 620, "likes": 59, "comments": 3},
    ]
    persona_records = [
        {
            "platform_post_id": "yt-top-1",
            "title": "Video vencedor",
            "persona_candidates": [
                {"persona_type": "natureza", "persona_profile_ids": [1], "disable_persona_reference": False},
                {"persona_type": "mulher", "persona_profile_ids": [2], "disable_persona_reference": False},
            ],
        },
        {
            "platform_post_id": "yt-top-2",
            "title": "Segundo video",
            "persona_candidates": [
                {"persona_type": "natureza", "persona_profile_ids": [3], "disable_persona_reference": False},
            ],
        },
    ]

    insights = _build_persona_insights(top_videos=top_videos, persona_records=persona_records)

    assert insights["available"] is True
    assert insights["matched_top_videos"] == 2
    assert insights["top_combinations"][0]["label"] == "natureza viva + mulher"
    assert insights["top_combinations"][0]["avg_views"] == 936
    assert "sem transformacao entre elas" in " ".join(insights["recommendations"])