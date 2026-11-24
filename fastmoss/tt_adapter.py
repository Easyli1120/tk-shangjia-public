"""Independent OpenAI-compatible Chat Completions transport for TT vision."""
import json
import re
import socket
import time
import urllib.error
import urllib.request
from openai_images import NoRedirect


class TTError(Exception):
    def __init__(self,message,status=None,retryable=False,request_id=None):
        super().__init__(message)
        self.status,self.retryable,self.request_id=status,retryable,request_id


def safe_error(message,key=''):
    value=str(message)
    if key:
        value=value.replace(key,'[REDACTED]')
    value=re.sub(r'(?i)(Bearer\s+)[^\s"\']+',r'\1[REDACTED]',value)
    value=re.sub(r'\bsk-[A-Za-z0-9_-]+','[REDACTED]',value)
    value=re.sub(r'data:image/[^\s]+','[IMAGE]',value)
    return value[:700]


class TTAdapter:
    def __init__(self,cfg,opener=None,sleep=time.sleep):
        self.cfg,self.opener,self.sleep=cfg,opener or urllib.request.build_opener(NoRedirect()),sleep

    def chat(self,messages,stream=False,json_output=True,on_delta=None,on_attempt=None):
        if not self.cfg.get('enabled',True):
            raise TTError('视觉模型已停用')
        if not self.cfg.get('api_key'):
            raise TTError('未配置 TT_API_KEY；请在 AI 模型管理填写视觉模型专用密钥')
        body={'model':self.cfg['model_id'],'messages':messages,'stream':stream,'max_tokens':6000}
        if json_output:
            body['response_format']={'type':'json_object'}
        if stream:
            body['stream_options']={'include_usage':True}
        request=urllib.request.Request(self.cfg['base_url'].rstrip('/')+'/chat/completions',data=json.dumps(body).encode(),
            headers={'Authorization':'Bearer '+self.cfg['api_key'],'Content-Type':'application/json'})
        for attempt in range(1,4):
            start=time.monotonic(); usage=None; request_id=None
            try:
                with self.opener.open(request,timeout=max(10,min(300,int(self.cfg.get('timeout',180))))) as response:
                    request_id=response.headers.get('x-request-id')
                    if stream:
                        pieces=[]; total=0; finished=False
                        while True:
                            line=response.readline(1024*1024)
                            if not line:break
                            total+=len(line)
                            if total>8*1024*1024:raise TTError('TT 流式响应超过限制')
                            if not line.startswith(b'data:'):continue
                            raw=line[5:].strip()
                            if raw==b'[DONE]':finished=True;break
                            payload=json.loads(raw)
                            if payload.get('error'):raise TTError('TT 流式错误：'+safe_error(payload['error'],self.cfg['api_key']))
                            usage=payload.get('usage') or usage
                            for choice in payload.get('choices',[]):
                                delta=choice.get('delta',{}).get('content') or ''
                                if isinstance(delta,str):
                                    pieces.append(delta)
                                    if on_delta:on_delta(delta)
                                if choice.get('finish_reason')=='stop':finished=True
                                elif choice.get('finish_reason') in ('length','content_filter'):raise TTError('TT 输出被截断或过滤，请减少图片或重试')
                        if not finished:raise TTError('TT 流式响应提前中断，请手动重试')
                        content=''.join(pieces)
                    else:
                        raw=response.read(8*1024*1024+1)
                        if len(raw)>8*1024*1024:raise TTError('TT 响应超过限制')
                        payload=json.loads(raw)
                        if payload.get('error'):raise TTError('TT 返回错误：'+safe_error(payload['error'],self.cfg['api_key']))
                        choice=payload['choices'][0]
                        if choice.get('finish_reason') not in (None,'stop'):raise TTError('TT 输出被截断或过滤，请减少图片或重试')
                        content=choice['message']['content'];usage=payload.get('usage')
                        request_id=request_id or payload.get('id')
                if not isinstance(content,str) or not content.strip():raise TTError('TT 未返回文本内容')
                if on_attempt:on_attempt({'attempt':attempt,'status':200,'request_id':request_id,'usage':usage,'outcome':'success','duration_ms':int((time.monotonic()-start)*1000)})
                return {'content':content,'usage':usage,'request_id':request_id}
            except urllib.error.HTTPError as exc:
                request_id=exc.headers.get('x-request-id')
                detail=exc.read(65536).decode('utf-8','replace')
                try:
                    parsed=json.loads(detail); detail=parsed.get('error',parsed)
                except ValueError:pass
                message='TT HTTP %d：%s'%(exc.code,safe_error(detail,self.cfg['api_key']))
                if exc.code==400:message+='；请核对 image_url data URL、图片数量、response_format 和模型视觉权限'
                error=TTError(message,exc.code,exc.code in (429,502,503,504),request_id)
                try:delay=min(30,max(1,int(exc.headers.get('Retry-After','2'))))
                except ValueError:delay=2
            except TTError as exc:
                error=exc;delay=2
            except (TimeoutError,socket.timeout,urllib.error.URLError):
                error=TTError('TT 网络超时或连接失败；结果不确定，请核对调用记录后手动重试',request_id=request_id);delay=2
            except Exception:
                error=TTError('TT 返回了无法解析的 Chat Completions 数据',request_id=request_id);delay=2
            if on_attempt:on_attempt({'attempt':attempt,'status':error.status,'request_id':error.request_id,'usage':usage,'outcome':'failed','error':str(error),'duration_ms':int((time.monotonic()-start)*1000)})
            if not error.retryable or attempt==3:raise error from None
            self.sleep(max(delay,2**attempt))
