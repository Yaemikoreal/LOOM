"""深层变异系统数据模型 - 四维结构性变异。

四个正交变异维度（ADR 0005）：
- 叙事结构 (NARRATIVE_STRUCTURE): 三幕剧、倒叙、网状叙事等
- 视点与声音 (POV_VOICE): 第一人称、第三人称有限、全知视角等
- 因果与时间线 (CAUSAL_TIMELINE): 线性、非线性、碎片化等
- 弧光与主题 (ARC_THEME): 英雄之旅、悲剧、救赎等

策略引擎每次选择 1-2 个维度进行变异，避免组合爆炸。
"""

from enum import Enum

from pydantic import BaseModel, Field


class MutationDimension(str, Enum):
    """变异维度枚举，定义四种正交的叙事变异方向。"""

    NARRATIVE_STRUCTURE = "narrative_structure"
    POV_VOICE = "pov_voice"
    CAUSAL_TIMELINE = "causal_timeline"
    ARC_THEME = "arc_theme"


class StructuralTemplate(BaseModel):
    """结构模板，定义一种具体的叙事结构变体。

    每个模板包含名称、维度、描述和 Writer Prompt 注入指令。
    """

    template_id: str = Field(description="模板唯一标识，如 three_act_nonlinear")
    dimension: MutationDimension = Field(description="所属变异维度")
    name: str = Field(description="模板名称，如 '非线性三幕剧'")
    description: str = Field(description="模板描述")
    prompt_hint: str = Field(
        description="注入 Writer Prompt 的变异指令，指导 LLM 按此结构创作"
    )


class MutationPlan(BaseModel):
    """变异计划，由策略引擎生成。

    包含选定的维度、模板和变异强度。
    """

    dimensions: list[MutationDimension] = Field(
        description="选定的变异维度（1-2 个）"
    )
    templates: list[StructuralTemplate] = Field(
        description="对应的结构模板"
    )
    intensity: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="变异强度 0.0-1.0，越高越大胆",
    )
    rationale: str = Field(
        default="",
        description="选择此变异方案的理由",
    )


# ── 预定义结构模板库 ────────────────────────────────────────────────

TEMPLATES: dict[str, StructuralTemplate] = {
    # 叙事结构维度
    "three_act_classic": StructuralTemplate(
        template_id="three_act_classic",
        dimension=MutationDimension.NARRATIVE_STRUCTURE,
        name="经典三幕剧",
        description="设置→对抗→解决的经典叙事弧线",
        prompt_hint="请按照经典三幕剧结构创作：第一幕建立冲突，第二幕升级对抗，第三幕解决。",
    ),
    "reverse_chronology": StructuralTemplate(
        template_id="reverse_chronology",
        dimension=MutationDimension.NARRATIVE_STRUCTURE,
        name="倒叙",
        description="从结局开始，逐步回溯揭示真相",
        prompt_hint="请使用倒叙结构：从故事的结局或高潮开始，通过回忆和闪回逐步揭示导致这个结局的原因。",
    ),
    "parallel_storylines": StructuralTemplate(
        template_id="parallel_storylines",
        dimension=MutationDimension.NARRATIVE_STRUCTURE,
        name="平行叙事",
        description="多条故事线交替推进，最终交汇",
        prompt_hint=(
            "请使用平行叙事结构：同时推进 2-3 条故事线，"
            "交替切换视角，让它们在关键时刻交汇。"
        ),
    ),
    "frame_narrative": StructuralTemplate(
        template_id="frame_narrative",
        dimension=MutationDimension.NARRATIVE_STRUCTURE,
        name="框架叙事",
        description="故事中的故事，多层嵌套",
        prompt_hint="请使用框架叙事结构：在主叙事中嵌入一个或多个子故事，子故事与主线相互映照。",
    ),
    # 视点与声音维度
    "first_person_intimate": StructuralTemplate(
        template_id="first_person_intimate",
        dimension=MutationDimension.POV_VOICE,
        name="亲密第一人称",
        description="深度沉浸的角色内心独白",
        prompt_hint="请使用第一人称亲密视角：通过角色的眼睛和内心独白叙述，让读者直接体验角色的思想和情感。",
    ),
    "third_limited_shifting": StructuralTemplate(
        template_id="third_limited_shifting",
        dimension=MutationDimension.POV_VOICE,
        name="切换式第三人称有限",
        description="在多个角色的有限视角间切换",
        prompt_hint="请使用切换式第三人称有限视角：在不同角色的视角间切换，每个场景只展示当前视角角色的感知和想法。",
    ),
    "unreliable_narrator": StructuralTemplate(
        template_id="unreliable_narrator",
        dimension=MutationDimension.POV_VOICE,
        name="不可靠叙述者",
        description="叙述者的偏见或缺失让真相逐渐浮现",
        prompt_hint="请使用不可靠叙述者技巧：叙述者的描述带有个人偏见、记忆缺失或刻意隐瞒，让读者通过线索推断真相。",
    ),
    # 因果与时间线维度
    "nonlinear_mosaic": StructuralTemplate(
        template_id="nonlinear_mosaic",
        dimension=MutationDimension.CAUSAL_TIMELINE,
        name="非线性拼贴",
        description="打乱时间线，通过片段拼贴揭示全貌",
        prompt_hint="请使用非线性拼贴结构：打乱事件的时间顺序，通过看似随机的片段组合，让读者自行拼凑出完整的故事。",
    ),
    "real_time_immersion": StructuralTemplate(
        template_id="real_time_immersion",
        dimension=MutationDimension.CAUSAL_TIMELINE,
        name="实时沉浸",
        description="以接近实时的速度叙述，放大细节",
        prompt_hint="请使用实时沉浸技巧：大幅放慢叙事速度，用大量细节描写短暂的时间段，让读者仿佛身临其境。",
    ),
    # 弧光与主题维度
    "tragedy_descent": StructuralTemplate(
        template_id="tragedy_descent",
        dimension=MutationDimension.ARC_THEME,
        name="悲剧下沉",
        description="角色从高位逐渐走向毁灭",
        prompt_hint="请使用悲剧下沉弧线：角色因性格缺陷（hamartia）逐渐走向不可逆转的毁灭，每个选择都让结局更加确定。",
    ),
    "redemption_arc": StructuralTemplate(
        template_id="redemption_arc",
        dimension=MutationDimension.ARC_THEME,
        name="救赎弧线",
        description="角色从错误中觉醒，寻求救赎",
        prompt_hint="请使用救赎弧线：角色经历深刻的自我认知，面对过去的错误，并通过牺牲或改变寻求救赎。",
    ),
    "growth_bildungsroman": StructuralTemplate(
        template_id="growth_bildungsroman",
        dimension=MutationDimension.ARC_THEME,
        name="成长小说",
        description="角色从天真走向成熟的成长历程",
        prompt_hint="请使用成长小说弧线：展示角色从天真/无知到成熟/智慧的转变过程，通过考验和学习获得成长。",
    ),
}


def get_templates_by_dimension(dimension: MutationDimension) -> list[StructuralTemplate]:
    """获取指定维度的所有结构模板。"""
    return [t for t in TEMPLATES.values() if t.dimension == dimension]


def get_template(template_id: str) -> StructuralTemplate | None:
    """根据模板 ID 获取结构模板。"""
    return TEMPLATES.get(template_id)
