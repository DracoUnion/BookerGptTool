import json
from typing import List
from pydantic import parse_obj_as
from .util import ask_chatgpt_retry, ext_cont_block, ext_code_block
from .trans_epub_pmt import (
    TRANS_TITLE_PMT, FMT_PMT, TRANS_BODY_PMT, TOC_PMT, TOC_EXT_PMT,
)
from .trans_epub_models import TocExtResult


class EpubTranslatorAgent:
    def __init__(self, args):
        self.args = args

    def translate_title(self, text: str) -> str:
        ques = TRANS_TITLE_PMT.replace('{text}', text)
        return ask_chatgpt_retry(ques, self.args.model, self.args)

    def format_text(self, text: str) -> str:
        ques = FMT_PMT.replace('{text}', text)
        return ask_chatgpt_retry(
            ques, self.args.model, self.args,
            parse_output=ext_cont_block,
        )

    def translate_body(self, text: str) -> str:
        ques = TRANS_BODY_PMT.replace('{text}', text)
        return ask_chatgpt_retry(
            ques, self.args.model, self.args,
            parse_output=ext_cont_block,
        )

    def fix_toc(self, text: str) -> str:
        ques = TOC_PMT.replace('{text}', text)
        return ask_chatgpt_retry(ques, self.args.model, self.args)

    def extract_chapter_toc(self, titles: list) -> List[TocExtResult]:
        ques = TOC_EXT_PMT.replace('{titles}', json.dumps(titles, ensure_ascii=False))
        parse_output = lambda s: parse_obj_as(
            List[TocExtResult],
            json.loads(ext_code_block(s)),
        )
        return ask_chatgpt_retry(
            ques, self.args.model, self.args,
            parse_output=parse_output,
        )
