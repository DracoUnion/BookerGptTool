import os
import uuid
from os import path

from .util import ask_chatgpt_retry, set_openai_props
from .gts_fiction_pmt import (
    SETTING_PMT, ROLE_PMT, OUTLINE_PMT,
    DETAIL_PMT, BODY_PMT, POLISH_PMT,
)


class GtsFictionAgent:
    """封装所有 LLM 调用，一个方法对应一次调用。"""

    def __init__(self, model, args):
        self.model = model
        self.args = args

    def _call(self, prompt):
        return ask_chatgpt_retry(prompt, self.model, self.args)

    def generate_setting(self, idea, write_command):
        prompt = SETTING_PMT.replace('{idea}', idea) \
            .replace('{command}', write_command)
        return self._call(prompt)

    def generate_roles(self, setting, write_command):
        prompt = ROLE_PMT.replace('{setting}', setting) \
            .replace('{command}', write_command)
        return self._call(prompt)

    def generate_outline(self, setting, roles, nchapters, write_command):
        prompt = OUTLINE_PMT.replace('{setting}', setting) \
            .replace('{roles}', roles) \
            .replace('{nchapters}', str(nchapters)) \
            .replace('{command}', write_command)
        return self._call(prompt)

    def generate_detail(self, setting, roles, outline, i, write_command):
        prompt = DETAIL_PMT.replace('{setting}', setting) \
            .replace('{roles}', roles) \
            .replace('{outline}', outline) \
            .replace('{i}', str(i)) \
            .replace('{command}', write_command)
        return self._call(prompt)

    def generate_body(self, setting, roles, detail, i, write_command, nword):
        prompt = BODY_PMT.replace('{setting}', setting) \
            .replace('{roles}', roles) \
            .replace('{detail}', detail) \
            .replace('{command}', write_command) \
            .replace('{i}', str(i)) \
            .replace('{nword}', str(nword))
        return self._call(prompt)

    def polish_body(self, body, polish_command, style_example):
        prompt = POLISH_PMT.replace('{body}', body) \
            .replace('{command}', polish_command) \
            .replace('{style}', style_example)
        return self._call(prompt)


class GtsFictionOrchestrator:
    """编排整个小说生成流程：文件缓存、步骤调度。"""

    def __init__(self, args):
        self.args = args
        self.agent = GtsFictionAgent(args.model, args)
        self.out_dir = args.out_dir

    def _load_or_generate(self, fname, generator_fn):
        """缓存模式：文件存在则直接读取，否则生成并保存。"""
        if path.isfile(fname):
            return open(fname, encoding='utf8').read()
        result = generator_fn()
        open(fname, 'w', encoding='utf8').write(result)
        return result

    def _step1_setting(self):
        print(f'[1] 生成世界观设定')
        fname = path.join(self.out_dir, '世界观.md')
        return self._load_or_generate(fname, lambda:
            self.agent.generate_setting(
                self.args.idea, self.args.write_command))

    def _step2_roles(self, setting):
        print(f'[2] 生成主要角色')
        fname = path.join(self.out_dir, '角色.md')
        return self._load_or_generate(fname, lambda:
            self.agent.generate_roles(setting, self.args.write_command))

    def _step3_outline(self, setting, roles):
        print(f'[3] 生成章节大纲')
        fname = path.join(self.out_dir, '大纲.md')
        return self._load_or_generate(fname, lambda:
            self.agent.generate_outline(
                setting, roles, self.args.chapters, self.args.write_command))

    def _step4_details(self, setting, roles, outline):
        details = []
        for i in range(1, self.args.chapters + 1):
            print(f'[4] 生成第{i}章细纲')
            fname = path.join(self.out_dir, f'细纲{i}.md')
            detail = self._load_or_generate(fname, lambda i=i:
                self.agent.generate_detail(
                    setting, roles, outline, i, self.args.write_command))
            details.append(detail)
        return details

    def _step5_bodies(self, setting, roles, details):
        bodies = []
        for i in range(1, self.args.chapters + 1):
            print(f'[5] 生成第{i}章正文')
            fname = path.join(self.out_dir, f'正文{i}.md')
            body = self._load_or_generate(fname, lambda i=i, d=details[i-1]:
                self.agent.generate_body(
                    setting, roles, d, i,
                    self.args.write_command, self.args.words))
            bodies.append(body)
        return bodies

    def _step6_polish(self, bodies):
        for i in range(1, self.args.chapters + 1):
            print(f'[6] 润色第{i}章正文')
            fname = path.join(self.out_dir, f'润色正文{i}.md')
            polished = self._load_or_generate(fname, lambda i=i, b=bodies[i-1]:
                self.agent.polish_body(
                    b, self.args.polish_command, self.args.style_example))
            bodies[i - 1] = polished
        return bodies

    def run(self):
        setting = self._step1_setting()
        roles = self._step2_roles(setting)
        outline = self._step3_outline(setting, roles)
        details = self._step4_details(setting, roles, outline)
        bodies = self._step5_bodies(setting, roles, details)
        self._step6_polish(bodies)
        print('[*] 全部完成')


def write_fiction(args):
    print(args)
    set_openai_props(args)

    if args.out_dir is None:
        args.out_dir = uuid.uuid4().hex
    os.makedirs(args.out_dir, exist_ok=True)

    orchestrator = GtsFictionOrchestrator(args)
    orchestrator.run()
