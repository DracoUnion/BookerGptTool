var toMD = require('to-markdown');
var fs = require('fs');
var path = require('path')

var isDir = s => fs.statSync(s).isDirectory()

function main() {
    
    var fname = process.argv[2]
    if(!fname) {
        console.log('请指定文件或目录。')
        process.exit();
    }
    
    if(isDir(fname))
        processDir(fname)
    else
        processFile(fname)
}

function processDir(dir) {
    var files = fs.readdirSync(dir)
    for(var fname of files) {
        fname = path.join(dir, fname)
        processFile(fname)
    }
}

function tomdJson(html) {
    return JSON.stringify(tomd(JSON.parse(html)))
}

function tomd(html) {
    return toMD(html, {
        gfm: true,
        converters: myConventors,
    });
}

function processFile(fname) {
    if(!fname.endsWith('.html')) 
        return;
    console.log(`file: ${fname}`)
    var co = fs.readFileSync(fname, 'utf-8')
               .replace(/<a[^>]*\/>/g, '');
    var mdName = fname.replace(/\.html/g, '.md')
    var md = tomd(co)
    fs.writeFileSync(mdName, md);
}

// my conventors for https://github.com/domchristie/to-markdown

function cell(content, node) {
  var index = Array.prototype.indexOf.call(node.parentNode.childNodes, node);
  var prefix = ' ';
  if (index === 0) { prefix = '| '; }
  return prefix + content + ' |';
}

var myConventors = [

  //<math>
  {
    filter: ['math'],
    replacement: function (c, n) {
	  var tex = n.getAttribute('alttext')
	  return (tex? '$' + tex.trim() + '$': c)
    }
  },

  // 内联代码
  {
    filter: ['code', 'tt', 'var', 'kbd'],
    replacement: function (content, node) {
      if(node.parentNode.nodeName === 'PRE')
        return content
      // 如果内容为空或者空白， 返回空串
      if (!content) return ''
      // 去掉所有换行符
      content = content.replace(/[\r\n]/g, ' ')
      // 如果以反引号开头或者结尾，需要加填充避免与分隔符连上
      var extraSpace = content.startsWith('`') || 
          content.endsWith('`')? ' ': ''
      var ms = content.match(/`+/gm) || []
      var maxLen = Math.max(...ms.map(x => x.length), 0) + 1
      var delimiter = '`'.repeat(maxLen)
      // 拼接到一起
      return delimiter + extraSpace + content + extraSpace + delimiter
    }
  },
  //<p> in <td>
  {
    filter: function(n) {
        return n.nodeName == 'P' &&
            (n.parentNode.nodeName === 'TD' || 
             n.parentNode.nodeName === 'TH')
    },
    replacement: function(c){return c}
  },

  //<dl> (to <p>)
  {
    filter: ['dd', 'dt', 'figcaption', 'caption'],
    replacement: function (c) {
      return '\n\n' + c + '\n\n';
    }
  },
  
  {
      filter: 'dl',
      replacement: function(c){return c}
  },
  
  // <em> & <i>
  
  {
      filter: ['em', 'i'],
      replacement: function(c){return '*' + c + '*'}
  },
  
  //<span> & <div>
  
  {
    filter: ['span', 'div', 'article', 'section', 'header', 'footer', 'figure', 'nav', 'u', 'center', 'small', 'cite', 'mark', 'font', 'big', 'time', 'address', 'abbr', 'object'],
    replacement: function(c){return c}
  },
  
  //sth to clean
  {
    filter: ['style', 'base', 'meta', 'script', 'ins', 'aside', 'noscript', 'form', 'label', 'input', 'button', 'col', 'colgroup'],
    replacement: function(){return ''}
  },
  
  //non-link <a>
  {
    filter: function (n) {
      return n.nodeName === 'A' && !n.getAttribute('href');
    },
    replacement: function(c){return c}
  },
  
  // <pre>
  {
    filter: ['pre', 'textarea'],
    replacement: function(c) {
        return '\n\n```\n' + c + '\n```\n\n';
    }
  },
  
  // tags in <pre>
  {
    filter: function(n) {
        return n.parentNode.nodeName === 'PRE' &&
            n.nodeName != 'BR'
    },
    replacement: function(c){return c}
  },
  
  //<th> & <td>
  {
    filter: ['th', 'td'],
    replacement: function (c, n) {
      return cell(c.replace(/\|/g, '&#124;').replace('\n', ' '), n);
    }
  },
  
  // iframe, video, source
  {
    filter: ['iframe', 'video', 'audio', 'source'],
    replacement: function (c, n) {
      var src = n.getAttribute('src')
      return (src? '\n\n<' + src + '>\n\n': '') + c
    }
      
  },
  
  //<sub>
  {
    filter: ['sub'],
    replacement: function (c, n) {
      return '[' + c + ']'
    }
  },
  
  //<sup>
  {
    filter: ['sup'],
    replacement: function (c, n) {
	  var sup = '⁰¹²³⁴⁵⁶⁷⁸⁹'.split('')
	  if(sup[c]) return sup[c]
	  else if(c.length == 1) return '^' + c
	  else return '^(' + c + ')'
    }
  },

];

if(module == require.main) main()
