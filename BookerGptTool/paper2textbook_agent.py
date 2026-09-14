import json_repair

from .openai import ask_chatgpt_retry, set_openai_props
from .paper2textbook_models import *
from .paper2textbook_pmt import *
from .util import ext_code_block, ext_cont_block, render_prompt


class Paper2TextbookAgent:
    """封装 paper2textbook 的独立 LLM 调用。"""

    def __init__(self, args):
        self.args = args
        self.model = args.model
        set_openai_props(args)

    @staticmethod
    def _json(parser, prompt, model, args):
        return ask_chatgpt_retry(
            prompt, model, args,
            parse_output=lambda s: parser(json_repair.loads(ext_code_block(s))),
        )

    def gen_brief(self, paper, lang, tier):
        prompt = render_prompt(BRIEF_PMT, paper=paper, lang=lang, tier=tier)
        return self._json(lambda d: BriefResult(**d), prompt, self.model, self.args)

    def gen_outline(self, paper, brief):
        prompt = render_prompt(
            OUTLINE_PMT, paper=paper, brief=brief.model_dump_json(ensure_ascii=False)
        )
        return self._json(lambda d: OutlineResult(**d), prompt, self.model, self.args)

    def gen_chapter(self, paper, brief, outline, idx, title, dialect):
        prompt = render_prompt(
            CHAPTER_PMT, paper=paper,
            brief=brief.model_dump_json(ensure_ascii=False),
            outline=outline.model_dump_json(ensure_ascii=False),
            idx=str(idx), title=title, dialect=dialect,
            lang=brief.language, tier=brief.tier,
        )
        return ask_chatgpt_retry(prompt, self.model, self.args, parse_output=ext_cont_block)

    def review_chapter(self, paper, brief, outline, idx, body):
        prompt = render_prompt(
            REVIEW_PMT, body=body,
            brief=brief.model_dump_json(ensure_ascii=False),
            outline=outline.model_dump_json(ensure_ascii=False),
        )
        return self._json(lambda d: ReviewResult(**d), prompt, self.model, self.args)

    def fix_chapter(self, paper, brief, outline, idx, body, comment, dialect):
        prompt = render_prompt(
            FIX_PMT, body=body, comment=comment,
            brief=brief.model_dump_json(ensure_ascii=False),
            outline=outline.model_dump_json(ensure_ascii=False),
            dialect=dialect,
        )
        return ask_chatgpt_retry(prompt, self.model, self.args, parse_output=ext_cont_block)

    def gen_exercises(self, paper, brief, outline, idx, title):
        prompt = render_prompt(
            EXERCISES_PMT, paper=paper,
            brief=brief.model_dump_json(ensure_ascii=False),
            outline=outline.model_dump_json(ensure_ascii=False),
            idx=str(idx), title=title,
        )
        return self._json(lambda d: ExercisesResult(**d), prompt, self.model, self.args)
