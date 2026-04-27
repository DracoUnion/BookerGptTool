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
