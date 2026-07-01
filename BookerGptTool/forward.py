from flask import Flask, request as req, jsonify, Response
import traceback
import yaml
import requests
import random
from os import path

def forward(args):
    keys = yaml.safe_load(
        open(args.fname, encoding='utf8').read())

    app = Flask(__name__)

    @app.post('/v1/chat/completions')
    def oai_api_forward():
        hdrs, data = dict(req.headers), req.get_json()
        key = random.choice(keys)
        hdrs['Authorization'] = f'Bearer {key["api_key"]}'
        hdrs.pop('Host')
        data['model'] = key['model']
        # KIMI 模型只能接受温度 0.6
        if 'api.kimi.com/coding' in key['base_url']:
            data['temperature'] = 0.6
        stream = data.get('stream', False)
        url = key['base_url'] + '/chat/completions'
        r = requests.post(
            url,
            json=data,
            headers=hdrs,
            stream=stream,
            timeout=(args.conn_timeout, args.read_timeout),
        )
        if not stream:
            return jsonify(r.json()), r.status_code
        
        # 流式：使用生成器转发 SSE 事件
        def generate():
            # 逐行读取 OpenAI 的流式响应
            for line in r.iter_lines(decode_unicode=False):
                if line:  # 跳过空行
                    # OpenAI 的 SSE 格式为 "data: {...}"，每块后有两个换行
                    yield line.decode('utf-8') + "\n\n"

        return Response(generate(), mimetype="text/event-stream")

    @app.errorhandler(Exception)
    def handle_ex(e):
        """
        捕获所有未在视图层处理的异常，返回 OpenAI 风格的错误 JSON。
        """
        # 针对特定异常可定制，这里统一返回 500
        traceback.print_exc()
        return jsonify({
            "error": {
                "message": f"Internal server error: {str(e)}",
                "type": "server_error",
                "param": None,
                "code": None
            }
        }), 500

    app.run(args.listen_host, args.listen_port, args.debug)