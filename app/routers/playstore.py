from fastapi import APIRouter

router = APIRouter(prefix="/api/playstore", tags=["playstore"])

PLAYSTORE_PREVIEW_EXAMPLES = [
    {
        "id": "plant-care",
        "title": "Lirio: antes, cuidado e resultado",
        "description": "Exemplo vertical com abertura forte, prova visual e CTA curto para gerar inspiracao sem expirar.",
        "badge": "Nao expira",
        "format": "9:16 · 18s",
        "gradient": "linear-gradient(135deg, #7b5a37 0%, #355f7a 55%, #0d1622 100%)",
        "tags": ["antes e depois", "plantas", "caseiro"],
        "prompt": "Mostre o vaso seco, corte para folhas novas e feche com enquadramento limpo da planta recuperada.",
        "structure": [
            "Gancho visual imediato",
            "Transformacao em 3 passos",
            "Fechamento com CTA leve",
        ],
    },
    {
        "id": "beauty-demo",
        "title": "Produto em maos + prova rapida",
        "description": "Layout pensado para pequenos negocios com foco em demonstracao de produto e narrativa curta.",
        "badge": "Curadoria",
        "format": "9:16 · 22s",
        "gradient": "linear-gradient(135deg, #6d385d 0%, #dc8ea2 48%, #141d2f 100%)",
        "tags": ["produto", "social proof", "vendas"],
        "prompt": "Abra com rosto e produto no mesmo frame, entre com beneficio central e finalize com prova de uso.",
        "structure": [
            "Rosto no primeiro segundo",
            "Beneficio em texto curto",
            "Oferta final",
        ],
    },
    {
        "id": "real-estate",
        "title": "Tour rapido de ambiente",
        "description": "Modelo para video de espaco ou servico local, com cortes fluidos e legenda curta.",
        "badge": "Favorito do time",
        "format": "16:9 · 25s",
        "gradient": "linear-gradient(135deg, #104868 0%, #7ec2d8 46%, #132438 100%)",
        "tags": ["tour", "imovel", "servico local"],
        "prompt": "Comece na entrada, marque 3 pontos fortes do ambiente e termine com chamada para contato.",
        "structure": [
            "Cena de abertura ampla",
            "3 beneficios em sequencia",
            "CTA final",
        ],
    },
]


@router.get("/examples")
async def get_playstore_examples():
    return {
        "items": PLAYSTORE_PREVIEW_EXAMPLES,
        "version": "20260518-01",
    }