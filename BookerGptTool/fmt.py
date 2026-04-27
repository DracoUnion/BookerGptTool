import re
import os
import copy
from multiprocessing import Pool
from os import path
from pyquery import PyQuery as pq

def fmt_publisher(text, mode):
    if mode in ['oreilly', 'orly']:
        text = fmt_oreilly(text)
    elif mode == 'packt':
        text = fmt_packt(text)
    elif mode == 'apress':
        text = fmt_apress(text)
    return text

def fmt_zh(text):
    text = fmt_en_zh_gap(text)
    text = fmt_uprscp(text)
    text = fmt_link(text)
    text = fmt_img(text)
    # text = fmt_chnum(text)
    # text = fmt_title_num(text)
    text = fmt_en_zh_gap(text)
    return text
    
num_uprscp_map = {
    '1': '¹',
    '2': '²',
    '3': '³',
    '4': '⁴',
    '5': '⁵',
    '6': '⁶',
    '7': '⁷',
    '8': '⁸',
    '9': '⁹',
    '0': '⁰',
}

def num2uprscp(text):
    return ''.join([
        num_uprscp_map.get(ch, ch)
        for ch in text
    ])
    
def fmt_uprscp(text):
    text =  re.sub(
        r'\^\((\d+)\)', 
        lambda g: num2uprscp(g.group(1)), 
        text
    )
    text = re.sub(
        r'\^(\d+)',
        lambda g: num2uprscp(g.group(1)), 
        text
    )
    return text

def fmt_en_zh_gap(text):
    text = re.sub(r'([\u4e00-\u9fff])([a-zA-Z0-9_])', r'\1 \2', text)
    text = re.sub(r'([a-zA-Z0-9_])([\u4e00-\u9fff])', r'\1 \2', text)
    return text

def fmt_link(text):
    RE_LINK = r'''
        \[
            https?://
            ([^\]]+)
        \]
        \(([^\)]+)\)
    '''
    text = re.sub(RE_LINK, r'[`\1`](\2)', text, flags=re.VERBOSE)
    RE_INNER_LINK = r'''
        (?<!!)
        \[([^\]]+)\]
        \(
            (?!https?://)
            [^\)]+
        \)
    '''
    text = re.sub(RE_INNER_LINK, r'\1', text, flags=re.VERBOSE)
    return text

def fmt_img(text):
    RE_INNER_IMG = r'''
        !\[([^\]]*)\]
        \(
            (?!https?://)
            ([^\)]+)
        \)
    '''

    def repl_img_func(g):
        desc, src = g.group(1), g.group(2)
        desc = desc.replace('\r', '').replace('\n', '')
        src = "img/" + path.basename(src)
        return f'![{desc}]({src})'

    return re.sub(RE_INNER_IMG, repl_img_func, text, flags=re.VERBOSE)

def fmt_oreilly(html):
    html = html.replace('&#13;', '')
    html = re.sub(r'<code[^>]*>(\s*)</code>', r'\1', html)
    return html

def fmt_packt(html):
    RE_UNUSED_TAG = r'</?(article|section|span|header|link)[^>]*>'
    RE_DIV_START = r'<div[^>]*>\s*'
    RE_DIV_CONT = r'<div>([^<][\s\S]+?)</div>'
    RE_REPL_P = r'<p>\1</p>'
    RE_P_SNIPPLET = r'<p [^>]*class=".*?\bsnippet\b.*?"[^>]*>(.+?)</p>'
    RE_P_SRC_CODE = r'<p [^>]*class=".*?\bsource-code\b.*?"[^>]*>(.+?)</p>'
    RE_REPL_PRE = r'<pre>\1</pre>'
    RE_REPL_CODE = r'<code>\1</code>'
    RE_STRONG_INLINE = r'<strong [^>]*class=".*?\binline-code\b.*?"[^>]*>(.+?)</strong>'
    RE_STRONG_CODE = r'<strong [^>]*class=".*?\binline\b.*?"[^>]*>(.+?)</strong>'
    RE_PRE_NL = r'</pre>\s*<pre>'
    RE_SRC = r'src="(.+?)"'
    # 去除无用标签
    html = re.sub(RE_UNUSED_TAG, '', html)
    # DIV 内容
    html = re.sub(RE_DIV_START, '<div>', html)
    html = re.sub(RE_DIV_CONT, RE_REPL_P, html)
    # 代码段
    html = re.sub(RE_P_SRC_CODE, RE_REPL_PRE, html)
    html = re.sub(RE_P_SNIPPLET, RE_REPL_PRE, html)
    html = re.sub(RE_STRONG_INLINE, RE_REPL_CODE, html)
    html = re.sub(RE_STRONG_CODE, RE_REPL_CODE, html)
    html = re.sub(RE_PRE_NL, '\n', html)
    # 图像
    def img_src_repl(m):
        src = m.group(1)
        fname = path.basename(src)
        return f'src="img/{fname}"'
    html = re.sub(RE_SRC, img_src_repl, html)
    return html
    
def process_apress_pre(el_pre, root):
    el_lines = el_pre.find('.FixedLine')
    lines = []
    for i in range(len(el_lines)):
        el_line = el_lines.eq(i)
        lines.append(el_line.text())
    el_new_pre = root('<pre></pre>')
    code = re.sub(r'<[^>]*>', '', '\n'.join(lines))
    code = re.sub(r'^\x20+', '', code, flags= re.M)
    el_new_pre.text(code)
    el_pre.replace_with(el_new_pre)

def process_apress_para(el_para, root):
    el_new_para = root('<p></p>')
    el_new_para.html(el_para.html())
    el_para.replace_with(el_new_para)

def fmt_apress(html):
    html = rm_xml_header(html)
    root = pq(html)
    
    el_pres = root('.ProgramCode')
    for i in range(len(el_pres)):
        el_pre = el_pres.eq(i)
        el_new_pre = root('<pre></pre>')
        code = re.sub(r'<[^>]*>', '', el_pre.text())
        code = re.sub(r'^\x20+', '', code, flags=re.M)
        code = code.replace('\xa0', '\x20')
        el_new_pre.text(code)
        el_pre.replace_with(el_new_pre)
    
    el_codes = root('.EmphasisFontCategoryNonProportional, .FontName2, .FontName1')
    for i in range(len(el_codes)):
        el_code = el_codes.eq(i)
        el_new_code = root('<code></code>')
        el_new_code.text(el_code.text())
        el_code.replace_with(el_new_code)
        
    el_paras = root('div.Para')
    print(len(el_paras))
    for i in range(len(el_paras)):
        process_apress_para(el_paras.eq(i), root)
        
    el_lis = root('.UnorderedList, .OrderedList, pre, .Figure, .Table')
    print(len(el_lis))
    for i in range(len(el_lis)):
        el_li = el_lis.eq(i)
        el_li_parent = el_li.parent()
        if not el_li_parent.is_('p, div.Para'):
            continue
        el_li.remove()
        el_li_parent.after(el_li)
        
    el_paras = root('.CaptionNumber, .MediaObject')
    print(len(el_paras))
    for i in range(len(el_paras)):
        process_apress_para(el_paras.eq(i), root)
    
    root('.ChapterContextInformation, .AuthorGroup, .ItemNumber').remove()
    
    html = str(root)
    html = re.sub(r'</?(div|span|article|header|section|figure|figcaption)[^>]*>', '', html)
    return html
