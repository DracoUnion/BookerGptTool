import pyturndown
import re

# 规则字典
RULES = {}


def register_rule(name, filter_condition, replacement_func):
    """
    注册一个转换规则。

    Args:
        name: 规则名称
        filter_condition: 匹配条件（字符串、列表或函数）
        replacement_func: 转换函数
    """
    RULES[name] = {
        'filter': filter_condition,
        'replacement': replacement_func
    }


# 上标字符映射表（0-9）
SUPERSCRIPTS = list('⁰¹²³⁴⁵⁶⁷⁸⁹')

# ---------- filter 函数（均为独立 def） ----------
def filter_math(node):
    return node.tag == 'math'


def filter_p_in_td(node):
    return (node.tag == 'p' and
            node.getparent() is not None and
            node.getparent().tag in ('td', 'th'))

def filter_dd_dt(node):
    return node.tag in ('dd', 'dt', 'figcaption', 'caption')

def filter_dl(node):
    return node.tag == 'dl'


def filter_span_div(node):
    return node.tag in ('span', 'div', 'article', 'section', 'header', 'footer',
                        'figure', 'nav', 'u', 'center', 'small', 'cite', 'mark',
                        'font', 'big', 'time', 'address', 'abbr', 'object')

def filter_clean(node):
    return node.tag in ('style', 'base', 'meta', 'script', 'ins', 'aside',
                        'noscript', 'form', 'label', 'input', 'button',
                        'col', 'colgroup')

def filter_a_no_href(node):
    return node.tag == 'a' and not node.get('href')

def filter_single_pre(node):
    print('filter_single_pre')
    if node.tag not in ('pre', 'textarea'):
        return False
    children = node.getchilren()
    has_code = len(children) == 1 and children[0].tag == 'code'
    return not has_code

def filter_in_pre(node):
    parent = node.getparent()
    return parent is not None and parent.tag == 'pre' and node.tag != 'br'

def filter_media(node):
    return node.tag in ('iframe', 'video', 'audio', 'source')

def filter_sub(node):
    return node.tag == 'sub'

def filter_sup(node):
    return node.tag == 'sup'

# ---------- replacement 函数（均为独立 def） ----------
def repl_math(content, node):
    tex = node.get('alttext')
    if tex:
        return '$' + tex.strip() + '$'
    return content

def repl_p_in_td(content, node):
    return content

def repl_dd_dt(content, node):
    return '\n\n' + content + '\n\n'

def repl_dl(content, node):
    return content

def repl_span_div(content, node):
    return content

def repl_clean(content, node):
    return ''

def repl_a_no_href(content, node):
    return content

def repl_single_pre(content, node):
    # 注意：此规则标记为 leaf，因此 content 实际上是 node.text_content()
    return '\n\n```\n' + content + '\n```\n\n'

def repl_in_pre(content, node):
    return content

def repl_media(content, node):
    src = node.get('src')
    prefix = '\n\n<' + src + '>\n\n' if src else ''
    return prefix + content

def repl_sub(content, node):
    return '[' + content + ']'

def repl_sup(content, node):
    # 如果内容为单个数字（0-9），返回上标字符
    if content in SUPERSCRIPTS:  # 直接匹配字符
        return content
    # 如果长度1，返回 ^x
    if len(content) == 1:
        return '^' + content
    # 否则返回 ^(xxx)
    return '^(' + content + ')'

register_rule('a_no_href', filter_a_no_href, repl_a_no_href)
register_rule('clean', filter_clean, repl_clean)
register_rule('dd_dt', filter_dd_dt, repl_dd_dt)
register_rule('dl', filter_dl, repl_dl)
register_rule('in_pre', filter_in_pre, repl_in_pre)
register_rule('math', filter_math, repl_math)
register_rule('media', filter_media, repl_media)
register_rule('p_in_td', filter_p_in_td, repl_p_in_td)
register_rule('single_pre', filter_single_pre, repl_single_pre)
register_rule('span_div', filter_span_div, repl_span_div)
register_rule('sub', filter_sub, repl_sub)
register_rule('sup', filter_sup, repl_sup)

# 导出规则字典
def get_rules():
    """
    获取所有 GFM 规则的字典。

    Returns:
        dict: 规则名称到规则字典的映射
    """
    return RULES.copy()

def tomd(html, lang=None):
    # 处理 IFRAME
    RE_IFRAME = r'<iframe[^>]*src="(.+?)"[^>]*>'
    RE_IFRAME_ALL = r'</?iframe[^>]*>'
    RE_IFRAME_REPL = r'<br/><br/><a href="\1">\1</a><br/><br/>'
    html = re.sub(RE_IFRAME, RE_IFRAME_REPL, html)
    html = re.sub(RE_IFRAME_ALL, '', html)
    tds = pyturndown.TurndownService()
    for k, r in get_rules().items():
        tds.add_rule(k, r)
    md = tds.turndown(html)
    if lang:
        md = re.sub(r'```([\s\S]+?```)', '```' + lang + r'\1', md)
    return md
